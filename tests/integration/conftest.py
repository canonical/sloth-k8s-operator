# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
from pathlib import Path

from pytest import fixture
from pytest_jubilant import get_resources, pack

logger = logging.getLogger("conftest")


@fixture(scope="module")
def sloth_charm():
    """Sloth charm used for integration testing."""
    if charm := os.getenv("CHARM_PATH"):
        logger.info("using sloth charm from env")
        return charm
    elif Path(charm := "./sloth-k8s_ubuntu@24.04-amd64.charm").exists():
        logger.info("using existing sloth charm from ./")
        return charm
    logger.info("packing from ./")
    return pack("./")


@fixture(scope="module")
def sloth_resources():
    return get_resources("./")
