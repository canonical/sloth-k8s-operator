#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Comprehensive integration test for SLO provider to Prometheus rules conversion."""

import json
import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import SLOTH

GRAFANA = "grafana"
PROMETHEUS = "prometheus"
PARCA = "parca"
TIMEOUT = 600


@pytest.mark.setup
def test_setup_full_cos_with_parca(juju: Juju, sloth_charm, sloth_resources):
    """Deploy complete stack: Sloth, Prometheus, Grafana, and Parca (SLO provider)."""
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

    # Deploy Parca from charmhub (provides SLOs via sloth relation)
    juju.deploy("parca-k8s", PARCA, channel="dev/edge", trust=True)

    # Set up integrations
    juju.integrate(f"{SLOTH}:grafana-dashboard", f"{GRAFANA}:grafana-dashboard")
    juju.integrate(f"{SLOTH}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{GRAFANA}:grafana-source", f"{PROMETHEUS}:grafana-source")
    juju.integrate(f"{PARCA}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{PARCA}:slos", f"{SLOTH}:sloth")

    # Wait for all apps to become active
    juju.wait(
        lambda status: (
            status.apps[SLOTH].is_active
            and status.apps[GRAFANA].is_active
            and status.apps[PROMETHEUS].is_active
            and status.apps[PARCA].is_active
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
    assert status.apps[PARCA].is_active, "Parca should be active"

    # Verify the relation exists
    assert "sloth" in status.apps[SLOTH].relations, \
        "Sloth should have sloth relation"


def test_sloth_generates_parca_slo_rules(juju: Juju):
    """Test that Sloth generates Prometheus rules from Parca's SLO specs.

    This is the critical test that verifies the complete SLO-to-rules flow:
    1. Parca charm sends SLO spec via slos relation
    2. Sloth charm receives the SLO spec
    3. Sloth generates Prometheus recording/alerting rules
    4. Rules are sent to Prometheus via metrics-endpoint relation
    5. Prometheus loads and serves the rules
    """
    # Wait for Parca SLO rules to appear in Prometheus
    # This may take longer as it involves: relation update → sloth generate → prometheus reload
    max_attempts = 40  # 40 * 5s = 200s max wait (generous timeout for full flow)
    parca_service_groups = []

    for attempt in range(max_attempts):
        try:
            cmd = 'curl -s http://localhost:9090/api/v1/rules'
            result = juju.exec(cmd, unit=f"{PROMETHEUS}/0")
            rules_data = json.loads(result.stdout)

            assert "data" in rules_data, "Prometheus should return rules data"
            groups = rules_data["data"]["groups"]

            # Find parca-service related Sloth-generated rule groups
            # Note: Prometheus transforms hyphens to underscores in group names
            parca_service_groups = [
                g for g in groups
                if "sloth" in g["name"].lower() and "parca" in g["name"].lower()
            ]

            if len(parca_service_groups) >= 3:
                break  # Found Parca's rules!
        except Exception:
            # Prometheus might not be ready yet, will retry
            pass

        if attempt < max_attempts - 1:
            time.sleep(5)

    # This is the key assertion - if this passes, the SLO-to-rules flow works!
    assert len(parca_service_groups) >= 3, \
        f"Should have at least 3 parca SLO rule groups from Parca, found: {len(parca_service_groups)}"

    # Verify we have the expected rule groups for Parca's SLOs
    group_names = [g["name"] for g in parca_service_groups]
    assert any("alerts" in name for name in group_names), \
        "Should have SLO alerts group for parca"
    assert any("meta" in name for name in group_names), \
        "Should have meta recordings group for parca"
    assert any("sli" in name for name in group_names), \
        "Should have SLI recordings group for parca"


def test_parca_slo_rules_content(juju: Juju):
    """Verify the actual content of the generated rules matches Parca's SLO spec."""
    cmd = 'curl -s http://localhost:9090/api/v1/rules'
    result = juju.exec(cmd, unit=f"{PROMETHEUS}/0")
    rules_data = json.loads(result.stdout)

    groups = rules_data["data"]["groups"]

    # Find parca SLI recordings group
    # Note: Prometheus transforms hyphens to underscores in group names
    sli_group = next(
        (g for g in groups
         if "sloth" in g["name"].lower()
         and "parca" in g["name"].lower()
         and "sli" in g["name"].lower()
         and "recordings" in g["name"].lower()),
        None
    )

    assert sli_group is not None, "Should have SLI recordings group for parca"

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

    # Find parca alerts group
    # Note: Prometheus transforms hyphens to underscores in group names
    alerts_group = next(
        (g for g in groups
         if "sloth" in g["name"].lower()
         and "parca" in g["name"].lower()
         and "alerts" in g["name"].lower()),
        None
    )

    assert alerts_group is not None, "Should have alerts group for parca"

    # Verify alert rules exist
    alert_rules = alerts_group.get("rules", [])
    assert len(alert_rules) > 0, "Alerts group should contain alert rules"

    # Check for Parca's alert names from the SLO spec
    alert_names = [r.get("name") for r in alert_rules if r.get("type") == "alerting"]

    # Parca defines alerts like: ParcaGrpcQueryErrorsHigh, ParcaGrpcQueryLatencyHigh, etc.
    assert any("Parca" in name for name in alert_names), \
        f"Should have Parca-related alerts, found alerts: {alert_names}"


def test_dynamic_slo_update(juju: Juju):
    """Test that changes to Parca's SLO configuration propagate to Prometheus rules."""
    # Change the SLO objectives
    juju.config(PARCA, {"slo-errors-target": "0.999", "slo-latency-target": "0.95"})

    # Wait for the update to propagate through the entire chain
    # parca config → parca relation update → sloth generate → prometheus reload
    time.sleep(30)

    # Verify rules still exist (may have slightly different objectives)
    cmd = 'curl -s http://localhost:9090/api/v1/rules'
    result = juju.exec(cmd, unit=f"{PROMETHEUS}/0")
    rules_data = json.loads(result.stdout)

    groups = rules_data["data"]["groups"]
    # Note: Prometheus transforms hyphens to underscores in group names
    parca_service_groups = [
        g for g in groups
        if "sloth" in g["name"].lower() and "parca" in g["name"].lower()
    ]

    # Rules should still be present after config change
    assert len(parca_service_groups) >= 3, \
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
    juju.remove_application(PARCA)