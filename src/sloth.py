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
from pydantic import ValidationError

from alert_windows_models import AlertWindows

logger = logging.getLogger(__name__)

VERSION_PATTERN = re.compile(r'([0-9]+[.][0-9]+[.][0-9]+[-0-9a-f]*)')
# sloth server bind port
_SLOTH_PORT = 8080
DEFAULT_BIN_PATH = "/usr/bin/sloth"

# Paths for SLO specs and generated rules in the container
SLO_SPECS_DIR = "/etc/sloth/slos"
GENERATED_RULES_DIR = "/etc/sloth/rules"
SLO_PERIOD_WINDOWS_DIR = "/etc/sloth/windows"


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
        slo_period_windows: str = "",
    ):
        self._container = container
        self._slo_period = slo_period
        self._slo_period_windows = slo_period_windows
        self._current_slo_specs: typing.Optional[typing.List[typing.Dict]] = None

    def is_config_valid(self) -> typing.Tuple[bool, str]:
        """Validate that the SLO period configuration is consistent.

        Returns:
            Tuple of (is_valid, error_message). If valid, error_message is empty.
        """
        # Sloth has built-in defaults only for 30d and 28d periods
        # For any other period, custom windows must be provided
        if self._slo_period not in ("30d", "28d"):
            if not self._slo_period_windows:
                return (
                    False,
                    f"Custom slo-period '{self._slo_period}' requires slo-period-windows configuration",
                )
        return (True, "")

    def reconcile(self, additional_slos: typing.Optional[typing.List[typing.Dict]] = None):
        """Unconditional control logic."""
        if self._container.can_connect():
            self._current_slo_specs = additional_slos
            self._reconcile_slo_period_windows()
            self._reconcile_slo_specs(additional_slos)
            # Note: sloth is not a long-running service, so we don't need to manage it via Pebble
            # It's a generator tool that creates rules and exits
            # self._reconcile_sloth_service()

    def _reconcile_slo_period_windows(self):
        """Configure custom SLO period windows if provided."""
        windows_path = f"{SLO_PERIOD_WINDOWS_DIR}/custom-period.yaml"

        if not self._slo_period_windows:
            # No custom windows configured
            # Clean up any previously configured windows
            if self._container.exists(windows_path):
                try:
                    self._container.remove_path(windows_path)
                    logger.info("Removed custom SLO period windows configuration")
                except Exception as e:
                    logger.warning(f"Failed to remove custom period windows file: {e}")
            return

        # Write the custom period windows configuration
        current_content = ""
        if self._container.exists(windows_path):
            current_content = self._container.pull(windows_path).read()

        if current_content != self._slo_period_windows:
            try:
                # Parse YAML
                parsed_yaml = yaml.safe_load(self._slo_period_windows)

                # Validate against AlertWindows spec using Pydantic
                AlertWindows.model_validate(parsed_yaml)

                # Only create directory after validation succeeds
                if not self._container.exists(SLO_PERIOD_WINDOWS_DIR):
                    self._container.make_dir(SLO_PERIOD_WINDOWS_DIR, make_parents=True)

                self._container.push(windows_path, self._slo_period_windows)
                logger.info("Updated custom SLO period windows configuration")
            except yaml.YAMLError as e:
                logger.error(f"Invalid YAML in slo-period-windows config: {e}")
            except ValidationError as e:
                logger.error(
                    f"Invalid AlertWindows specification in slo-period-windows config: {e}"
                )

    def _reconcile_slo_specs(self, additional_slos: typing.Optional[typing.List[typing.Dict]] = None):
        """Create SLO specifications and generate Prometheus rules."""
        # Create directories
        for directory in [SLO_SPECS_DIR, GENERATED_RULES_DIR]:
            if not self._container.exists(directory):
                self._container.make_dir(directory, make_parents=True)

        # Process SLOs from relations
        self._reconcile_additional_slos(additional_slos)

    def _reconcile_additional_slos(self, additional_slos: typing.Optional[typing.List[typing.Dict]] = None):
        """Process additional SLOs from relations and generate rules."""
        if not additional_slos:
            logger.debug("No additional SLOs to process")
            return

        for idx, slo_spec in enumerate(additional_slos):
            try:
                service_name = slo_spec.get("service", f"service-{idx}")
                slo_path = f"{SLO_SPECS_DIR}/{service_name}.yaml"
                slo_yaml = yaml.safe_dump(slo_spec, default_flow_style=False)

                # Check if content changed
                current_content = ""
                if self._container.exists(slo_path):
                    current_content = self._container.pull(slo_path).read()

                output_path = f"{GENERATED_RULES_DIR}/{service_name}.yaml"
                rules_missing = not self._container.exists(output_path)

                if current_content != slo_yaml or rules_missing:
                    self._container.push(slo_path, slo_yaml, make_dirs=True)
                    if current_content != slo_yaml:
                        logger.info(f"Updated SLO specification for service '{service_name}'")

                    # Generate rules for this SLO
                    self._generate_rules_from_slo(slo_path)

            except Exception as e:
                logger.error(f"Failed to process SLO spec {idx}: {e}")

    def _generate_rules_from_slo(self, slo_path: str):
        """Generate Prometheus rules from an SLO specification."""
        slo_filename = Path(slo_path).stem
        output_path = f"{GENERATED_RULES_DIR}/{slo_filename}.yaml"

        # Remove any stale output file before generating so that a failed generation
        # leaves no rules behind — allowing validate_generated_rules to detect the mismatch.
        if self._container.exists(output_path):
            try:
                self._container.remove_path(output_path)
            except Exception as e:
                logger.warning(f"Failed to remove stale rules file {output_path}: {e}")

        try:
            # Build sloth generate command
            cmd = [DEFAULT_BIN_PATH, "generate", "-i", slo_path]

            # Add default SLO period
            cmd.extend(["--default-slo-period", self._slo_period])

            # Add custom period windows path if configured
            if self._slo_period_windows and self._container.exists(SLO_PERIOD_WINDOWS_DIR):
                cmd.extend(["--slo-period-windows-path", SLO_PERIOD_WINDOWS_DIR])

            # Run sloth generate command
            process = self._container.exec(cmd, timeout=30)
            stdout, stderr = process.wait_output()

            # Log stderr if present (sloth may have warnings/errors)
            if stderr:
                logger.warning(f"sloth generate stderr for {slo_filename}: {stderr}")

            # Save generated rules
            self._container.push(output_path, stdout, make_dirs=True)
            logger.info(f"Generated Prometheus rules for {slo_filename}")
        except ops.pebble.ExecError as e:
            service_name = slo_filename
            error_msg = e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)
            logger.warning(
                f"SLO validation failed for service '{service_name}': {error_msg}"
            )
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

    def validate_generated_rules(
        self, slo_specs: typing.Optional[typing.List[typing.Dict]] = None
    ) -> typing.Tuple[bool, str, int, int]:
        """Validate that all SLO specs successfully generated recording rules.

        Each SLO definition should generate exactly 17 Prometheus recording rules:
        - 2 alert rules (alerts group)
        - 7 meta recording rules (meta_recordings group)
        - 8 SLI recording rules (sli_recordings group)

        Args:
            slo_specs: List of SLO specification dictionaries

        Returns:
            Tuple of (is_valid, error_message, expected_count, actual_count)
            - is_valid: True if all SLOs generated the expected number of rules
            - error_message: Description of validation failure, empty if valid
            - expected_count: Number of rules that should have been generated
            - actual_count: Number of rules actually generated
        """
        if not self._container.can_connect() or not slo_specs:
            return (True, "", 0, 0)

        # Each SLO should generate 17 rules total (2 alerts + 7 meta + 8 sli)
        rules_per_slo = 17

        # Count total number of SLOs across all specs
        total_slos = sum(len(spec.get("slos", [])) for spec in slo_specs)
        expected_rule_count = total_slos * rules_per_slo

        # Count actual rules generated
        actual_rule_count = self._count_generated_rules()

        # Validate counts match
        is_valid = actual_rule_count == expected_rule_count
        error_message = ""

        if not is_valid:
            failed_slo_count = (expected_rule_count - actual_rule_count) // rules_per_slo
            error_message = (
                f"SLO rule generation incomplete: expected {expected_rule_count} rules, "
                f"found {actual_rule_count} ({failed_slo_count} SLO{'s' if failed_slo_count != 1 else ''} failed)"
            )

        return (is_valid, error_message, expected_rule_count, actual_rule_count)

    def _count_generated_rules(self) -> int:
        """Count the total number of rules in all generated rule files.

        Returns:
            Total number of rules found in generated YAML files
        """
        rule_count = 0

        if not self._container.exists(GENERATED_RULES_DIR):
            return rule_count

        try:
            files = self._container.list_files(GENERATED_RULES_DIR)
            for file_info in files:
                if not file_info.name.endswith(".yaml"):
                    continue

                file_path = f"{GENERATED_RULES_DIR}/{file_info.name}"
                try:
                    content = self._container.pull(file_path).read()
                    rules = yaml.safe_load(content)

                    if rules and "groups" in rules:
                        for group in rules["groups"]:
                            rule_count += len(group.get("rules", []))

                except Exception as e:
                    logger.error(f"Failed to count rules in {file_path}: {e}")

        except Exception as e:
            logger.error(f"Failed to list rules directory: {e}")

        return rule_count

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
