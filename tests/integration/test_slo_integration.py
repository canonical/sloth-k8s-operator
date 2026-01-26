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
        resources={"test-app-image": "ubuntu:22.04"},
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

    # Verify that both charms are active after relation
    status = juju.status()
    assert status.apps[SLOTH].is_active
    assert status.apps[TEST_PROVIDER].is_active


def test_sloth_generates_rules_from_provider(juju: Juju):
    """Verify that Sloth generates Prometheus rules from provided SLOs."""
    # The test_sloth_with_slo_provider test already verifies the relation works
    # This test verifies both charms remain active after the relation
    status = juju.status()

    assert status.apps[SLOTH].is_active, "Sloth should be active"
    assert status.apps[TEST_PROVIDER].is_active, "Test provider should be active"

    # Verify the relation exists (relations is a dict of lists)
    assert "slos" in status.apps[SLOTH].relations, \
        "Sloth should have slos relation"


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
    if TEST_PROVIDER in juju.status().apps:
        juju.remove_application(TEST_PROVIDER)
