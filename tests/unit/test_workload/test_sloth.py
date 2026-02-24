# Copyright 2025 Canonical
# See LICENSE file for licensing details.
from io import StringIO
from unittest.mock import MagicMock

import pytest
import yaml

from sloth import GENERATED_RULES_DIR, SLO_PERIOD_WINDOWS_DIR, SLO_SPECS_DIR, Sloth


@pytest.fixture
def sloth():
    container_mock = MagicMock()
    container_mock.can_connect.return_value = True
    return Sloth(
        container=container_mock,
        slo_period="30d",
        slo_period_windows="",
    )


def test_default_pebble_layer(sloth):
    expected = {
        "services": {
            "sloth": {
                "summary": "sloth",
                "startup": "enabled",
                "override": "replace",
                "command": f"/usr/bin/sloth serve --listen=localhost:{Sloth.port} --default-slo-period=30d",
            }
        }
    }
    assert sloth._pebble_layer() == expected


def _mock_container_exec_return_value(sloth, value):
    pebble_exec_out = MagicMock()
    pebble_exec_out.stdout = StringIO(value)
    sloth._container.exec.return_value = pebble_exec_out


@pytest.mark.parametrize("version", ("0.11.0", "0.10.0"))
def test_fetch_version_valid(sloth, version):
    _mock_container_exec_return_value(sloth, f"sloth version {version}")
    assert sloth.version() == version


@pytest.mark.parametrize("version", ("", "booboontu", "42"))
def test_fetch_version_invalid(sloth, version):
    _mock_container_exec_return_value(sloth, f"sloth version {version}")
    assert sloth.version() == ""


def test_reconcile_slo_specs_creates_directories(sloth):
    """Test that reconcile creates necessary directories."""
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_specs()

    # Should create both directories
    assert any(
        call[0][0] == SLO_SPECS_DIR for call in sloth._container.make_dir.call_args_list
    )
    assert any(
        call[0][0] == GENERATED_RULES_DIR for call in sloth._container.make_dir.call_args_list
    )


def test_reconcile_additional_slos(sloth):
    """Test that additional SLOs from relations are processed."""
    additional_slo = {
        "version": "prometheus/v1",
        "service": "my-app",
        "labels": {"team": "my-team"},
        "slos": [
            {
                "name": "requests-availability",
                "objective": 99.9,
                "description": "High availability target",
            }
        ],
    }

    sloth._container.exists.return_value = False

    # Mock exec for sloth generate command
    exec_mock = MagicMock()
    exec_mock.wait_output.return_value = ("generated rules", "")
    sloth._container.exec.return_value = exec_mock

    sloth._reconcile_additional_slos([additional_slo])

    # Verify the SLO spec was written
    push_calls = list(sloth._container.push.call_args_list)
    slo_written = False
    for call_args in push_calls:
        path = call_args[0][0]
        if "my-app.yaml" in path and SLO_SPECS_DIR in path:
            slo_written = True
            content = call_args[0][1]
            slo_data = yaml.safe_load(content)
            assert slo_data["service"] == "my-app"
            break

    assert slo_written, "Additional SLO spec was not written"


def test_reconcile_additional_slos_generates_rules(sloth):
    """Test that rules are generated for additional SLOs."""
    additional_slo = {
        "version": "prometheus/v1",
        "service": "my-app",
        "slos": [{"name": "test"}],
    }

    sloth._container.exists.return_value = False

    # Mock exec for sloth generate command
    exec_mock = MagicMock()
    generated_rules = "groups:\n  - name: test-rules\n"
    exec_mock.wait_output.return_value = (generated_rules, "")
    sloth._container.exec.return_value = exec_mock

    sloth._reconcile_additional_slos([additional_slo])

    # Verify sloth generate was called
    assert sloth._container.exec.called
    exec_args = sloth._container.exec.call_args[0][0]
    assert "generate" in exec_args

    # Verify rules were written
    push_calls = list(sloth._container.push.call_args_list)
    rules_written = False
    for call_args in push_calls:
        path = call_args[0][0]
        if "my-app.yaml" in path and GENERATED_RULES_DIR in path:
            rules_written = True
            assert call_args[0][1] == generated_rules
            break

    assert rules_written, "Generated rules were not written"


