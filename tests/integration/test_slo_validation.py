#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""BDD integration tests for SLO validation behaviour."""

import textwrap
import time

import jubilant
import pytest
from jubilant import Juju
from pytest_bdd import given, then, when
from tenacity import retry, stop_after_attempt, wait_fixed

from tests.integration.helpers import SLOTH

PROMETHEUS = "prometheus"
PARCA = "parca"
TIMEOUT = 600

# Parca gRPC SLO with query expressions missing the {{.window}} range selector.
# Valid Sloth SLOs require {{.window}} in rate() calls so that Sloth can substitute
# the correct recording window (e.g. 5m, 30m, 1h, etc.) during rule generation.
INVALID_SLO_MISSING_WINDOW = textwrap.dedent("""\
    version: prometheus/v1
    service: parca
    slos:
    - name: parca-grpc-query-errors
      objective: 99.9
      description: SLO for parca-grpc-query-errors
      sli:
        events:
          error_query: >-
            sum(rate(grpc_server_handled_total{grpc_service="parca.query.v1alpha1.QueryService",
            grpc_method="Query",
            grpc_code=~"Aborted|Unavailable|Internal|Unknown|Unimplemented|DataLoss"}))
            or vector(0)
          total_query: >-
            sum(rate(grpc_server_handled_total{grpc_service="parca.query.v1alpha1.QueryService",
            grpc_method="Query"}))
            or vector(1)
      alerting:
        name: ParcaGrpcQueryErrorsHigh
        labels:
          severity: warning
        page_alert:
          labels:
            disable: "true"
        ticket_alert:
          labels:
            disable: "true"
""")


@pytest.mark.setup
@given("sloth deployed and related together with prometheus and parca")
def test_deploy_and_integrate(juju: Juju, sloth_charm, sloth_resources):
    juju.deploy(sloth_charm, SLOTH, resources=sloth_resources, trust=True)
    juju.deploy("prometheus-k8s", PROMETHEUS, channel="2/stable", trust=True)
    juju.deploy("parca-k8s", PARCA, channel="dev/edge", trust=True)
    juju.integrate(f"{SLOTH}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{PARCA}:metrics-endpoint", f"{PROMETHEUS}:metrics-endpoint")
    juju.integrate(f"{PARCA}:slos", f"{SLOTH}:sloth")
    juju.wait(
        lambda status: (
            status.apps[SLOTH].is_active
            and status.apps[PROMETHEUS].is_active
            and status.apps[PARCA].is_active
        ),
        error=jubilant.any_error,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )


@when("parca is configured with an SLO expression missing a query window")
def test_configure_invalid_slos(juju: Juju):
    juju.config(PARCA, {"slos": INVALID_SLO_MISSING_WINDOW})
    time.sleep(30)


@retry(stop=stop_after_attempt(20), wait=wait_fixed(10))
@then("sloth is in blocked state with a message indicating that there are invalid SLOs")
def test_blocked_state(juju: Juju):
    unit_status = juju.status().apps[SLOTH].units[f"{SLOTH}/0"]
    assert unit_status.is_blocked, \
        "Sloth should be in blocked state when SLO rule generation fails"
    message = unit_status.workload_status.message
    assert "incomplete" in message.lower(), \
        f"Error message should mention incomplete generation: {message}"
    assert "rules" in message.lower(), \
        f"Error message should mention rules: {message}"
    assert "failed" in message.lower(), \
        f"Error message should mention failed SLOs: {message}"


@retry(stop=stop_after_attempt(12), wait=wait_fixed(10))
@then("sloth logs validation errors for the SLOs that failed to generate")
def test_validation_error_logs(juju: Juju):
    log_output = juju.cli(
        "debug-log", "--replay", "--level=WARNING", "--limit=500", f"--include={SLOTH}/0"
    )
    log_lower = log_output.lower()
    assert "slo validation failed" in log_lower or "slo rule validation failed" in log_lower, \
        f"Logs should contain SLO validation failure warnings, found: {log_output[-500:]}"
    assert "generate" in log_lower, \
        f"Logs should mention rule generation failure, found: {log_output[-500:]}"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    juju.remove_application(SLOTH)
    juju.remove_application(PROMETHEUS)
    juju.remove_application(PARCA)

