#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Sloth - an SLI/SLO generator."""

import logging
import socket
import typing
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

import ops
import ops_tracing
import yaml
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.sloth_k8s.v0.slo import SLORequirer
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer
from charms.tls_certificates_interface.v4.tls_certificates import (
    CertificateRequestAttributes,
    Mode,
    TLSCertificatesRequiresV4,
)

from models import TLSConfig
from sloth import Sloth

logger = logging.getLogger(__name__)

# where we store the certificate in the charm container
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"

CERTIFICATES_RELATION_NAME = "certificates"
SLOTH_CONTAINER = "sloth"


class SlothOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Sloth - an SLI/SLO generator."""

    def __init__(self, *args):
        super().__init__(*args)
        self._fqdn = socket.getfqdn()

        # Relation endpoints
        self.certificates = TLSCertificatesRequiresV4(
            charm=self,
            relationship_name=CERTIFICATES_RELATION_NAME,
            certificate_requests=[self._get_certificate_request_attributes()],
            mode=Mode.UNIT,
        )
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=self._metrics_scrape_jobs,
            alert_rules_path="src/prometheus_alert_rules",
            # external_url=self.http_server_url,
            refresh_event=[self.certificates.on.certificate_available],
        )

        self.grafana_dashboard_provider = GrafanaDashboardProvider(self)
        self.logging = LogForwarder(self)

        self.catalogue = CatalogueConsumer(
            self,
            item=CatalogueItem(
                "Sloth UI",
                icon="gauge",
                url=self._fqdn,
                description="""SLI/SLO generator for Prometheus. Generates alerting and recording rules.""",
            ),
        )
        self.charm_tracing = TracingEndpointRequirer(
            self, relation_name="charm-tracing", protocols=["otlp_http"]
        )

        # SLO provider/requirer for collecting SLO specs from related charms
        self.slo_requirer = SLORequirer(self, relation_name="slos")
        self.framework.observe(
            self.slo_requirer.on.slos_changed,
            self._on_slos_changed,
        )

        # Workloads
        self.sloth = Sloth(
            container=self.unit.get_container(Sloth.container_name),
            slo_period=typing.cast(str, self.config.get("slo-period", "30d")),
            tls_config=None,  # Will be updated during reconcile
            additional_slos=[],  # Will be updated during reconcile
        )

        # event handlers
        self.framework.observe(self.on.collect_unit_status, self._on_collect_unit_status)

        # keep this after the collect-status observer, but before any other event handler
        if self.is_scaled_up():
            logger.error(
                "Application has scale >1 but doesn't support scaling. "
                "Deploy a new application instead."
            )
            return

        # Observe events that should trigger reconciliation
        self.framework.observe(self.on.config_changed, self._on_reconcile_event)
        self.framework.observe(self.on.update_status, self._on_reconcile_event)
        self.framework.observe(self.on.sloth_pebble_ready, self._on_reconcile_event)

        # Observe relation events that need reconciliation
        self.framework.observe(self.on.metrics_endpoint_relation_joined, self._on_reconcile_event)
        self.framework.observe(self.on.metrics_endpoint_relation_changed, self._on_reconcile_event)

        self.framework.observe(self.on.list_endpoints_action, self._on_list_endpoints_action)

        # unconditional logic - safe to call even if containers aren't ready
        try:
            self.reconcile()
        except Exception as e:
            # During early hooks (like install), containers may not be ready yet.
            # This is expected and will be resolved once containers are available.
            logger.debug(f"Reconcile skipped during init: {e}")

    def is_scaled_up(self) -> bool:
        """Check whether we have peers."""
        peer_relation = self.model.get_relation("sloth-peers")
        if not peer_relation:
            return False
        return len(peer_relation.units) > 0

    # RECONCILERS
    def reconcile(self):
        """Unconditional logic to run regardless of the event we are processing."""
        # Update TLS config on workloads before reconciling
        tls_config = self._tls_config
        self.sloth._tls_config = tls_config

        # Update SLOs from relations
        self.sloth._additional_slos = self.slo_requirer.get_slos()

        if self.charm_tracing.is_ready() and (
            endpoint := self.charm_tracing.get_endpoint("otlp_http")
        ):
            ops_tracing.set_destination(
                url=endpoint + "/v1/traces",
                ca=tls_config.certificate.ca.raw if tls_config else None,
            )

        # Reconcile each workload independently - failures in one shouldn't block others
        try:
            self.sloth.reconcile()
        except Exception as e:
            logger.error(f"Sloth reconciliation failed: {e}")

        self._reconcile_tls_config()
        self._reconcile_relations()

    def _reconcile_relations(self):
        self.metrics_endpoint_provider.set_scrape_job_spec()
        self._update_alert_rules()

    def _update_alert_rules(self):
        """Update alert rules from generated SLO specifications."""
        if not self.sloth._container.can_connect():
            return

        if not self.unit.is_leader():
            return

        try:
            alert_rules = self.sloth.get_alert_rules()
            if alert_rules and alert_rules.get("groups"):
                # Write alert rules to file for MetricsEndpointProvider to pick up
                alert_rules_dir = Path("src/prometheus_alert_rules")
                alert_rules_file = alert_rules_dir / "sloth_slo_rules.yaml"

                # Ensure directory exists
                alert_rules_dir.mkdir(parents=True, exist_ok=True)

                # Write the rules as YAML
                alert_rules_file.write_text(yaml.dump(alert_rules))
                logger.info(f"Updated alert rules with {len(alert_rules['groups'])} groups")

                # Trigger the metrics endpoint provider to re-read the rules
                self.metrics_endpoint_provider.set_scrape_job_spec()
        except Exception as e:
            logger.error(f"Failed to update alert rules: {e}")

    def _reconcile_tls_config(self) -> None:
        """Update the TLS certificates for the charm container."""
        cacert_path = Path(CA_CERT_PATH)
        if tls_config := self._tls_config:
            cacert_path.parent.mkdir(parents=True, exist_ok=True)
            cacert_path.write_text(tls_config.certificate.ca.raw)
        else:
            cacert_path.unlink(missing_ok=True)

    # TLS CONFIG
    @property
    def _tls_config(self) -> Optional["TLSConfig"]:
        if not self.model.relations.get(CERTIFICATES_RELATION_NAME):
            return None
        cr = self._get_certificate_request_attributes()
        certificate, key = self.certificates.get_assigned_certificate(certificate_request=cr)

        if not (key and certificate):
            return None
        return TLSConfig(cr, key=key, certificate=certificate)

    @property
    def _tls_ready(self) -> bool:
        """Return True if tls is enabled and the necessary data is available."""
        return bool(self._tls_config)

    def _get_certificate_request_attributes(self) -> CertificateRequestAttributes:
        sans_dns: FrozenSet[str] = frozenset([self._fqdn])
        return CertificateRequestAttributes(
            common_name=self.app.name,
            sans_dns=sans_dns,
        )

    @property
    def _metrics_scrape_jobs(self) -> List[Dict]:
        return []

    # EVENT HANDLERS
    def _on_collect_unit_status(self, event: ops.CollectStatusEvent):
        """Set unit status depending on the state."""
        if self.is_scaled_up():
            event.add_status(
                ops.BlockedStatus(
                    "You can't scale up sloth-k8s. Deploy a new application instead."
                )
            )

        containers_not_ready = [
            workload.container_name
            for workload in {Sloth}
            if not self.unit.get_container(workload.container_name).can_connect()
        ]

        if containers_not_ready:
            event.add_status(
                ops.WaitingStatus(f"Waiting for containers: {containers_not_ready}...")
            )
        else:
            self.unit.set_workload_version(self.sloth.version)

        event.add_status(ops.ActiveStatus(""))  # TODO: Add "UI ready at x" when we have a UI

    def _on_reconcile_event(self, event: ops.EventBase):
        """Handle events that require reconciliation."""
        try:
            self.reconcile()
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            # Don't defer - reconciliation will be retried on next event

    def _on_slos_changed(self, event: ops.EventBase):
        """Handle changes to SLO relations."""
        logger.info("SLO specifications changed, triggering reconciliation")
        try:
            self.reconcile()
        except Exception as e:
            logger.error(f"Reconciliation failed after SLO change: {e}")

    def _on_list_endpoints_action(self, event: ops.ActionEvent):
        """React to the list-endpoints action."""
        event.set_results({})  # TODO: Set endpoints after we have a UI


if __name__ == "__main__":  # pragma: nocover
    ops.main(SlothOperatorCharm)