def test_reconcile_multiple_additional_slos(sloth):
    """Test processing multiple SLOs from different relations."""
    additional_slos = [
        {"version": "prometheus/v1", "service": "app1", "slos": [{"name": "test1"}]},
        {"version": "prometheus/v1", "service": "app2", "slos": [{"name": "test2"}]},
    ]

    sloth._container.exists.return_value = False

    # Mock exec
    exec_mock = MagicMock()
    exec_mock.wait_output.return_value = ("rules", "")
    sloth._container.exec.return_value = exec_mock

    sloth._reconcile_additional_slos(additional_slos)

    # Verify both SLO specs were written
    push_calls = list(sloth._container.push.call_args_list)
    services_written = set()
    for call_args in push_calls:
        path = call_args[0][0]
        if SLO_SPECS_DIR in path:
            if "app1.yaml" in path:
                services_written.add("app1")
            elif "app2.yaml" in path:
                services_written.add("app2")

    assert services_written == {"app1", "app2"}, "Not all SLO specs were written"


def test_generate_rules_from_slo(sloth):
    """Test that rules are generated from SLO specs."""
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("groups:\n- name: test\n  rules: []", "")
    sloth._container.exec.return_value = mock_process

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/test.yaml")

    # Verify sloth generate was called
    sloth._container.exec.assert_called_once()
    args = sloth._container.exec.call_args[0][0]
    assert args[0].endswith("sloth")
    assert "generate" in args
    assert "-i" in args

    # Verify output was saved
    sloth._container.push.assert_called_once()
    assert GENERATED_RULES_DIR in sloth._container.push.call_args[0][0]


def test_get_alert_rules_returns_empty_when_disconnected(sloth):
    """Test that get_alert_rules returns empty dict when container is disconnected."""
    sloth._container.can_connect.return_value = False

    result = sloth.get_alert_rules()

    assert result == {}


def test_get_alert_rules_consolidates_multiple_files(sloth):
    """Test that get_alert_rules consolidates rules from multiple files."""
    sloth._container.exists.return_value = True

    # Mock file listing
    file1 = MagicMock()
    file1.name = "service1.yaml"
    file2 = MagicMock()
    file2.name = "service2.yaml"
    sloth._container.list_files.return_value = [file1, file2]

    # Mock file content
    rules1 = {
        "groups": [
            {"name": "group1", "rules": [{"alert": "Alert1"}]}
        ]
    }
    rules2 = {
        "groups": [
            {"name": "group2", "rules": [{"alert": "Alert2"}]}
        ]
    }

    def mock_pull(path):
        mock_file = MagicMock()
        if "service1" in path:
            mock_file.read.return_value = yaml.safe_dump(rules1)
        else:
            mock_file.read.return_value = yaml.safe_dump(rules2)
        return mock_file

    sloth._container.pull.side_effect = mock_pull

    result = sloth.get_alert_rules()

    assert "groups" in result
    assert len(result["groups"]) == 2
    assert result["groups"][0]["name"] == "group1"
    assert result["groups"][1]["name"] == "group2"


def test_get_alert_rules_skips_non_yaml_files(sloth):
    """Test that get_alert_rules skips non-YAML files."""
    sloth._container.exists.return_value = True

    # Mock file listing with a non-yaml file
    file1 = MagicMock()
    file1.name = "service1.yaml"
    file2 = MagicMock()
    file2.name = "readme.txt"
    sloth._container.list_files.return_value = [file1, file2]

    rules = {"groups": [{"name": "test"}]}
    mock_file = MagicMock()
    mock_file.read.return_value = yaml.safe_dump(rules)
    sloth._container.pull.return_value = mock_file

    _ = sloth.get_alert_rules()

    # Should only pull the yaml file
    assert sloth._container.pull.call_count == 1
    assert "service1" in sloth._container.pull.call_args[0][0]


