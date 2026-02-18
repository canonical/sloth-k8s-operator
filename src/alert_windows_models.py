# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Pydantic models for Sloth AlertWindows specification validation.

These models map to the AlertWindows spec from:
https://github.com/slok/sloth/tree/main/pkg/prometheus/alertwindows/v1
"""

import re

from pydantic import BaseModel, Field, field_validator


class Window(BaseModel):
    """Configuration for a single alert window.

    Defines the error budget consumption thresholds and time windows
    for triggering alerts.
    """

    error_budget_percent: float = Field(
        ...,
        alias="errorBudgetPercent",
        description="Max error budget consumption allowed in the window",
        gt=0,
        le=100,
    )
    short_window: str = Field(
        ...,
        alias="shortWindow",
        description="Window that stops alerts when error is gone (e.g., '5m', '30m')",
    )
    long_window: str = Field(
        ...,
        alias="longWindow",
        description="Window for measuring overall error budget consumption (e.g., '1h', '6h')",
    )

    @field_validator("short_window", "long_window")
    @classmethod
    def validate_duration(cls, v: str) -> str:
        """Validate that duration strings follow Prometheus format.

        Valid formats: <number><unit> where unit is one of: s, m, h, d, w, y
        Examples: 5m, 1h, 30s, 2d
        """
        if not v:
            raise ValueError("Duration cannot be empty")

        # Check if it matches the pattern: number + time unit
        pattern = r"^\d+(\.\d+)?[smhdwy]$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid duration format: {v}. "
                "Must be <number><unit> where unit is s, m, h, d, w, or y (e.g., '5m', '1h')"
            )
        return v


class QuickSlowWindow(BaseModel):
    """Configuration for quick and slow alert windows.

    Quick alerts fire when significant error budget is consumed quickly.
    Slow alerts fire when error budget is consumed over a longer period.
    """

    quick: Window = Field(..., description="Windows for quick alerting trigger")
    slow: Window = Field(..., description="Windows for slow alerting trigger")


class PageWindow(QuickSlowWindow):
    """Configuration for page alerting windows.

    Page alerts are high-priority alerts that typically require immediate attention.

    Note: This class is a separate type from QuickSlowWindow to match the Sloth
    AlertWindows spec structure, providing type clarity in the YAML configuration.
    See: https://github.com/slok/sloth/blob/main/pkg/prometheus/alertwindows/v1/v1.go
    """

    pass


class TicketWindow(QuickSlowWindow):
    """Configuration for ticket alerting windows.

    Ticket alerts are lower-priority alerts that typically create tracking tickets.

    Note: This class is a separate type from QuickSlowWindow to match the Sloth
    AlertWindows spec structure, providing type clarity in the YAML configuration.
    See: https://github.com/slok/sloth/blob/main/pkg/prometheus/alertwindows/v1/v1.go
    """

    pass


class Spec(BaseModel):
    """Root specification for AlertWindows configuration."""

    slo_period: str = Field(
        ...,
        alias="sloPeriod",
        description="The full SLO period used for this windows (e.g., '30d', '7d')",
    )
    page: PageWindow = Field(..., description="Configuration for page alerting windows")
    ticket: TicketWindow = Field(..., description="Configuration for ticket alerting windows")

    @field_validator("slo_period")
    @classmethod
    def validate_slo_period(cls, v: str) -> str:
        """Validate SLO period format."""
        if not v:
            raise ValueError("SLO period cannot be empty")

        pattern = r"^\d+(\.\d+)?[smhdwy]$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid SLO period format: {v}. "
                "Must be <number><unit> where unit is s, m, h, d, w, or y (e.g., '30d', '7d')"
            )
        return v


class AlertWindows(BaseModel):
    """AlertWindows specification for Sloth.

    This model validates the complete AlertWindows YAML structure according to
    the Sloth specification (apiVersion: sloth.slok.dev/v1, kind: AlertWindows).
    """

    kind: str = Field(..., description="Resource kind, must be 'AlertWindows'")
    api_version: str = Field(
        ..., alias="apiVersion", description="API version, must be 'sloth.slok.dev/v1'"
    )
    spec: Spec = Field(..., description="AlertWindows specification")

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        """Validate that kind is 'AlertWindows'."""
        if v != "AlertWindows":
            raise ValueError(f"Invalid kind: {v}. Must be 'AlertWindows'")
        return v

    @field_validator("api_version")
    @classmethod
    def validate_api_version(cls, v: str) -> str:
        """Validate that apiVersion is 'sloth.slok.dev/v1'."""
        if v != "sloth.slok.dev/v1":
            raise ValueError(f"Invalid apiVersion: {v}. Must be 'sloth.slok.dev/v1'")
        return v

    model_config = {"populate_by_name": True}
