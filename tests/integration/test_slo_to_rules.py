#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Comprehensive integration test for SLO provider to Prometheus rules conversion."""

import json
import time
from typing import Callable

import jubilant
import pytest
from jubilant import Juju, TaskError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_delay,
    wait_exponential,
)

from tests.integration.helpers import SLOTH

GRAFANA = "grafana"
PROMETHEUS = "prometheus"
TEST_PROVIDER = "slo-test-provider"
TIMEOUT = 600
PROMETHEUS_RULES_CMD = "curl -s http://localhost:9090/api/v1/rules"
PROMETHEUS_RULES_TIMEOUT = 600


def _fetch_prometheus_rules(juju: Juju) -> dict:
    """Fetch Prometheus rules once."""
    result = juju.exec(PROMETHEUS_RULES_CMD, unit=f"{PROMETHEUS}/0")
    return json.loads(result.stdout)


@retry(
    stop=stop_after_delay(PROMETHEUS_RULES_TIMEOUT),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((TaskError, AssertionError, KeyError, json.JSONDecodeError)),
    reraise=True,
)
def _wait_for_prometheus_groups(
    juju: Juju,
    group_filter: Callable[[dict], bool],
    *,
    min_count: int = 1,
) -> list[dict]:
    """Return matching rule groups once they appear in Prometheus."""
    rules_data = _fetch_prometheus_rules(juju)
    if "data" not in rules_data or "groups" not in rules_data["data"]:
        raise AssertionError("Prometheus should return rules data")
    groups = rules_data["data"]["groups"]
    matches = [g for g in groups if group_filter(g)]
    if len(matches) < min_count:
        raise AssertionError(
            f"Expected at least {min_count} Prometheus rule groups, found: {len(matches)}"
        )
    return matches


@pytest.mark.setup
def test_setup_full_cos_with_provider(juju: Juju, sloth_charm, sloth_resources, slo_provider_charm):
    """Deploy complete stack: Sloth, Prometheus, Grafana, and SLO provider."""
    # Deploy Sloth
    juju.deploy(
        sloth_charm,
        SLOTH,
        resources=sloth_resources,
        trust=True,
    )

    # Deploy COS components
    juju.deploy("grafana-k8s", GRAFANA, channel="2/stable", trust=True)
    juju.deploy("prometheus-k8s", PROMETHEUS, channel="2/stable", trust=True)

    # Deploy SLO provider test charm with specific configuration
    juju.deploy(
        slo_provider_charm,
        TEST_PROVIDER,
        config={
            "slo-service-name": "test-service",
            "slo-requests-availability": "99.5",
        },
        resources={"test-app-image": "ubuntu:22.04"},
    )

    # Set up integrations
    juju.integrate(f"{SLOTH}:grafana-dashboard", f"{GRAFANA}:grafana-dashboard")
    juju.integrate(f"{SLOTH}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{GRAFANA}:grafana-source", f"{PROMETHEUS}:grafana-source")
    juju.integrate(TEST_PROVIDER, f"{SLOTH}:sloth")

    # Wait for all apps to become active
    juju.wait(
        lambda status: (
            status.apps[SLOTH].is_active
            and status.apps[GRAFANA].is_active
            and status.apps[PROMETHEUS].is_active
            and TEST_PROVIDER in status.apps
            and status.apps[TEST_PROVIDER].is_active
        ),
        error=jubilant.any_error,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )


def test_slo_provider_relation_established(juju: Juju):
    """Verify that the SLO provider relation is properly established."""
    status = juju.status()

    assert status.apps[SLOTH].is_active, "Sloth should be active"
    assert status.apps[TEST_PROVIDER].is_active, "Test provider should be active"

    # Verify the relation exists
    assert "sloth" in status.apps[SLOTH].relations, \
        "Sloth should have sloth relation"


def test_sloth_generates_builtin_prometheus_rules(juju: Juju):
    """Test that Sloth generates its built-in Prometheus availability SLO rules."""
    prometheus_groups = _wait_for_prometheus_groups(
        juju,
        lambda g: "sloth" in g["name"].lower() and "prometheus" in g["name"].lower(),
        min_count=3,
    )

    assert len(prometheus_groups) >= 3, \
        f"Should have at least 3 Prometheus SLO rule groups (alerts, meta, sli), found: {len(prometheus_groups)}"

    # Verify we have the expected rule groups for the built-in Prometheus SLO
    group_names = [g["name"] for g in prometheus_groups]
    assert any("alerts" in name for name in group_names), "Should have SLO alerts group"
    assert any("meta" in name for name in group_names), "Should have meta recordings group"
    assert any("sli" in name for name in group_names), "Should have SLI recordings group"