def test_reconcile_slo_period_windows_not_configured(sloth):
    """Test that no period windows are written when not configured."""
    sloth._slo_period_windows = ""

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write files
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_configured(sloth):
    """Test that custom period windows are written when configured."""
    custom_windows = """apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 8
      shortWindow: 5m
      longWindow: 1h
    slow:
      errorBudgetPercent: 12.5
      shortWindow: 30m
      longWindow: 6h
  ticket:
    quick:
      errorBudgetPercent: 20
      shortWindow: 2h
      longWindow: 1d
    slow:
      errorBudgetPercent: 42
      shortWindow: 6h
      longWindow: 3d
"""

    sloth._slo_period_windows = custom_windows
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should create directory (after validation)
    sloth._container.make_dir.assert_called_with(SLO_PERIOD_WINDOWS_DIR, make_parents=True)

    # Should write the config file
    sloth._container.push.assert_called_once()
    push_args = sloth._container.push.call_args[0]
    assert SLO_PERIOD_WINDOWS_DIR in push_args[0]
    assert push_args[1] == custom_windows


def test_reconcile_slo_period_windows_invalid_yaml(sloth):
    """Test that invalid YAML is not written and logs an error."""
    invalid_yaml = "invalid: yaml: {{{"

    sloth._slo_period_windows = invalid_yaml
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when YAML is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_generate_rules_with_default_period(sloth):
    """Test that sloth generate is called with default period."""
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("generated rules", "")
    sloth._container.exec.return_value = mock_process
    sloth._slo_period = "30d"
    sloth._slo_period_windows = ""

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/test.yaml")

    # Verify sloth generate was called with default period
    sloth._container.exec.assert_called_once()
    args = sloth._container.exec.call_args[0][0]
    assert "generate" in args
    assert "--default-slo-period" in args
    assert "30d" in args


def test_generate_rules_with_custom_period_windows(sloth):
    """Test that sloth generate is called with custom period windows path."""
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("generated rules", "")
    sloth._container.exec.return_value = mock_process
    sloth._slo_period = "7d"
    sloth._slo_period_windows = "custom yaml config"
    sloth._container.exists.return_value = True

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/test.yaml")

    # Verify sloth generate was called with period windows path
    sloth._container.exec.assert_called_once()
    args = sloth._container.exec.call_args[0][0]
    assert "generate" in args
    assert "--default-slo-period" in args
    assert "7d" in args
    assert "--slo-period-windows-path" in args
    assert SLO_PERIOD_WINDOWS_DIR in args


def test_generate_rules_without_custom_period_windows(sloth):
    """Test that sloth generate is not called with period windows path when not configured."""
    mock_process = MagicMock()
    mock_process.wait_output.return_value = ("generated rules", "")
    sloth._container.exec.return_value = mock_process
    sloth._slo_period = "30d"
    sloth._slo_period_windows = ""

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/test.yaml")

    # Verify sloth generate was called without period windows path
    sloth._container.exec.assert_called_once()
    args = sloth._container.exec.call_args[0][0]
    assert "generate" in args
    assert "--slo-period-windows-path" not in args


