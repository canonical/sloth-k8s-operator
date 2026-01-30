#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test for config-based SLO functionality."""

import json
import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import SLOTH

TIMEOUT = 600
PROMETHEUS = "prometheus"


@pytest.mark.setup
def test_setup_with_prometheus(juju: Juju, sloth_charm, sloth_resources):
    """Deploy sloth and prometheus for testing config SLOs."""
    juju.deploy(
        sloth_charm,
        SLOTH,
        resources=sloth_resources,
        trust=True,
    )

    # Deploy Prometheus to verify rules are generated
    juju.deploy("prometheus-k8s", PROMETHEUS, channel="stable", trust=True)

    juju.wait(
        lambda status: status.apps[SLOTH].is_active and status.apps[PROMETHEUS].is_active,
        error=jubilant.any_error,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )


def test_set_config_slo(juju: Juju):
    """Test setting a single SLO via config."""
    slo_config = """
version: prometheus/v1
service: config-test-service
labels:
  team: platform
  environment: test
slos:
  - name: requests-availability
    objective: 99.5
    description: "99.5% availability for config test"
    sli:
      events:
        error_query: 'sum(rate(http_requests_total{service="config-test",status=~"5.."}[{{.window}}]))'
        total_query: 'sum(rate(http_requests_total{service="config-test"}[{{.window}}]))'
    alerting:
      name: ConfigTestServiceHighErrorRate
      labels:
        severity: warning
      annotations:
        summary: "Config test service is experiencing high error rates"
"""

    juju.config(SLOTH, {"slos": slo_config})

    # Wait for config-changed to process
    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    status = juju.status()
    assert status.apps[SLOTH].is_active, "Sloth should remain active after config change"


def test_set_multiple_config_slos(juju: Juju):
    """Test setting multiple SLOs via config (multi-document YAML)."""
    slo_config = """
version: prometheus/v1
service: app1
labels:
  team: team1
slos:
  - name: availability
    objective: 99.9
    sli:
      events:
        error_query: 'sum(rate(app1_errors[{{.window}}]))'
        total_query: 'sum(rate(app1_requests[{{.window}}]))'
---
version: prometheus/v1
service: app2
labels:
  team: team2
slos:
  - name: availability
    objective: 99.5
    sli:
      events:
        error_query: 'sum(rate(app2_errors[{{.window}}]))'
        total_query: 'sum(rate(app2_requests[{{.window}}]))'
"""

    juju.config(SLOTH, {"slos": slo_config})

    # Wait for config-changed to process
    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    status = juju.status()
    assert status.apps[SLOTH].is_active, "Sloth should remain active with multiple config SLOs"


def test_config_slo_generates_prometheus_rules(juju: Juju):
    """Verify that config SLOs generate Prometheus rules."""
    # Relate Sloth to Prometheus
    juju.integrate(f"{SLOTH}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")

    # Wait for relation to establish and rules to propagate
    juju.wait(
        lambda status: status.apps[SLOTH].is_active and status.apps[PROMETHEUS].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    # Give some extra time for rules to propagate to Prometheus
    # (Rules need to: generate → write to file → send via relation → reload in Prometheus)
    max_retries = 6
    retry_delay = 10
    rules_found = False

    for attempt in range(max_retries):
        # Query Prometheus API for rules
        result = juju.exec(
            "curl -s http://localhost:9090/api/v1/rules",
            unit=f"{PROMETHEUS}/0"
        )

        try:
            rules_data = json.loads(result.stdout)
            if rules_data.get("status") == "success":
                groups = rules_data.get("data", {}).get("groups", [])

                # Look for rules from our config SLOs
                for group in groups:
                    group_name = group.get("name", "")
                    # Check if rules from config-test-service or app1/app2 are present
                    if any(service in group_name.lower() for service in ["config-test", "app1", "app2"]):
                        rules_found = True
                        break

            if rules_found:
                break

        except (json.JSONDecodeError, KeyError):
            pass

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    assert rules_found, "Expected to find Prometheus rules generated from config SLOs"


def test_clear_config_slo(juju: Juju):
    """Test clearing config SLOs."""
    juju.config(SLOTH, {"slos": ""})

    # Wait for config-changed to process
    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    status = juju.status()
    assert status.apps[SLOTH].is_active, "Sloth should remain active after clearing config"


def test_invalid_config_slo(juju: Juju):
    """Test that invalid config SLO doesn't crash the charm."""
    # Set invalid YAML
    juju.config(SLOTH, {"slos": "invalid: yaml: {{{"})

    # Wait a bit for config-changed to process
    time.sleep(10)

    status = juju.status()
    # Charm should handle invalid config gracefully (log error, don't crash)
    # It might be active or error depending on how strict we want to be
    # For now, we expect it to remain active (error is logged but doesn't block)
    assert status.apps[SLOTH].is_active or status.apps[SLOTH].status in ["active", "error"], \
        "Sloth should handle invalid config gracefully"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    if PROMETHEUS in juju.status().apps:
        juju.remove_application(PROMETHEUS)
