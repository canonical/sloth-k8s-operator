from contextlib import ExitStack
from unittest.mock import patch

import pytest
from ops.testing import Container, Context, PeerRelation

from charm import SlothOperatorCharm


@pytest.fixture(autouse=True)
def patch_all(tmp_path):
    ca_tmp_path = tmp_path / "ca.tmp"
    with ExitStack() as stack:
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(patch("charm.CA_CERT_PATH", str(ca_tmp_path)))
        stack.enter_context(patch("sloth.Sloth.version", "0.11.0"))
        yield


@pytest.fixture(scope="function")
def context():
    return Context(charm_type=SlothOperatorCharm)


@pytest.fixture
def sloth_peers():
    return PeerRelation("sloth-peers")


@pytest.fixture(scope="function")
def sloth_container():
    return Container(
        "sloth",
        can_connect=True,
    )