def test_reconcile_slo_period_windows_invalid_spec_missing_fields(sloth):
    """Test that incomplete AlertWindows spec is rejected."""
    # Missing 'ticket' field
    incomplete_spec = """apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 8
      shortWindow: 5m
      longWindow: 1h
"""

    sloth._slo_period_windows = incomplete_spec
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when spec is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_invalid_kind(sloth):
    """Test that AlertWindows with wrong kind is rejected."""
    wrong_kind = """apiVersion: sloth.slok.dev/v1
kind: WrongKind
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 8
      shortWindow: 5m
      longWindow: 1h
    slow:
      errorBudgetPercent: 12.5
      shortWindow: 30m
      longWindow: 6h
  ticket:
    quick:
      errorBudgetPercent: 20
      shortWindow: 2h
      longWindow: 1d
    slow:
      errorBudgetPercent: 42
      shortWindow: 6h
      longWindow: 3d
"""

    sloth._slo_period_windows = wrong_kind
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when kind is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_invalid_api_version(sloth):
    """Test that AlertWindows with wrong apiVersion is rejected."""
    wrong_api_version = """apiVersion: wrong/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 8
      shortWindow: 5m
      longWindow: 1h
    slow:
      errorBudgetPercent: 12.5
      shortWindow: 30m
      longWindow: 6h
  ticket:
    quick:
      errorBudgetPercent: 20
      shortWindow: 2h
      longWindow: 1d
    slow:
      errorBudgetPercent: 42
      shortWindow: 6h
      longWindow: 3d
"""

    sloth._slo_period_windows = wrong_api_version
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when apiVersion is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_invalid_duration_format(sloth):
    """Test that AlertWindows with invalid duration format is rejected."""
    invalid_duration = """apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 8
      shortWindow: invalid
      longWindow: 1h
    slow:
      errorBudgetPercent: 12.5
      shortWindow: 30m
      longWindow: 6h
  ticket:
    quick:
      errorBudgetPercent: 20
      shortWindow: 2h
      longWindow: 1d
    slow:
      errorBudgetPercent: 42
      shortWindow: 6h
      longWindow: 3d
"""

    sloth._slo_period_windows = invalid_duration
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when duration format is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_invalid_error_budget_percent(sloth):
    """Test that AlertWindows with invalid errorBudgetPercent is rejected."""
    invalid_percent = """apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
  page:
    quick:
      errorBudgetPercent: 150
      shortWindow: 5m
      longWindow: 1h
    slow:
      errorBudgetPercent: 12.5
      shortWindow: 30m
      longWindow: 6h
  ticket:
    quick:
      errorBudgetPercent: 20
      shortWindow: 2h
      longWindow: 1d
    slow:
      errorBudgetPercent: 42
      shortWindow: 6h
      longWindow: 3d
"""

    sloth._slo_period_windows = invalid_percent
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should not create directory or write the file when errorBudgetPercent is invalid
    assert not sloth._container.make_dir.called
    assert not sloth._container.push.called


def test_is_config_valid_with_default_30d():
    """Test that config is valid with default 30d period."""
    container_mock = MagicMock()
    sloth = Sloth(container=container_mock, slo_period="30d", slo_period_windows="")

    is_valid, error_msg = sloth.is_config_valid()

    assert is_valid
    assert error_msg == ""


def test_is_config_valid_with_28d():
    """Test that config is valid with 28d period (has built-in defaults)."""
    container_mock = MagicMock()
    sloth = Sloth(container=container_mock, slo_period="28d", slo_period_windows="")

    is_valid, error_msg = sloth.is_config_valid()

    assert is_valid
    assert error_msg == ""


def test_is_config_valid_with_7d_no_windows():
    """Test that config is invalid with 7d period and no custom windows."""
    container_mock = MagicMock()
    sloth = Sloth(container=container_mock, slo_period="7d", slo_period_windows="")

    is_valid, error_msg = sloth.is_config_valid()

    assert not is_valid
    assert "7d" in error_msg
    assert "slo-period-windows" in error_msg


def test_is_config_valid_with_7d_and_windows():
    """Test that config is valid with 7d period and custom windows."""
    container_mock = MagicMock()
    custom_windows = """apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 7d
"""
    sloth = Sloth(container=container_mock, slo_period="7d", slo_period_windows=custom_windows)

    is_valid, error_msg = sloth.is_config_valid()

    assert is_valid
    assert error_msg == ""


