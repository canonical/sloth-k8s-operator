# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Unit tests for AlertWindows models."""

import pytest
from pydantic import ValidationError

from alert_windows_models import AlertWindows, Spec, Window


def test_window_valid():
    """Test valid Window creation."""
    window = Window(
        errorBudgetPercent=10.0,
        shortWindow="5m",
        longWindow="1h",
    )
    assert window.error_budget_percent == 10.0
    assert window.short_window == "5m"
    assert window.long_window == "1h"


def test_window_invalid_duration():
    """Test Window with invalid duration format."""
    with pytest.raises(ValidationError) as exc_info:
        Window(
            errorBudgetPercent=10.0,
            shortWindow="invalid",
            longWindow="1h",
        )
    assert "Invalid duration format" in str(exc_info.value)


def test_window_invalid_error_budget_percent():
    """Test Window with invalid error budget percent."""
    with pytest.raises(ValidationError) as exc_info:
        Window(
            errorBudgetPercent=150.0,  # Over 100%
            shortWindow="5m",
            longWindow="1h",
        )
    assert "less than or equal to 100" in str(exc_info.value)


def test_window_negative_error_budget_percent():
    """Test Window with negative error budget percent."""
    with pytest.raises(ValidationError) as exc_info:
        Window(
            errorBudgetPercent=-5.0,
            shortWindow="5m",
            longWindow="1h",
        )
    assert "greater than 0" in str(exc_info.value)


def test_alert_windows_valid():
    """Test valid AlertWindows creation from dict."""
    data = {
        "apiVersion": "sloth.slok.dev/v1",
        "kind": "AlertWindows",
        "spec": {
            "sloPeriod": "7d",
            "page": {
                "quick": {
                    "errorBudgetPercent": 8,
                    "shortWindow": "5m",
                    "longWindow": "1h",
                },
                "slow": {
                    "errorBudgetPercent": 12.5,
                    "shortWindow": "30m",
                    "longWindow": "6h",
                },
            },
            "ticket": {
                "quick": {
                    "errorBudgetPercent": 20,
                    "shortWindow": "2h",
                    "longWindow": "1d",
                },
                "slow": {
                    "errorBudgetPercent": 42,
                    "shortWindow": "6h",
                    "longWindow": "3d",
                },
            },
        },
    }

    alert_windows = AlertWindows.model_validate(data)
    assert alert_windows.kind == "AlertWindows"
    assert alert_windows.api_version == "sloth.slok.dev/v1"
    assert alert_windows.spec.slo_period == "7d"


def test_alert_windows_invalid_kind():
    """Test AlertWindows with invalid kind."""
    data = {
        "apiVersion": "sloth.slok.dev/v1",
        "kind": "WrongKind",
        "spec": {
            "sloPeriod": "7d",
            "page": {
                "quick": {"errorBudgetPercent": 8, "shortWindow": "5m", "longWindow": "1h"},
                "slow": {"errorBudgetPercent": 12.5, "shortWindow": "30m", "longWindow": "6h"},
            },
            "ticket": {
                "quick": {"errorBudgetPercent": 20, "shortWindow": "2h", "longWindow": "1d"},
                "slow": {"errorBudgetPercent": 42, "shortWindow": "6h", "longWindow": "3d"},
            },
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        AlertWindows.model_validate(data)
    assert "Invalid kind" in str(exc_info.value)


def test_alert_windows_invalid_api_version():
    """Test AlertWindows with invalid apiVersion."""
    data = {
        "apiVersion": "wrong/v1",
        "kind": "AlertWindows",
        "spec": {
            "sloPeriod": "7d",
            "page": {
                "quick": {"errorBudgetPercent": 8, "shortWindow": "5m", "longWindow": "1h"},
                "slow": {"errorBudgetPercent": 12.5, "shortWindow": "30m", "longWindow": "6h"},
            },
            "ticket": {
                "quick": {"errorBudgetPercent": 20, "shortWindow": "2h", "longWindow": "1d"},
                "slow": {"errorBudgetPercent": 42, "shortWindow": "6h", "longWindow": "3d"},
            },
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        AlertWindows.model_validate(data)
    assert "Invalid apiVersion" in str(exc_info.value)


def test_alert_windows_missing_required_field():
    """Test AlertWindows with missing required field."""
    data = {
        "apiVersion": "sloth.slok.dev/v1",
        "kind": "AlertWindows",
        "spec": {
            "sloPeriod": "7d",
            "page": {
                "quick": {"errorBudgetPercent": 8, "shortWindow": "5m", "longWindow": "1h"},
                "slow": {"errorBudgetPercent": 12.5, "shortWindow": "30m", "longWindow": "6h"},
            },
            # Missing 'ticket' field
        },
    }

    with pytest.raises(ValidationError) as exc_info:
        AlertWindows.model_validate(data)
    assert "ticket" in str(exc_info.value).lower()


def test_spec_invalid_slo_period():
    """Test Spec with invalid sloPeriod format."""
    with pytest.raises(ValidationError) as exc_info:
        Spec(
            sloPeriod="invalid",
            page={
                "quick": {"errorBudgetPercent": 8, "shortWindow": "5m", "longWindow": "1h"},
                "slow": {"errorBudgetPercent": 12.5, "shortWindow": "30m", "longWindow": "6h"},
            },
            ticket={
                "quick": {"errorBudgetPercent": 20, "shortWindow": "2h", "longWindow": "1d"},
                "slow": {"errorBudgetPercent": 42, "shortWindow": "6h", "longWindow": "3d"},
            },
        )
    assert "Invalid SLO period format" in str(exc_info.value)


def test_duration_formats():
    """Test various valid duration formats."""
    valid_durations = ["5s", "30s", "5m", "30m", "1h", "6h", "1d", "7d", "4w", "1y"]

    for duration in valid_durations:
        window = Window(
            errorBudgetPercent=10.0,
            shortWindow=duration,
            longWindow=duration,
        )
        assert window.short_window == duration
        assert window.long_window == duration


def test_invalid_duration_formats():
    """Test various invalid duration formats."""
    invalid_durations = ["", "5", "m", "5x", "5 m", "5.5.5m", "abc"]

    for duration in invalid_durations:
        with pytest.raises(ValidationError):
            Window(
                errorBudgetPercent=10.0,
                shortWindow=duration,
                longWindow="1h",
            )
