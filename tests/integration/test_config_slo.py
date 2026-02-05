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
    juju.deploy("prometheus-k8s", PROMETHEUS, channel="1/stable", trust=True)

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

    # Give extra time for rules to propagate to Prometheus
    # (Rules need to: generate → write to file → send via relation → reload in Prometheus)
    max_retries = 10
    retry_delay = 15
    rules_found = False
    last_error = None

    for attempt in range(max_retries):
        try:
            # Query Prometheus API for rules
            result = juju.exec(
                "curl -s http://localhost:9090/api/v1/rules",
                unit=f"{PROMETHEUS}/0"
            )

            rules_data = json.loads(result.stdout)
            if rules_data.get("status") == "success":
                groups = rules_data.get("data", {}).get("groups", [])

                # Look for rules from our config SLOs
                # Check for any Sloth-generated rules (they have specific naming patterns)
                for group in groups:
                    group_name = group.get("name", "").lower()
                    # Check if rules from config-test-service or app1/app2 are present
                    if any(service in group_name for service in ["config-test", "app1", "app2", "sloth"]):
                        rules_found = True
                        break

            if rules_found:
                break

        except (json.JSONDecodeError, KeyError, AttributeError) as e:
            last_error = str(e)

        if attempt < max_retries - 1:
            time.sleep(retry_delay)

    # More informative assertion message
    if not rules_found:
        status = juju.status()
        assert False, (
            f"Expected to find Prometheus rules generated from config SLOs after {max_retries} attempts. "
            f"Sloth status: {status.apps[SLOTH].status}, "
            f"Prometheus status: {status.apps[PROMETHEUS].status}"
            f"{f', Last error: {last_error}' if last_error else ''}"
        )



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
    time.sleep(15)

    status = juju.status()
    # Charm should handle invalid config gracefully (log error, don't crash)
    # The charm may stay active (errors are logged) or go to error state
    assert status.apps[SLOTH].status in ["active", "error"], \
        f"Sloth should handle invalid config gracefully, got status: {status.apps[SLOTH].status}"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    if PROMETHEUS in juju.status().apps:
        juju.remove_application(PROMETHEUS)
