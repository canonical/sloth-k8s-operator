# Copyright 2025 Canonical
# See LICENSE file for licensing details.
from io import StringIO
from unittest.mock import MagicMock

import pytest
import yaml

from sloth import GENERATED_RULES_DIR, SLO_SPECS_DIR, Sloth


@pytest.fixture
def sloth():
    container_mock = MagicMock()
    container_mock.can_connect.return_value = True
    return Sloth(
        container=container_mock,
        slo_period="30d",
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



