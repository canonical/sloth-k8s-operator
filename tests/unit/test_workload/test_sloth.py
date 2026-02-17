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
                "command": f"/usr/local/bin/sloth serve --listen=localhost:{Sloth.port} --default-slo-period=30d",
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


def test_reconcile_slo_specs_writes_prometheus_slo(sloth):
    """Test that reconcile writes the Prometheus SLO spec."""
    sloth._container.exists.return_value = False

    sloth._reconcile_slo_specs()

    # Check that push was called with the SLO spec
    push_calls = list(sloth._container.push.call_args_list)
    slo_written = False
    for call_args in push_calls:
        path = call_args[0][0]
        if path.endswith("prometheus-availability.yaml"):
            slo_written = True
            content = call_args[0][1]
            # Parse YAML to verify it's valid
            slo_data = yaml.safe_load(content)
            assert slo_data["service"] == "prometheus"
            break

    assert slo_written, "Prometheus SLO spec was not written"


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


def test_get_prometheus_availability_slo(sloth):
    """Test that the hardcoded SLO is properly formatted."""
    slo_yaml = sloth._get_prometheus_availability_slo()
    slo = yaml.safe_load(slo_yaml)

    assert slo["version"] == "prometheus/v1"
    assert slo["service"] == "prometheus"
    assert len(slo["slos"]) == 1

    slo_spec = slo["slos"][0]
    assert slo_spec["name"] == "requests-availability"
    assert slo_spec["objective"] == 99.0
    assert "error_query" in slo_spec["sli"]["events"]
    assert "total_query" in slo_spec["sli"]["events"]
    assert "alerting" in slo_spec


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
    file1.name = "prometheus-availability.yaml"
    file2 = MagicMock()
    file2.name = "another-slo.yaml"
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
        if "prometheus-availability" in path:
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
    file1.name = "prometheus-availability.yaml"
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
    assert "prometheus-availability" in sloth._container.pull.call_args[0][0]


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
    assert "30d" in error_msg
    assert "28d" in error_msg


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

