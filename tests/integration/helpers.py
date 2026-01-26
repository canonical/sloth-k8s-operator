# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
from subprocess import getoutput, getstatusoutput
from typing import Tuple

from jubilant import Juju

# Constants from charm source (avoid importing from src/)
CA_CERT_PATH = "/usr/local/share/ca-certificates/ca.cert"
SLOTH_HTTP_PORT = 7994  # nginx proxy port for sloth

SLOTH = "sloth"
logger = logging.getLogger("helpers")


def get_unit_ip(model_name, app_name, unit_id):
    """Return a juju unit's IP."""
    return getoutput(
        f"""juju status --model {model_name} --format json | jq '.applications.{app_name}.units."{app_name}/{unit_id}".address'"""
    ).strip('"')


def get_unit_fqdn(model_name, app_name, unit_id):
    """Return a juju unit's K8s cluster FQDN."""
    return f"{app_name}-{unit_id}.{app_name}-endpoints.{model_name}.svc.cluster.local"


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address


def query_sloth_server(
    model_name, exec_target_app_name, tls=False, ca_cert_path=CA_CERT_PATH, url_path=""
) -> Tuple[int, str]:
    """Curl the sloth server from a juju unit, and return the statuscode."""
    sloth_address = get_unit_fqdn(model_name, SLOTH, 0)
    url = f"{'https' if tls else 'http'}://{sloth_address}:{SLOTH_HTTP_PORT}{url_path}"
    cert_flags = f"--cacert {ca_cert_path}" if tls else ""
    cmd = f"""juju exec --model {model_name} --unit {exec_target_app_name}/0 "curl {cert_flags} {url}" """
    return getstatusoutput(cmd)
