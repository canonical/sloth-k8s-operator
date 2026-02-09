#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Simple test charm that provides SLO specifications."""

import logging

import ops
from charmlibs.interfaces.sloth import SLOProvider

logger = logging.getLogger(__name__)


class SLOTestProviderCharm(ops.CharmBase):
    """Test charm that provides SLO specifications to Sloth."""

    def __init__(self, *args):
        super().__init__(*args)

        self.slo_provider = SLOProvider(self, relation_name="sloth")

        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.sloth_relation_joined, self._on_sloth_relation_joined)
        self.framework.observe(self.on.sloth_relation_changed, self._on_sloth_relation_changed)

    def _on_config_changed(self, event: ops.ConfigChangedEvent):
        """Handle config changed event."""
        self._provide_slo()
        self.unit.status = ops.ActiveStatus("Ready to provide SLOs")

    def _on_sloth_relation_joined(self, event: ops.RelationJoinedEvent):
        """Handle SLO relation joined."""
        self._provide_slo()

    def _on_sloth_relation_changed(self, event: ops.RelationChangedEvent):
        """Handle SLO relation changed."""
        self._provide_slo()

    def _provide_slo(self):
        """Provide SLO specification to Sloth."""
        service_name = self.config.get("slo-service-name", "test-service")
        objective = float(self.config.get("slo-objective", "99.9"))

        # Note: New library expects YAML strings, not dictionaries
        slo_yaml = f"""version: prometheus/v1
service: {service_name}
labels:
  team: test-team
  component: integration-test
slos:
  - name: requests-availability
    objective: {objective}
    description: "{objective}% of requests should succeed"
    sli:
      events:
        error_query: 'sum(rate(http_requests_total{{service="{service_name}",status=~"5.."}}[{{{{.window}}}}]))'
        total_query: 'sum(rate(http_requests_total{{service="{service_name}"}}[{{{{.window}}}}]))'
    alerting:
      name: {service_name.replace('-', '').title()}HighErrorRate
      labels:
        severity: critical
      annotations:
        summary: "{service_name} is experiencing high error rate"
"""

        try:
            self.slo_provider.provide_slos(slo_yaml)
            logger.info(f"Provided SLO for service '{service_name}' with {objective}% objective")
        except Exception as e:
            logger.error(f"Failed to provide SLO: {e}")


if __name__ == "__main__":
    ops.main(SLOTestProviderCharm)
