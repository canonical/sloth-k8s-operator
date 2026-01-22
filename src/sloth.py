# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Control Sloth running in a container under Pebble. Provides a Sloth class."""

import logging
import re
import typing
from pathlib import Path

import ops.pebble
import yaml
from ops import Container
from ops.pebble import Layer

logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r'([0-9]+[.][0-9]+[.][0-9]+[-0-9a-f]*)')
# sloth server bind port
_SLOTH_PORT = 8080
DEFAULT_BIN_PATH = "/usr/local/bin/sloth"

# Paths for SLO specs and generated rules in the container
SLO_SPECS_DIR = "/etc/sloth/slos"
GENERATED_RULES_DIR = "/etc/sloth/rules"


class Sloth:
    """Sloth workload."""

    port = _SLOTH_PORT
    service_name = "sloth"
    container_name = "sloth"
    layer_name = "sloth"

    def __init__(
        self,
        container: Container,
        slo_period: str = "30d",
        additional_slos: typing.Optional[typing.List[typing.Dict]] = None,
    ):
        self._container = container
        self._slo_period = slo_period
        self._additional_slos = additional_slos or []

    def reconcile(self):
        """Unconditional control logic."""
        if self._container.can_connect():
            self._reconcile_slo_specs()
            # Note: sloth is not a long-running service, so we don't need to manage it via Pebble
            # It's a generator tool that creates rules and exits
            # self._reconcile_sloth_service()

    def _reconcile_slo_specs(self):
        """Create SLO specifications and generate Prometheus rules."""
        # Create directories
        for directory in [SLO_SPECS_DIR, GENERATED_RULES_DIR]:
            if not self._container.exists(directory):
                self._container.make_dir(directory, make_parents=True)

        # Write hardcoded Prometheus availability SLO
        prometheus_slo = self._get_prometheus_availability_slo()
        slo_path = f"{SLO_SPECS_DIR}/prometheus-availability.yaml"

        current_content = ""
        if self._container.exists(slo_path):
            current_content = self._container.pull(slo_path).read()

        if current_content != prometheus_slo:
            self._container.push(slo_path, prometheus_slo, make_dirs=True)
            logger.info("Updated Prometheus availability SLO specification")

            # Generate Prometheus rules from the SLO
            self._generate_rules_from_slo(slo_path)

        # Process additional SLOs from relations
        self._reconcile_additional_slos()

    def _get_prometheus_availability_slo(self) -> str:
        """Return the hardcoded Prometheus availability SLO specification."""
        slo = {
            "version": "prometheus/v1",
            "service": "prometheus",
            "labels": {
                "owner": "observability-team",
                "repo": "sloth-k8s",
            },
            "slos": [
                {
                    "name": "requests-availability",
                    "objective": 99.0,
                    "description": "Prometheus should have low request activity (less than 1 req/s) 99% of the time",
                    "sli": {
                        "events": {
                            "error_query": 'sum(rate(prometheus_http_requests_total{juju_application="prometheus"}[{{.window}}])) > bool 1',
                            "total_query": 'sum(rate(prometheus_http_requests_total{juju_application="prometheus"}[{{.window}}])) >= bool 0',
                        }
                    },
                    "alerting": {
                        "name": "PrometheusHighRequestActivity",
                        "labels": {
                            "category": "availability",
                        },
                        "annotations": {
                            "summary": "Prometheus is experiencing high request activity (>1 req/s)",
                        },
                        "page_alert": {
                            "labels": {
                                "severity": "critical",
                            }
                        },
                        "ticket_alert": {
                            "labels": {
                                "severity": "warning",
                            }
                        },
                    },
                }
            ],
        }
        return yaml.safe_dump(slo, default_flow_style=False)

    def _reconcile_additional_slos(self):
        """Process additional SLOs from relations and generate rules."""
        if not self._additional_slos:
            logger.debug("No additional SLOs to process")
            return

        for idx, slo_spec in enumerate(self._additional_slos):
            try:
                service_name = slo_spec.get("service", f"service-{idx}")
                slo_path = f"{SLO_SPECS_DIR}/{service_name}.yaml"
                slo_yaml = yaml.safe_dump(slo_spec, default_flow_style=False)

                # Check if content changed
                current_content = ""
                if self._container.exists(slo_path):
                    current_content = self._container.pull(slo_path).read()

                if current_content != slo_yaml:
                    self._container.push(slo_path, slo_yaml, make_dirs=True)
                    logger.info(f"Updated SLO specification for service '{service_name}'")

                    # Generate rules for this SLO
                    self._generate_rules_from_slo(slo_path)

            except Exception as e:
                logger.error(f"Failed to process SLO spec {idx}: {e}")

    def _generate_rules_from_slo(self, slo_path: str):
        """Generate Prometheus rules from an SLO specification."""
        slo_filename = Path(slo_path).stem
        output_path = f"{GENERATED_RULES_DIR}/{slo_filename}.yaml"

        try:
            # Run sloth generate command
            process = self._container.exec(
                [DEFAULT_BIN_PATH, "generate", "-i", slo_path],
                timeout=30,
            )
            stdout, stderr = process.wait_output()

            # Save generated rules
            self._container.push(output_path, stdout, make_dirs=True)
            logger.info(f"Generated Prometheus rules for {slo_filename}")
        except ops.pebble.ExecError as e:
            logger.error(f"Failed to generate rules from {slo_path}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error generating rules: {e}")

    def get_alert_rules(self) -> dict:
        """Collect and consolidate all generated alert rules."""
        if not self._container.can_connect():
            return {}

        if not self._container.exists(GENERATED_RULES_DIR):
            return {}

        all_rules = {"groups": []}

        try:
            # List all rule files
            files = self._container.list_files(GENERATED_RULES_DIR)

            for file_info in files:
                if not file_info.name.endswith(".yaml"):
                    continue

                file_path = f"{GENERATED_RULES_DIR}/{file_info.name}"

                try:
                    content = self._container.pull(file_path).read()
                    rules = yaml.safe_load(content)

                    if rules and "groups" in rules:
                        all_rules["groups"].extend(rules["groups"])

                except Exception as e:
                    logger.error(f"Failed to load rules from {file_path}: {e}")

        except Exception as e:
            logger.error(f"Failed to list rules directory: {e}")

        return all_rules

    def _reconcile_sloth_service(self):
        layer = self._pebble_layer()
        self._container.add_layer(self.layer_name, layer, combine=True)
        self._container.replan()

    def _pebble_layer(self) -> Layer:
        """Return a Pebble layer for Sloth based on the current configuration."""
        return Layer(
            {
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "sloth",
                        "command": sloth_command_line(
                            http_address=f"localhost:{_SLOTH_PORT}",
                            slo_period=self._slo_period,
                        ),
                        "startup": "enabled",
                    }
                },
            }
        )

    @property
    def version(self) -> str:
        """Fetch the version from the binary."""
        try:
            version_out = self._container.exec([DEFAULT_BIN_PATH, "version"]).stdout
        except ops.pebble.Error:
            logger.exception("error attempting to fetch sloth version from container")
            return ""

        if not version_out:
            logger.error("unable to get version from sloth: version command has no stdout.")
            return ""

        match = VERSION_PATTERN.search(version_out.read())
        if not match:
            logger.error(
                f"unable to get version from sloth: version command returned {version_out!r}, "
                f"which didn't match the expected {VERSION_PATTERN.pattern!r}"
            )
            return ""
        return match.groups()[0]


def sloth_command_line(
    http_address: str = f":{_SLOTH_PORT}",
    slo_period: str = "30d",
    *,
    bin_path: str = DEFAULT_BIN_PATH,
) -> str:
    """Generate a valid Sloth command line.

    Args:
        http_address: Http address for the sloth server.
        slo_period: SLO period for calculations.
        bin_path: Path to the Sloth binary to be started.
    """
    cmd = [
        str(bin_path),
        "serve",
        f"--listen={http_address}",
        f"--default-slo-period={slo_period}",
    ]

    return " ".join(cmd)
