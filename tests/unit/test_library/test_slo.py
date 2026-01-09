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
    SLOsChangedEvent,
    SLOSpec,
)

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

INVALID_SLO_SPEC_NO_VERSION = {
    "service": "test-service",
    "slos": [{"name": "test", "objective": 99.9}],
}

INVALID_SLO_SPEC_BAD_VERSION = {
    "version": "invalid",
    "service": "test-service",
    "slos": [{"name": "test", "objective": 99.9}],
}

INVALID_SLO_SPEC_EMPTY_SLOS = {
    "version": "prometheus/v1",
    "service": "test-service",
    "slos": [],
}


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
        self.slos_changed_events = []
        self.framework.observe(
            self.slo_requirer.on.slos_changed,
            self._on_slos_changed,
        )

    def _on_slos_changed(self, event):
        """Record slos_changed events for testing."""
        self.slos_changed_events.append(event)


class TestSLOSpec:
    """Tests for the SLOSpec pydantic model."""

    def test_valid_slo_spec(self):
        """Test that a valid SLO spec is accepted."""
        spec = SLOSpec(**VALID_SLO_SPEC)
        assert spec.version == "prometheus/v1"
        assert spec.service == "test-service"
        assert len(spec.slos) == 1
        assert spec.labels == {"team": "test-team"}

    def test_valid_slo_spec_without_labels(self):
        """Test that SLO spec without labels is accepted."""
        spec_no_labels = VALID_SLO_SPEC.copy()
        spec_no_labels.pop("labels")
        spec = SLOSpec(**spec_no_labels)
        assert spec.labels == {}

    def test_invalid_version_format(self):
        """Test that invalid version format is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SLOSpec(**INVALID_SLO_SPEC_BAD_VERSION)
        assert "Version must be in format" in str(exc_info.value)

    def test_missing_version(self):
        """Test that missing version is rejected."""
        with pytest.raises(ValidationError):
            SLOSpec(**INVALID_SLO_SPEC_NO_VERSION)

    def test_empty_slos_list(self):
        """Test that empty SLOs list is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SLOSpec(**INVALID_SLO_SPEC_EMPTY_SLOS)
        assert "At least one SLO must be defined" in str(exc_info.value)

    def test_missing_required_fields(self):
        """Test that missing required fields are rejected."""
        incomplete_spec = {"version": "prometheus/v1"}
        with pytest.raises(ValidationError):
            SLOSpec(**incomplete_spec)