def test_sloth_generates_provider_slo_rules(juju: Juju):
    """Test that Sloth generates Prometheus rules from the SLO provider charm.

    This is the critical test that verifies the complete SLO-to-rules flow:
    1. Provider charm sends SLO spec via sloth relation
    2. Sloth charm receives the SLO spec
    3. Sloth generates Prometheus recording/alerting rules
    4. Rules are sent to Prometheus via metrics-endpoint relation
    5. Prometheus loads and serves the rules
    """
    # Wait for provider SLO rules to appear in Prometheus
    # This may take longer as it involves: relation update → sloth generate → prometheus reload
    test_service_groups = _wait_for_prometheus_groups(
        juju,
        lambda g: "sloth" in g["name"].lower() and "test_service" in g["name"].lower(),
        min_count=3,
    )

    # This is the key assertion - if this passes, the SLO-to-rules flow works!
    assert len(test_service_groups) >= 3, \
        f"Should have at least 3 test-service SLO rule groups from provider, found: {len(test_service_groups)}"

    # Verify we have the expected rule groups for the provider's SLO
    group_names = [g["name"] for g in test_service_groups]
    assert any("alerts" in name for name in group_names), \
        "Should have SLO alerts group for test-service"
    assert any("meta" in name for name in group_names), \
        "Should have meta recordings group for test-service"
    assert any("sli" in name for name in group_names), \
        "Should have SLI recordings group for test-service"


def test_provider_slo_rules_content(juju: Juju):
    """Verify the actual content of the generated rules matches the SLO spec."""
    groups = _wait_for_prometheus_groups(
        juju,
        lambda g: "sloth" in g["name"].lower() and "test_service" in g["name"].lower(),
        min_count=3,
    )

    # Find test-service SLI recordings group
    # Note: Prometheus transforms hyphens to underscores in group names
    sli_group = next(
        (g for g in groups
         if "sloth" in g["name"].lower()
         and "test_service" in g["name"].lower()
         and "sli" in g["name"].lower()
         and "recordings" in g["name"].lower()),
        None
    )

    assert sli_group is not None, "Should have SLI recordings group for test-service"

    # Verify rules exist in the group
    rules = sli_group.get("rules", [])
    assert len(rules) > 0, "SLI recordings group should contain rules"

    # Check for expected recording rule metrics
    rule_names = [r.get("name") for r in rules if r.get("type") == "recording"]

    # Sloth generates recording rules like: slo:sli_error:ratio_rate5m, slo:sli_error:ratio_rate30m, etc.
    expected_patterns = ["slo:sli_error:ratio", "slo:period_error_budget_remaining"]

    found_patterns = []
    for pattern in expected_patterns:
        if any(pattern in name for name in rule_names):
            found_patterns.append(pattern)

    assert len(found_patterns) >= 1, \
        f"Should have at least one expected SLO recording rule pattern, found: {found_patterns}"

    # Find test-service alerts group
    # Note: Prometheus transforms hyphens to underscores in group names
    alerts_group = next(
        (g for g in groups
         if "sloth" in g["name"].lower()
         and "test_service" in g["name"].lower()
         and "alerts" in g["name"].lower()),
        None
    )

    assert alerts_group is not None, "Should have alerts group for test-service"

    # Verify alert rules exist
    alert_rules = alerts_group.get("rules", [])
    assert len(alert_rules) > 0, "Alerts group should contain alert rules"

    # Check for the custom alert name from the SLO spec
    alert_names = [r.get("name") for r in alert_rules if r.get("type") == "alerting"]

    # The test provider defines alert name as: TestserviceHighErrorRate
    assert any("TestserviceHighErrorRate" in name for name in alert_names), \
        f"Should have custom alert 'TestserviceHighErrorRate', found alerts: {alert_names}"


def test_dynamic_slo_update(juju: Juju):
    """Test that changes to SLO configuration propagate to Prometheus rules."""
    # Change the SLO objective
    juju.config(TEST_PROVIDER, {"slo-requests-availability": "99.9"})

    # Wait for the update to propagate through the entire chain
    # provider config → provider relation update → sloth generate → prometheus reload
    time.sleep(30)

    # Note: Prometheus transforms hyphens to underscores in group names
    test_service_groups = _wait_for_prometheus_groups(
        juju,
        lambda g: "sloth" in g["name"].lower() and "test_service" in g["name"].lower(),
        min_count=3,
    )

    # Rules should still be present after config change
    assert len(test_service_groups) >= 3, \
        "Rules should still exist after SLO config update"


def test_grafana_dashboards_present(juju: Juju):
    """Verify that Sloth dashboards are available in Grafana."""
    result = juju.run(f"{GRAFANA}/0", "get-admin-password")
    password = result.results.get("admin-password")
    assert password, "Could not get Grafana admin password"

    cmd = f'curl -s http://admin:{password}@localhost:3000/api/search?type=dash-db'
    result = juju.exec(cmd, unit=f"{GRAFANA}/0")
    dashboards = json.loads(result.stdout)

    assert isinstance(dashboards, list), "Grafana API should return a list of dashboards"

    # Find Sloth SLO dashboards
    sloth_dashboards = [
        d for d in dashboards
        if "slo" in d.get("title", "").lower()
        or "sloth" in d.get("title", "").lower()
    ]

    assert len(sloth_dashboards) >= 2, \
        f"Should have at least 2 Sloth SLO dashboards, found: {len(sloth_dashboards)}"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    juju.remove_application(GRAFANA)
    juju.remove_application(PROMETHEUS)
    if TEST_PROVIDER in juju.status().apps:
        juju.remove_application(TEST_PROVIDER)
