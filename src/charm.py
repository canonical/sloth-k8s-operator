#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Charmed Operator to deploy Sloth - an SLI/SLO generator."""

import logging
import socket
import typing
from pathlib import Path
from typing import Dict, List

import cosl.reconciler
import ops
import ops_tracing
import yaml
from charmlibs.interfaces.sloth import SlothRequirer
from charms.catalogue_k8s.v1.catalogue import CatalogueConsumer, CatalogueItem
from charms.certificate_transfer_interface.v1.certificate_transfer import (
    CertificateTransferRequires,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.tempo_coordinator_k8s.v0.tracing import TracingEndpointRequirer

from sloth import Sloth

logger = logging.getLogger(__name__)

# Where we store the certificate in the charm container
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"

CERTIFICATES_RELATION_NAME = "certificates"
SLOTH_CONTAINER = "sloth"


class SlothOperatorCharm(ops.CharmBase):
    """Charmed Operator to deploy Sloth - an SLI/SLO generator."""

    def __init__(self, *args):
        super().__init__(*args)
        self._fqdn = socket.getfqdn()

        # Relation endpoints
        self.certificate_transfer = CertificateTransferRequires(
            self, relationship_name="receive-ca-cert"
        )
        self.metrics_endpoint_provider = MetricsEndpointProvider(
            self,
            jobs=self._metrics_scrape_jobs,
            alert_rules_path="src/prometheus_alert_rules",
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
        self.slo_requirer = SlothRequirer(self)

        # Workloads
        self._sloth_container = self.unit.get_container(Sloth.container_name)
        self.sloth = Sloth(
            container=self._sloth_container,
            slo_period=typing.cast(str, self.config.get("slo-period", "30d")),
            slo_period_windows=typing.cast(str, self.config.get("slo-period-windows", "")),
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
        cosl.reconciler.observe_events(self, cosl.reconciler.all_events, self._on_reconcile_event)

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
        if self.charm_tracing.is_ready() and (
            endpoint := self.charm_tracing.get_endpoint("otlp_http")
        ):
            ops_tracing.set_destination(
                url=endpoint + "/v1/traces",
                ca=None,  # TLS not implemented (yet?) for Sloth
            )

        try:
            self.sloth.reconcile(self.slo_requirer.get_slos())
        except Exception as e:
            logger.error(f"Sloth reconciliation failed: {e}")

        self._reconcile_cert_transfer()
        self._reconcile_relations()

    def _reconcile_relations(self):
        # Update alert rules first, then trigger relation update
        self._update_alert_rules()
        # Trigger the metrics endpoint provider to send updated rules to Prometheus
        self.metrics_endpoint_provider.set_scrape_job_spec()

    def _update_alert_rules(self):
        """Update alert rules from generated SLO specifications."""
        if not self._sloth_container.can_connect():
            return

        if not self.unit.is_leader():
            return

        try:
            alert_rules = self.sloth.get_alert_rules()
            if alert_rules and alert_rules.get("groups"):
                # Write alert rules to file for MetricsEndpointProvider to pick up
                # Use charm_dir to ensure we write to the correct location
                alert_rules_dir = Path(self.charm_dir) / "src" / "prometheus_alert_rules"
                alert_rules_file = alert_rules_dir / "sloth_slo_rules.yaml"

                # Ensure directory exists
                alert_rules_dir.mkdir(parents=True, exist_ok=True)

                # Write the rules as YAML
                alert_rules_file.write_text(yaml.dump(alert_rules))
                logger.info(f"Updated alert rules with {len(alert_rules['groups'])} groups to {alert_rules_file}")
        except Exception as e:
            logger.error(f"Failed to update alert rules: {e}")

    def _reconcile_cert_transfer(self) -> None:
        """Update the TLS certificates for the charm container."""
        cacert_path = Path(CA_CERT_PATH)
        if certs := self.certificate_transfer.get_all_certificates():
            # TODO: Also remove the certificates that are no longer in relation data
            for index, cert in enumerate(certs):
                cacert_path.parent.mkdir(parents=True, exist_ok=True)
                cert_file = cacert_path.parent / f"certificate_transfer-{index}.cert"
                cert_file.write_text(cert)
        else:
            cacert_path.unlink(missing_ok=True)

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

        if not self._sloth_container.can_connect():
            event.add_status(
                ops.WaitingStatus("Waiting for workload container...")
            )
        else:
            self.unit.set_workload_version(self.sloth.version())

        # Check if SLO period configuration is valid
        is_valid, error_msg = self.sloth.is_config_valid()
        if not is_valid:
            # Log detailed explanation
            logger.warning(
                f"{error_msg}. Sloth only has built-in defaults for '30d' and '28d' periods. "
                "For other periods, you must provide custom slo-period-windows configuration."
            )
            event.add_status(ops.BlockedStatus(error_msg))
            return

        event.add_status(ops.ActiveStatus(""))  # TODO: Add "UI ready at x" when we have a UI

    def _on_reconcile_event(self, event: ops.EventBase):
        """Handle events that require reconciliation."""
        try:
            self.reconcile()
        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            # Don't defer - reconciliation will be retried on next event

    def _on_list_endpoints_action(self, event: ops.ActionEvent):
        """React to the list-endpoints action."""
        event.set_results({})  # TODO: Set endpoints after we have a UI


if __name__ == "__main__":  # pragma: nocover
    ops.main(SlothOperatorCharm)