class TestSLOProvider:
    """Tests for the SLOProvider class."""

    def test_provide_slo_with_relation(self):
        """Test providing an SLO spec when relation exists."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        # Trigger start and provide SLO
        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slo(VALID_SLO_SPEC)
            state_out = mgr.run()

        # Check that SLO was set in relation data
        relation_out = state_out.get_relation(slo_relation.id)
        slo_yaml = relation_out.local_unit_data.get("slo_spec")
        assert slo_yaml is not None
        slo_data = yaml.safe_load(slo_yaml)
        assert slo_data["service"] == "test-service"
        assert slo_data["version"] == "prometheus/v1"

    def test_provide_slo_without_relation(self):
        """Test providing an SLO spec when no relation exists."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        state = State()

        # Should not raise error, just log warning
        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slo(VALID_SLO_SPEC)
            _ = mgr.run()

    def test_provide_invalid_slo_spec(self):
        """Test that providing invalid SLO spec raises ValidationError."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            with pytest.raises(ValidationError):
                charm.slo_provider.provide_slo(INVALID_SLO_SPEC_BAD_VERSION)

    def test_provide_slo_to_multiple_relations(self):
        """Test providing SLO spec to multiple relations."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation_1 = Relation("slos")
        slo_relation_2 = Relation("slos")
        state = State(relations=[slo_relation_1, slo_relation_2])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slo(VALID_SLO_SPEC)
            state_out = mgr.run()

        # Both relations should have the SLO spec
        for rel in [slo_relation_1, slo_relation_2]:
            relation_out = state_out.get_relation(rel.id)
            slo_yaml = relation_out.local_unit_data.get("slo_spec")
            assert slo_yaml is not None
            slo_data = yaml.safe_load(slo_yaml)
            assert slo_data["service"] == "test-service"

    def test_provide_slos_with_multiple_specs(self):
        """Test providing multiple SLO specs at once."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slos([VALID_SLO_SPEC, VALID_SLO_SPEC_2])
            state_out = mgr.run()

        # Check that both SLOs were set in relation data as multi-document YAML
        relation_out = state_out.get_relation(slo_relation.id)
        slo_yaml = relation_out.local_unit_data.get("slo_spec")
        assert slo_yaml is not None

        # Parse multi-document YAML
        slo_docs = list(yaml.safe_load_all(slo_yaml))
        assert len(slo_docs) == 2

        services = {doc["service"] for doc in slo_docs}
        assert services == {"test-service", "another-service"}

    def test_provide_slos_with_empty_list(self):
        """Test that providing empty list logs warning."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        # Should not raise error, just log warning
        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slos([])
            state_out = mgr.run()

        # Relation data should be empty
        relation_out = state_out.get_relation(slo_relation.id)
        slo_yaml = relation_out.local_unit_data.get("slo_spec")
        assert slo_yaml is None

    def test_provide_slos_validates_all_specs(self):
        """Test that all specs are validated before providing."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            # One valid, one invalid - should raise ValidationError
            with pytest.raises(ValidationError):
                charm.slo_provider.provide_slos([VALID_SLO_SPEC, INVALID_SLO_SPEC_BAD_VERSION])

    def test_provide_slo_calls_provide_slos(self):
        """Test that provide_slo is a wrapper around provide_slos."""
        context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            charm.slo_provider.provide_slo(VALID_SLO_SPEC)
            state_out = mgr.run()

        # Should work the same as before
        relation_out = state_out.get_relation(slo_relation.id)
        slo_yaml = relation_out.local_unit_data.get("slo_spec")
        assert slo_yaml is not None
        slo_data = yaml.safe_load(slo_yaml)
        assert slo_data["service"] == "test-service"


class TestSLORequirer:
    """Tests for the SLORequirer class."""

    def test_get_slos_no_relations(self):
        """Test getting SLOs when no relations exist."""
        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State()

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        assert slos == []

    def test_get_slos_with_valid_data(self):
        """Test getting SLOs from relation with valid data."""
        slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={0: {"slo_spec": slo_yaml}},
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"
        assert slos[0]["version"] == "prometheus/v1"

    def test_get_slos_from_multiple_units(self):
        """Test getting SLOs from multiple units."""
        slo_yaml_1 = yaml.safe_dump(VALID_SLO_SPEC)
        slo_yaml_2 = yaml.safe_dump(VALID_SLO_SPEC_2)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={
                0: {"slo_spec": slo_yaml_1},
                1: {"slo_spec": slo_yaml_2},
            },
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        assert len(slos) == 2
        services = {slo["service"] for slo in slos}
        assert services == {"test-service", "another-service"}

    def test_get_slos_from_unit_with_multi_document_yaml(self):
        """Test getting multiple SLOs from a single unit (multi-document YAML)."""
        # Merge two specs into multi-document YAML (like provide_slos does)
        slo_yaml_multi = "---\n".join([
            yaml.safe_dump(VALID_SLO_SPEC, default_flow_style=False),
            yaml.safe_dump(VALID_SLO_SPEC_2, default_flow_style=False)
        ])

        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={0: {"slo_spec": slo_yaml_multi}},
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        # Should get both SLOs from the single unit
        assert len(slos) == 2
        services = {slo["service"] for slo in slos}
        assert services == {"test-service", "another-service"}

    def test_get_slos_from_multiple_relations(self):
        """Test getting SLOs from multiple relations."""
        slo_yaml_1 = yaml.safe_dump(VALID_SLO_SPEC)
        slo_yaml_2 = yaml.safe_dump(VALID_SLO_SPEC_2)
        slo_relation_1 = Relation(
            "slos",
            remote_app_name="provider1",
            remote_units_data={0: {"slo_spec": slo_yaml_1}},
        )
        slo_relation_2 = Relation(
            "slos",
            remote_app_name="provider2",
            remote_units_data={0: {"slo_spec": slo_yaml_2}},
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation_1, slo_relation_2])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        assert len(slos) == 2
        services = {slo["service"] for slo in slos}
        assert services == {"test-service", "another-service"}

    def test_get_slos_skips_invalid_data(self):
        """Test that invalid SLO specs are skipped."""
        valid_slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        invalid_slo_yaml = yaml.safe_dump(INVALID_SLO_SPEC_BAD_VERSION)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={
                0: {"slo_spec": valid_slo_yaml},
                1: {"slo_spec": invalid_slo_yaml},
            },
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        # Only valid SLO should be returned
        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"

    def test_get_slos_skips_malformed_yaml(self):
        """Test that malformed YAML is skipped."""
        valid_slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={
                0: {"slo_spec": valid_slo_yaml},
                1: {"slo_spec": "invalid: yaml: {{{"},
            },
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        # Only valid SLO should be returned
        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"

    def test_get_slos_skips_empty_data(self):
        """Test that empty SLO data is skipped."""
        valid_slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={
                0: {"slo_spec": valid_slo_yaml},
                1: {},  # No slo_spec key
                2: {"slo_spec": ""},  # Empty string
            },
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.start(), state) as mgr:
            charm = mgr.charm
            slos = charm.slo_requirer.get_slos()
            _ = mgr.run()

        # Only valid SLO should be returned
        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"

    def test_slos_changed_event_on_relation_joined(self):
        """Test that slos_changed event is emitted on relation-joined."""
        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        slo_relation = Relation("slos")
        state = State(relations=[slo_relation])

        with context(context.on.relation_joined(slo_relation), state) as mgr:
            charm = mgr.charm
            initial_events = len(charm.slos_changed_events)
            _ = mgr.run()
            final_events = len(charm.slos_changed_events)

        assert final_events == initial_events + 1
        assert isinstance(charm.slos_changed_events[-1], SLOsChangedEvent)

    def test_slos_changed_event_on_relation_changed(self):
        """Test that slos_changed event is emitted on relation-changed."""
        slo_yaml = yaml.safe_dump(VALID_SLO_SPEC)
        slo_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={0: {"slo_spec": slo_yaml}},
        )

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.relation_changed(slo_relation), state) as mgr:
            charm = mgr.charm
            initial_events = len(charm.slos_changed_events)
            _ = mgr.run()
            final_events = len(charm.slos_changed_events)

        assert final_events == initial_events + 1

    def test_slos_changed_event_on_relation_departed(self):
        """Test that slos_changed event is emitted on relation-departed."""
        slo_relation = Relation("slos")

        context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        state = State(relations=[slo_relation])

        with context(context.on.relation_departed(slo_relation), state) as mgr:
            charm = mgr.charm
            initial_events = len(charm.slos_changed_events)
            _ = mgr.run()
            final_events = len(charm.slos_changed_events)

        assert final_events == initial_events + 1


class TestSLOIntegration:
    """Integration tests for provider and requirer working together."""

    def test_full_lifecycle(self):
        """Test full lifecycle: provide SLO → relation → requirer gets SLO."""
        # Provider provides SLO
        provider_context = Context(
            ProviderCharm,
            meta={"name": "provider", "requires": {"slos": {"interface": "slo"}}},
        )
        provider_relation = Relation("slos")
        provider_state = State(relations=[provider_relation])

        with provider_context(provider_context.on.start(), provider_state) as mgr:
            provider_charm = mgr.charm
            provider_charm.slo_provider.provide_slo(VALID_SLO_SPEC)
            provider_state_out = mgr.run()

        # Get the relation data from provider
        provider_relation_out = provider_state_out.get_relation(provider_relation.id)
        slo_yaml = provider_relation_out.local_unit_data.get("slo_spec")

        # Requirer receives SLO
        requirer_context = Context(
            RequirerCharm,
            meta={"name": "requirer", "provides": {"slos": {"interface": "slo"}}},
        )
        requirer_relation = Relation(
            "slos",
            remote_app_name="provider",
            remote_units_data={0: {"slo_spec": slo_yaml}},
        )
        requirer_state = State(relations=[requirer_relation])

        with requirer_context(requirer_context.on.start(), requirer_state) as mgr:
            requirer_charm = mgr.charm
            slos = requirer_charm.slo_requirer.get_slos()
            _ = mgr.run()

        # Verify the SLO was successfully transmitted
        assert len(slos) == 1
        assert slos[0]["service"] == "test-service"
        assert slos[0]["version"] == "prometheus/v1"
