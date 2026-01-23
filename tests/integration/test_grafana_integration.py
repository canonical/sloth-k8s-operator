#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test for Sloth charm Grafana dashboard integration."""

import json
import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import SLOTH

GRAFANA = "grafana"
PROMETHEUS = "prometheus"
TIMEOUT = 600


@pytest.mark.setup
def test_setup_with_cos(juju: Juju, sloth_charm, sloth_resources):
    """Deploy sloth with Grafana and Prometheus."""
    # Deploy Sloth
    juju.deploy(
        sloth_charm,
        SLOTH,
        resources=sloth_resources,
        trust=True,
    )

    # Deploy minimal COS (Grafana + Prometheus for dashboard testing)
    juju.deploy("grafana-k8s", GRAFANA, channel="1/stable", trust=True)
    juju.deploy("prometheus-k8s", PROMETHEUS, channel="1/stable", trust=True)

    # Integrate Sloth with Grafana and Prometheus
    juju.integrate(f"{SLOTH}:grafana-dashboard", f"{GRAFANA}:grafana-dashboard")
    juju.integrate(f"{SLOTH}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{GRAFANA}:grafana-source", f"{PROMETHEUS}:grafana-source")

    # Wait for all apps to become active
    juju.wait(
        lambda status: (
            status.apps[SLOTH].is_active
            and status.apps[GRAFANA].is_active
            and status.apps[PROMETHEUS].is_active
        ),
        error=jubilant.any_error,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )


def test_sloth_dashboard_in_grafana(juju: Juju):
    """Test that Sloth SLO dashboards are available in Grafana."""
    # Get Grafana admin password using juju run action
    result = juju.run(f"{GRAFANA}/0", "get-admin-password")

    # The password is in the results dictionary
    password = result.results.get("admin-password")

    assert password, f"Could not find admin password in results: {result.results}"

    # Query Grafana API for dashboards
    cmd = f'curl -s http://admin:{password}@localhost:3000/api/search?type=dash-db'
    result = juju.exec(cmd, unit=f"{GRAFANA}/0")

    dashboards = json.loads(result.stdout)

    # Check that dashboards list is returned
    assert isinstance(dashboards, list), "Grafana API should return a list of dashboards"
    assert len(dashboards) > 0, "Should have at least one dashboard"

    # Find Sloth SLO dashboards
    sloth_dashboards = [
        d for d in dashboards
        if "slo" in d.get("title", "").lower()
        or "sloth" in d.get("title", "").lower()
    ]

    assert len(sloth_dashboards) >= 2, \
        f"Should have at least 2 Sloth SLO dashboards, found: {len(sloth_dashboards)}"

    # Verify the dashboard titles
    dashboard_titles = [d["title"] for d in sloth_dashboards]

    # Check for detail dashboard
    assert any("detail" in title.lower() for title in dashboard_titles), \
        f"Expected 'detail' dashboard, got: {dashboard_titles}"

    # Check for overview/high-level dashboard
    assert any("high level" in title.lower() or "overview" in title.lower() for title in dashboard_titles), \
        f"Expected 'high level' or 'overview' dashboard, got: {dashboard_titles}"


def test_sloth_dashboard_content(juju: Juju):
    """Test that the Sloth SLO dashboard has valid content."""
    # Get Grafana admin password
    result = juju.run(f"{GRAFANA}/0", "get-admin-password")

    # The password is in the results dictionary
    password = result.results.get("admin-password")
    assert password, f"Could not find admin password in results: {result.results}"

    # Get list of dashboards to find UID
    cmd = f'curl -s http://admin:{password}@localhost:3000/api/search?type=dash-db'
    result = juju.exec(cmd, unit=f"{GRAFANA}/0")
    dashboards = json.loads(result.stdout)

    sloth_dashboard = next(
        (d for d in dashboards if "slo" in d.get("title", "").lower()),
        None
    )
    assert sloth_dashboard is not None, "Sloth dashboard should exist"

    # Fetch full dashboard JSON
    uid = sloth_dashboard["uid"]
    cmd = f'curl -s http://admin:{password}@localhost:3000/api/dashboards/uid/{uid}'
    result = juju.exec(cmd, unit=f"{GRAFANA}/0")
    dashboard_data = json.loads(result.stdout)

    assert "dashboard" in dashboard_data, "Response should contain dashboard data"
    dashboard = dashboard_data["dashboard"]

    # Verify dashboard has panels
    assert "panels" in dashboard, "Dashboard should have panels"
    assert len(dashboard["panels"]) > 0, "Dashboard should have at least one panel"

    # Verify panels reference Sloth metrics
    panel_targets = []
    for panel in dashboard["panels"]:
        if "targets" in panel:
            panel_targets.extend(panel["targets"])

    # Check that at least some panels query SLO-related metrics
    metric_queries = [t.get("expr", "") for t in panel_targets if "expr" in t]
    slo_queries = [q for q in metric_queries if "slo:" in q or "sloth_" in q]

    assert len(slo_queries) > 0, \
        f"Dashboard should have panels querying SLO metrics (slo:* or sloth_*), found: {len(metric_queries)} queries"


def test_sloth_generates_slo_rules(juju: Juju):
    """Test that Sloth generates SLO rules that appear in Prometheus."""
    # Wait for rules to propagate to Prometheus (with retry logic)
    # Rules need time to be: generated -> written to file -> sent via relation -> loaded by Prometheus
    max_attempts = 30  # 30 attempts * 5s = 150s max wait
    sloth_groups = []

    for attempt in range(max_attempts):
        # Query Prometheus for Sloth-generated rules
        cmd = 'curl -s http://localhost:9090/api/v1/rules'
        result = juju.exec(cmd, unit=f"{PROMETHEUS}/0")
        rules_data = json.loads(result.stdout)

        assert "data" in rules_data, "Prometheus should return rules data"
        groups = rules_data["data"]["groups"]

        # Find Sloth-generated rule groups
        sloth_groups = [
            g for g in groups
            if "sloth" in g["name"].lower() and "slo" in g["name"].lower()
        ]

        if len(sloth_groups) >= 3:
            break  # Found the rules!

        if attempt < max_attempts - 1:
            time.sleep(5)  # Wait 5 seconds before retrying

    assert len(sloth_groups) >= 3, \
        f"Should have at least 3 Sloth SLO rule groups (alerts, meta, sli), found: {len(sloth_groups)}"

    # Verify we have the expected rule groups
    group_names = [g["name"] for g in sloth_groups]
    assert any("alerts" in name for name in group_names), "Should have SLO alerts group"
    assert any("meta_recordings" in name for name in group_names), "Should have meta recordings group"
    assert any("sli_recordings" in name for name in group_names), "Should have SLI recordings group"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    juju.remove_application(GRAFANA)
    juju.remove_application(PROMETHEUS)