def test_reconcile_slo_period_windows_cleanup_when_removed(sloth):
    """Test that custom windows file is removed when config is cleared."""
    sloth._slo_period_windows = ""
    sloth._container.exists.return_value = True

    sloth._reconcile_slo_period_windows()

    # Should check if file exists
    sloth._container.exists.assert_called_with(f"{SLO_PERIOD_WINDOWS_DIR}/custom-period.yaml")

    # Should remove the file
    sloth._container.remove_path.assert_called_once_with(
        f"{SLO_PERIOD_WINDOWS_DIR}/custom-period.yaml"
    )

    # Should not push any new content
    assert not sloth._container.push.called


def test_reconcile_slo_period_windows_no_cleanup_when_not_exists(sloth):
    """Test that no cleanup happens when file doesn't exist."""
    sloth._slo_period_windows = ""
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_period_windows()

    # Should check if file exists
    sloth._container.exists.assert_called_with(f"{SLO_PERIOD_WINDOWS_DIR}/custom-period.yaml")

    # Should not try to remove file
    assert not sloth._container.remove_path.called

    # Should not push any new content
    assert not sloth._container.push.called


def test_generate_rules_validation_failure_logs_warning(sloth, caplog):
    """Test that SLO validation failures log a warning with service name and error."""
    import logging

    import ops.pebble

    # Set up logging capture at WARNING level
    caplog.set_level(logging.WARNING)

    # Mock exec to raise ExecError (validation failure)
    error_message = "error validating SLO spec: invalid field 'objective'"
    exec_error = ops.pebble.ExecError(
        command=["sloth", "generate"],
        exit_code=1,
        stdout="",
        stderr=error_message,
    )
    sloth._container.exec.side_effect = exec_error

    # Call the method with a service-specific path
    service_name = "my-app"
    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/{service_name}.yaml")

    # Verify warning was logged with correct format
    warning_messages = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert len(warning_messages) == 1, f"Expected 1 warning, got: {warning_messages}"

    warning_msg = warning_messages[0]
    assert "SLO validation failed" in warning_msg, "Should mention validation failure"
    assert f"service '{service_name}'" in warning_msg, "Should include service name"
    assert error_message in warning_msg, "Should include sloth error message"


def test_generate_rules_validation_failure_without_stderr(sloth, caplog):
    """Test that SLO validation failures without stderr still log a warning."""
    import logging

    import ops.pebble

    caplog.set_level(logging.WARNING)

    # Mock exec to raise ExecError without stderr attribute
    exec_error = ops.pebble.ExecError(
        command=["sloth", "generate"],
        exit_code=1,
        stdout="",
        stderr="",
    )
    sloth._container.exec.side_effect = exec_error

    service_name = "test-service"
    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/{service_name}.yaml")

    # Verify warning was logged
    warning_messages = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert len(warning_messages) == 1

    warning_msg = warning_messages[0]
    assert "SLO validation failed" in warning_msg
    assert f"service '{service_name}'" in warning_msg


def test_reconcile_additional_slos_validation_failure(sloth, caplog):
    """Test that validation failures during SLO reconciliation are properly logged."""
    import logging

    import ops.pebble

    caplog.set_level(logging.WARNING)

    additional_slo = {
        "version": "prometheus/v1",
        "service": "invalid-slo-app",
        "slos": [{"name": "test", "objective": "invalid"}],  # Invalid objective
    }

    sloth._container.exists.return_value = False

    # Mock exec to fail (validation error)
    error_message = "objective must be a number between 0 and 100"
    exec_error = ops.pebble.ExecError(
        command=["sloth", "generate"],
        exit_code=1,
        stdout="",
        stderr=error_message,
    )
    sloth._container.exec.side_effect = exec_error

    sloth._reconcile_additional_slos([additional_slo])

    # Verify warning was logged with service name
    warning_messages = [record.message for record in caplog.records if record.levelname == "WARNING"]
    assert any("SLO validation failed" in msg for msg in warning_messages)
    assert any("invalid-slo-app" in msg for msg in warning_messages)
    assert any(error_message in msg for msg in warning_messages)


