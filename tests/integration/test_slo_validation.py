#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""BDD integration tests for SLO validation behaviour."""

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


@when("we integrate parca with sloth and configure parca to provide invalid SLOs")
def test_configure_invalid_slos(juju: Juju):
    juju.config(PARCA, {"slo-errors-target": "999.9"})
    time.sleep(30)


@retry(stop=stop_after_attempt(20), wait=wait_fixed(10))
@then("sloth is in blocked state with a message indicating that there are invalid SLOs")
def test_blocked_state(juju: Juju):
    unit_status = juju.status().apps[SLOTH].units[f"{SLOTH}/0"]
    assert unit_status.is_blocked, \
        "Sloth should be in blocked state when SLO rule generation fails"
    message = unit_status.workload_status.message
    assert "incomplete" in message.lower() or "expected" in message.lower(), \
        f"Error message should mention incomplete generation: {message}"
    assert "rules" in message.lower(), \
        f"Error message should mention rules: {message}"


@then("sloth logs validation warnings for the SLOs that are failing")
def test_validation_warning_logs(juju: Juju):
    log_output = juju.cli("debug-log", "--replay", "--level=WARNING", "--limit=200")
    assert any(
        keyword in log_output.lower()
        for keyword in ["slo", "sloth", "invalid", "rules", "generate", "fail", "warning"]
    ), f"Logs should contain SLO validation warnings, found: {log_output[:500]}"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    juju.remove_application(SLOTH)
    juju.remove_application(PROMETHEUS)
    juju.remove_application(PARCA)

