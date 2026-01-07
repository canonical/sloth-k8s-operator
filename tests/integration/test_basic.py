#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Basic integration test for Sloth charm."""

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import SLOTH

TIMEOUT = 600


@pytest.mark.setup
def test_setup(juju: Juju, sloth_charm, sloth_resources):
    """Deploy sloth."""
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


def test_sloth_is_running(juju: Juju):
    """Test that sloth is running and active."""
    status = juju.status()
    assert SLOTH in status.apps
    assert status.apps[SLOTH].is_active


@pytest.mark.teardown
def test_teardown(juju: Juju):
    """Clean up deployed charms."""
    juju.remove_application(SLOTH)