def test_validate_generated_rules_with_valid_slos(sloth):
    """Test rule validation when all SLOs generate the expected number of rules."""
    # Simulate 2 SLO specs, each with 1 SLO definition
    slo_specs = [
        {
            "version": "prometheus/v1",
            "service": "app1",
            "slos": [{"name": "availability", "objective": 99.9}],
        },
        {
            "version": "prometheus/v1",
            "service": "app2",
            "slos": [{"name": "latency", "objective": 99.5}],
        },
    ]

    # Each SLO generates 17 rules (2 alerts + 7 meta + 8 sli)
    # 2 SLOs × 17 rules = 34 expected rules
    sloth._container.exists.return_value = True

    file1 = MagicMock()
    file1.name = "app1.yaml"
    file2 = MagicMock()
    file2.name = "app2.yaml"
    sloth._container.list_files.return_value = [file1, file2]

    # Each file contains 17 rules for one SLO
    rules_app1 = {
        "groups": [
            {"name": "alerts", "rules": [{}, {}]},  # 2 rules
            {"name": "meta", "rules": [{}, {}, {}, {}, {}, {}, {}]},  # 7 rules
            {"name": "sli", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},  # 8 rules
        ]
    }
    rules_app2 = {
        "groups": [
            {"name": "alerts", "rules": [{}, {}]},  # 2 rules
            {"name": "meta", "rules": [{}, {}, {}, {}, {}, {}, {}]},  # 7 rules
            {"name": "sli", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},  # 8 rules
        ]
    }

    def mock_pull(path):
        mock_file = MagicMock()
        if "app1" in path:
            mock_file.read.return_value = yaml.safe_dump(rules_app1)
        else:
            mock_file.read.return_value = yaml.safe_dump(rules_app2)
        return mock_file

    sloth._container.pull.side_effect = mock_pull

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert is_valid, "Validation should pass when all rules are generated"
    assert error_msg == "", "Error message should be empty when valid"
    assert expected_count == 34, "Expected 2 SLOs × 17 rules = 34"
    assert actual_count == 34, "Should count 34 actual rules"


def test_validate_generated_rules_with_invalid_slos(sloth):
    """Test rule validation when some SLOs fail to generate rules."""
    # 3 SLO specs, each with 1 SLO definition
    slo_specs = [
        {"version": "prometheus/v1", "service": "app1", "slos": [{"name": "test1"}]},
        {"version": "prometheus/v1", "service": "app2", "slos": [{"name": "test2"}]},
        {"version": "prometheus/v1", "service": "app3", "slos": [{"name": "test3"}]},
    ]

    # Expected: 3 SLOs × 17 rules = 51 rules
    # Actual: Only app1 and app2 generated rules (app3 failed validation)
    sloth._container.exists.return_value = True

    file1 = MagicMock()
    file1.name = "app1.yaml"
    file2 = MagicMock()
    file2.name = "app2.yaml"
    # app3.yaml is missing (validation failed, no rules generated)
    sloth._container.list_files.return_value = [file1, file2]

    rules_per_slo = {
        "groups": [
            {"name": "alerts", "rules": [{}, {}]},  # 2 rules
            {"name": "meta", "rules": [{}, {}, {}, {}, {}, {}, {}]},  # 7 rules
            {"name": "sli", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},  # 8 rules
        ]
    }

    mock_file = MagicMock()
    mock_file.read.return_value = yaml.safe_dump(rules_per_slo)
    sloth._container.pull.return_value = mock_file

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert not is_valid, "Validation should fail when rules are missing"
    assert "incomplete" in error_msg.lower(), "Error should mention incomplete generation"
    assert "51" in error_msg, "Error should show expected count (51)"
    assert "34" in error_msg, "Error should show actual count (34)"
    assert "1 SLO failed" in error_msg, "Error should mention 1 failed SLO"
    assert expected_count == 51
    assert actual_count == 34


