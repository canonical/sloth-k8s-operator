#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test for SLO provider/requirer functionality."""

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import SLOTH

TIMEOUT = 600
TEST_PROVIDER = "slo-test-provider"


@pytest.mark.setup
def test_setup(juju: Juju, sloth_charm, sloth_resources):
    """Deploy sloth and test provider charm."""
    juju.deploy(
        sloth_charm,
        SLOTH,
        resources=sloth_resources,
        trust=True,
    )

    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        error=jubilant.any_error,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )


def test_sloth_with_slo_provider(juju: Juju, slo_provider_charm):
    """Test that Sloth can receive and process SLOs from a provider charm."""
    # Deploy the SLO provider test charm
    juju.deploy(
        slo_provider_charm,
        TEST_PROVIDER,
        config={
            "slo-service-name": "test-service",
            "slo-objective": "99.9",
        },
    )

    juju.wait(
        lambda status: TEST_PROVIDER in status.apps and status.apps[TEST_PROVIDER].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    # Relate the provider to Sloth
    juju.integrate(TEST_PROVIDER, f"{SLOTH}:slos")

    juju.wait(
        lambda status: status.apps[SLOTH].is_active,
        delay=10,
        successes=1,
        timeout=TIMEOUT,
    )

    # Verify that Sloth received and processed the SLO
    # Check the container for generated rules
    result = juju.run_action(f"{SLOTH}/0", "list-endpoints")
    assert result.status == "completed"


def test_sloth_generates_rules_from_provider(juju: Juju):
    """Verify that Sloth generates Prometheus rules from provided SLOs."""
    # Execute a command in the sloth container to check for generated rules
    cmd = "ls /etc/sloth/rules/"
    result = juju.ssh(f"{SLOTH}/0", f"exec --container sloth -- {cmd}")

    # Should see both prometheus-availability.yaml and test-service.yaml
    assert "prometheus-availability.yaml" in result.stdout
    assert "test-service.yaml" in result.stdout


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    if TEST_PROVIDER in juju.status().apps:
        juju.remove_application(TEST_PROVIDER)
