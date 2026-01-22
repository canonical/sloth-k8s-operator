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
def slo_provider_charm():
    """SLO test provider charm used for integration testing."""
    provider_dir = Path("./tests/integration/slo-test-provider")
    charm_path = provider_dir / "slo-test-provider_ubuntu-22.04-amd64.charm"

    if charm_path.exists():
        logger.info(f"using existing provider charm: {charm_path}")
        return str(charm_path)

    logger.info(f"packing provider charm from {provider_dir}")
    return pack(str(provider_dir))


@fixture(scope="module")
def sloth_resources():
    return get_resources("./")