def test_validate_generated_rules_with_multiple_slos_per_service(sloth):
    """Test rule validation when a service has multiple SLO definitions."""
    # 1 service with 3 SLO definitions
    slo_specs = [
        {
            "version": "prometheus/v1",
            "service": "multi-app",
            "slos": [
                {"name": "availability"},
                {"name": "latency"},
                {"name": "errors"},
            ],
        }
    ]

    # Expected: 3 SLOs × 17 rules = 51 rules
    sloth._container.exists.return_value = True

    file1 = MagicMock()
    file1.name = "multi-app.yaml"
    sloth._container.list_files.return_value = [file1]

    # All 3 SLOs in one file, each generates 17 rules = 51 total
    rules_multi = {
        "groups": [
            # First SLO (availability)
            {"name": "alerts1", "rules": [{}, {}]},
            {"name": "meta1", "rules": [{}, {}, {}, {}, {}, {}, {}]},
            {"name": "sli1", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},
            # Second SLO (latency)
            {"name": "alerts2", "rules": [{}, {}]},
            {"name": "meta2", "rules": [{}, {}, {}, {}, {}, {}, {}]},
            {"name": "sli2", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},
            # Third SLO (errors)
            {"name": "alerts3", "rules": [{}, {}]},
            {"name": "meta3", "rules": [{}, {}, {}, {}, {}, {}, {}]},
            {"name": "sli3", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},
        ]
    }

    mock_file = MagicMock()
    mock_file.read.return_value = yaml.safe_dump(rules_multi)
    sloth._container.pull.return_value = mock_file

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert is_valid, "Validation should pass with multiple SLOs in one service"
    assert error_msg == ""
    assert expected_count == 51
    assert actual_count == 51


def test_validate_generated_rules_partial_failure(sloth):
    """Test rule validation with partial SLO failure (some succeed, some fail)."""
    # 5 SLO specs
    slo_specs = [
        {"version": "prometheus/v1", "service": f"app{i}", "slos": [{"name": "test"}]}
        for i in range(1, 6)
    ]

    # Expected: 5 SLOs × 17 rules = 85 rules
    # Actual: 3 succeeded (app1, app2, app3), 2 failed (app4, app5)
    sloth._container.exists.return_value = True

    files = [MagicMock(name=f"app{i}.yaml") for i in range(1, 4)]  # Only 3 files
    sloth._container.list_files.return_value = files

    rules_per_slo = {
        "groups": [
            {"name": "alerts", "rules": [{}, {}]},
            {"name": "meta", "rules": [{}, {}, {}, {}, {}, {}, {}]},
            {"name": "sli", "rules": [{}, {}, {}, {}, {}, {}, {}, {}]},
        ]
    }

    mock_file = MagicMock()
    mock_file.read.return_value = yaml.safe_dump(rules_per_slo)
    sloth._container.pull.return_value = mock_file

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert not is_valid
    assert "2 SLOs failed" in error_msg, "Error should mention 2 failed SLOs"
    assert expected_count == 85
    assert actual_count == 51  # 3 × 17


def test_validate_generated_rules_no_slos(sloth):
    """Test rule validation when no SLOs are provided."""
    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(None)

    assert is_valid, "Should be valid when no SLOs are provided"
    assert error_msg == ""
    assert expected_count == 0
    assert actual_count == 0


def test_validate_generated_rules_empty_slos(sloth):
    """Test rule validation when SLO specs list is empty."""
    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules([])

    assert is_valid, "Should be valid when SLO list is empty"
    assert error_msg == ""
    assert expected_count == 0
    assert actual_count == 0


def test_validate_generated_rules_container_not_connected(sloth):
    """Test rule validation when container is not connected."""
    sloth._container.can_connect.return_value = False

    slo_specs = [
        {"version": "prometheus/v1", "service": "app1", "slos": [{"name": "test"}]}
    ]

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert is_valid, "Should return valid when container is not connected"
    assert error_msg == ""
    assert expected_count == 0
    assert actual_count == 0


