# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Unit tests for the SLO library."""

import pytest
import yaml
from ops.charm import CharmBase
from ops.testing import Context, Relation, State
from pydantic import ValidationError

from lib.charms.sloth_k8s.v0.slo import (
    SLOProvider,
    SLORequirer,
    SLOSpec,
)


class ProviderCharm(CharmBase):
    """Test charm that provides SLOs."""

    def __init__(self, *args):
        super().__init__(*args)
        self.slo_provider = SLOProvider(self, relation_name="slos")


class RequirerCharm(CharmBase):
    """Test charm that requires SLOs."""

    def __init__(self, *args):
        super().__init__(*args)
        self.slo_requirer = SLORequirer(self, relation_name="slos")


# Test SLO specifications
VALID_SLO_SPEC = {
    "version": "prometheus/v1",
    "service": "test-service",
    "labels": {"team": "test-team"},
    "slos": [
        {
            "name": "requests-availability",
            "objective": 99.9,
            "description": "99.9% of requests should succeed",
            "sli": {
                "events": {
                    "error_query": 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))',
                    "total_query": "sum(rate(http_requests_total[{{.window}}]))",
                }
            },
            "alerting": {
                "name": "TestServiceHighErrorRate",
                "labels": {"severity": "critical"},
            },
        }
    ],
}

VALID_SLO_SPEC_2 = {
    "version": "prometheus/v1",
    "service": "another-service",
    "labels": {"team": "another-team"},
    "slos": [
        {
            "name": "latency",
            "objective": 95.0,
            "description": "95% of requests should be fast",
            "sli": {
                "events": {
                    "error_query": 'sum(rate(http_request_duration_seconds_bucket{le="0.5"}[{{.window}}]))',
                    "total_query": "sum(rate(http_request_duration_seconds_count[{{.window}}]))",
                }
            },
        }
    ],
}


class TestSLOSpec:
    """Tests for the SLOSpec pydantic model."""

    def test_valid_slo_spec(self):
        """Test that a valid SLO spec is accepted."""
        spec = SLOSpec(**VALID_SLO_SPEC)
        assert spec.version == "prometheus/v1"
        assert spec.service == "test-service"
        assert len(spec.slos) == 1

    def test_invalid_version_format(self):
        """Test that invalid version format is rejected."""
        invalid_spec = VALID_SLO_SPEC.copy()
        invalid_spec["version"] = "invalid"
        with pytest.raises(ValidationError):
            SLOSpec(**invalid_spec)

    def test_empty_slos_list(self):
        """Test that empty SLOs list is rejected."""
        invalid_spec = VALID_SLO_SPEC.copy()
        invalid_spec["slos"] = []
        with pytest.raises(ValidationError):
            SLOSpec(**invalid_spec)

    def test_missing_required_fields(self):
        """Test that missing required fields are rejected."""
        incomplete_spec = {"version": "prometheus/v1"}
        with pytest.raises(ValidationError):
            SLOSpec(**incomplete_spec)


class TestSLORequirer:
    """Tests for the SLORequirer class."""

    def test_get_slos_no_relations(self):
        """Test getting SLOs when no relations exist."""
        context = Context(
            RequirerCharm, meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}}
        )
        state = State()

        state_out = context.run(context.on.start(), state)
        # Create a new charm instance to test the requirer
        ctx2 = Context(
            RequirerCharm, meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}}
        )
        state2 = State()
        state_out2 = ctx2.run(ctx2.on.start(), state2)

        # Test via directly instantiating
        from unittest.mock import MagicMock

        mock_charm = MagicMock(spec=RequirerCharm)
        mock_charm.model.relations.get.return_value = []
        requirer = SLORequirer(mock_charm, relation_name="slos")
        slos = requirer.get_slos()
        assert slos == []

    def test_get_slos_with_valid_data(self):
        """Test getting SLOs from relation with valid data."""
        slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={0: {"slo_spec": slo_yaml}},
        )

        from unittest.mock import MagicMock

        mock_charm = MagicMock(spec=RequirerCharm)
        mock_charm.model.relations.get.return_value = [relation]

        # Mock the relation data access
        mock_unit = MagicMock()
        mock_unit.name = "provider/0"
        relation.units = [mock_unit]

        requirer = SLORequirer(mock_charm, relation_name="slos")
        slos = requirer.get_slos()
        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"