def test_validate_generated_rules_no_rules_directory(sloth):
    """Test rule validation when rules directory doesn't exist."""
    slo_specs = [
        {"version": "prometheus/v1", "service": "app1", "slos": [{"name": "test"}]}
    ]

    sloth._container.exists.return_value = False

    is_valid, error_msg, expected_count, actual_count = sloth.validate_generated_rules(slo_specs)

    assert not is_valid, "Should be invalid when no rules are generated"
    assert "1 SLO failed" in error_msg
    assert expected_count == 17
    assert actual_count == 0




def test_generate_rules_deletes_stale_output_before_generating(sloth):
    """Test that existing output file is removed before running sloth generate.

    This ensures a failed generation leaves no stale rules behind so that
    validate_generated_rules correctly detects the mismatch.
    """
    import ops.pebble

    service_name = "my-app"
    output_path = f"{GENERATED_RULES_DIR}/{service_name}.yaml"

    # Simulate stale output file existing
    sloth._container.exists.return_value = True

    error = ops.pebble.ExecError(
        command=["sloth", "generate"], exit_code=1, stdout="", stderr="invalid objective"
    )
    sloth._container.exec.side_effect = error

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/{service_name}.yaml")

    # Verify stale file was removed before attempting generation
    sloth._container.remove_path.assert_called_once_with(output_path)
    # And no new rules file was written (generation failed)
    push_calls = [c for c in sloth._container.push.call_args_list if GENERATED_RULES_DIR in str(c)]
    assert not push_calls, "Rules file should not be written on generation failure"


def test_generate_rules_clears_stale_file_on_success_then_rewrites(sloth):
    """Test that the stale file is removed and then rewritten on successful generation."""
    service_name = "my-app"
    output_path = f"{GENERATED_RULES_DIR}/{service_name}.yaml"

    sloth._container.exists.return_value = True

    exec_mock = MagicMock()
    exec_mock.wait_output.return_value = ("new rules content", "")
    sloth._container.exec.return_value = exec_mock

    sloth._generate_rules_from_slo(f"{SLO_SPECS_DIR}/{service_name}.yaml")

    sloth._container.remove_path.assert_called_once_with(output_path)
    push_calls = [c for c in sloth._container.push.call_args_list if GENERATED_RULES_DIR in str(c)]
    assert len(push_calls) == 1
    assert push_calls[0][0][1] == "new rules content"


def test_reconcile_regenerates_rules_when_output_file_missing(sloth):
    """Test that rules are regenerated when the output file is absent but spec is unchanged.

    This covers the pod-restart scenario: the spec file exists with the same content
    but the rules output file is gone, so generation must be re-triggered.
    """
    service_name = "my-app"
    slo_spec = {
        "version": "prometheus/v1",
        "service": service_name,
        "slos": [{"name": "requests-availability", "objective": 99.9}],
    }
    slo_yaml = yaml.safe_dump(slo_spec, default_flow_style=False)

    def exists_side_effect(path):
        # Spec file exists (with unchanged content); rules output file does NOT exist
        if path == f"{SLO_SPECS_DIR}/{service_name}.yaml":
            return True
        return False

    sloth._container.exists.side_effect = exists_side_effect
    # pull() returns the same spec content as the new spec → no content change
    pull_mock = MagicMock()
    pull_mock.read.return_value = slo_yaml
    sloth._container.pull.return_value = pull_mock

    exec_mock = MagicMock()
    exec_mock.wait_output.return_value = ("generated rules", "")
    sloth._container.exec.return_value = exec_mock

    sloth._reconcile_additional_slos([slo_spec])

    # Even though spec content is identical, rules must be regenerated because output is missing
    assert sloth._container.exec.called, "sloth generate should run when output file is missing"
    exec_args = sloth._container.exec.call_args[0][0]
    assert "generate" in exec_args
