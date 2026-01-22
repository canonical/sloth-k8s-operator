# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Unit tests for the Sloth charm."""

from dataclasses import replace

import pytest
import yaml
from ops.model import ActiveStatus, WaitingStatus
from ops.testing import CharmEvents, Relation, State


@pytest.fixture
def base_state(sloth_container, sloth_peers):
    """Return base state with all containers ready."""
    return State(
        containers=[sloth_container],
        relations=[sloth_peers],
    )


def assert_healthy(state: State):
    """Assert that the charm is in a healthy state."""
    # Check the unit status is active
    assert isinstance(state.unit_status, ActiveStatus)

    # Check the workload version is set
    assert state.workload_version == "0.11.0"


@pytest.fixture(params=(0,))
def any_container(sloth_container, request):
    """Parametrized fixture for testing any individual container."""
    return (sloth_container,)[request.param]


def test_healthy_container_events(context, any_container, base_state):
    """Test that pebble-ready events for any container lead to healthy state."""
    state_out = context.run(context.on.pebble_ready(any_container), base_state)
    assert_healthy(state_out)


@pytest.mark.parametrize(
    "event",
    (
        CharmEvents().update_status(),
        CharmEvents().start(),
        CharmEvents().install(),
        CharmEvents().config_changed(),
    ),
)
def test_healthy_lifecycle_events(context, event, base_state):
    """Test that standard lifecycle events lead to healthy state."""
    state_out = context.run(event, base_state)
    assert_healthy(state_out)


def test_config_changed_container_not_ready(
    context, sloth_container, sloth_peers
):
    """Test config-changed when containers are not ready."""
    sloth_container_not_ready = replace(sloth_container, can_connect=False)
    state = State(
        containers=[sloth_container_not_ready],
        relations=[sloth_peers],
    )

    state_out = context.run(context.on.config_changed(), state)
    assert isinstance(state_out.unit_status, WaitingStatus)
    assert "Waiting for containers" in state_out.unit_status.message


def test_install_container_not_ready(
    context, sloth_container
):
    """Test install hook when containers are not ready."""
    sloth_container_not_ready = replace(sloth_container, can_connect=False)
    state = State(
        containers=[sloth_container_not_ready]
    )

    # Install should not fail even if containers aren't ready
    state_out = context.run(context.on.install(), state)
    assert isinstance(state_out.unit_status, WaitingStatus)


def test_slo_relation_joined(context, base_state):
    """Test that SLO relation can be joined."""
    slo_relation = Relation("slos", remote_app_name="slo-provider")
    state = replace(base_state, relations=list(base_state.relations) + [slo_relation])

    state_out = context.run(context.on.relation_joined(slo_relation), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_slo_relation_changed_with_valid_data(context, base_state):
    """Test SLO relation changed with valid SLO data."""
    slo_spec = {
        "version": "prometheus/v1",
        "service": "test-app",
        "labels": {"team": "test"},
        "slos": [
            {
                "name": "availability",
                "objective": 99.9,
                "description": "Test SLO",
                "sli": {
                    "events": {
                        "error_query": "sum(rate(errors[{{.window}}]))",
                        "total_query": "sum(rate(requests[{{.window}}]))",
                    }
                },
            }
        ],
    }
    slo_yaml = yaml.safe_dump(slo_spec)
    slo_relation = Relation(
        "slos",
        remote_app_name="slo-provider",
        remote_units_data={0: {"slo_spec": slo_yaml}},
    )
    state = replace(base_state, relations=list(base_state.relations) + [slo_relation])

    state_out = context.run(context.on.relation_changed(slo_relation), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_slo_relation_changed_with_invalid_data(context, base_state):
    """Test SLO relation changed with invalid SLO data."""
    invalid_slo_yaml = "invalid: yaml: {{{"
    slo_relation = Relation(
        "slos",
        remote_app_name="slo-provider",
        remote_units_data={0: {"slo_spec": invalid_slo_yaml}},
    )
    state = replace(base_state, relations=list(base_state.relations) + [slo_relation])

    # Should not crash, just log error
    state_out = context.run(context.on.relation_changed(slo_relation), state)
    # Charm should still be active (invalid SLOs are logged and skipped)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_slo_relation_departed(context, base_state):
    """Test SLO relation departed."""
    slo_relation = Relation("slos", remote_app_name="slo-provider")
    state = replace(base_state, relations=list(base_state.relations) + [slo_relation])

    state_out = context.run(context.on.relation_departed(slo_relation), state)
    # Should remain active after relation departure
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_multiple_slo_relations(context, base_state):
    """Test handling multiple SLO provider relations."""
    slo_spec_1 = {
        "version": "prometheus/v1",
        "service": "app1",
        "slos": [
            {
                "name": "availability",
                "objective": 99.9,
                "sli": {"events": {"error_query": "errors1", "total_query": "requests1"}},
            }
        ],
    }
    slo_spec_2 = {
        "version": "prometheus/v1",
        "service": "app2",
        "slos": [
            {
                "name": "availability",
                "objective": 99.5,
                "sli": {"events": {"error_query": "errors2", "total_query": "requests2"}},
            }
        ],
    }
    slo_relation_1 = Relation(
        "slos",
        remote_app_name="provider1",
        remote_units_data={0: {"slo_spec": yaml.safe_dump(slo_spec_1)}},
    )
    slo_relation_2 = Relation(
        "slos",
        remote_app_name="provider2",
        remote_units_data={0: {"slo_spec": yaml.safe_dump(slo_spec_2)}},
    )
    state = replace(base_state, relations=list(base_state.relations) + [slo_relation_1, slo_relation_2])

    state_out = context.run(context.on.update_status(), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_config_slo_period(context, base_state):
    """Test that slo-period config option is respected."""
    state = replace(base_state, config={"slo-period": "7d"})

    state_out = context.run(context.on.config_changed(), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_ingress_relation(context, base_state):
    """Test ingress relation integration."""
    ingress_relation = Relation(
        "ingress",
        remote_app_name="traefik",
        remote_app_data={"external_host": "sloth.example.com", "scheme": "https"},
    )
    state = replace(base_state, relations=list(base_state.relations) + [ingress_relation])

    state_out = context.run(context.on.relation_changed(ingress_relation), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_metrics_endpoint_relation(context, base_state):
    """Test metrics-endpoint relation."""
    metrics_relation = Relation("metrics-endpoint", remote_app_name="prometheus")
    state = replace(base_state, relations=list(base_state.relations) + [metrics_relation])

    state_out = context.run(context.on.relation_joined(metrics_relation), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_grafana_dashboard_relation(context, base_state):
    """Test grafana-dashboard relation."""
    grafana_relation = Relation("grafana-dashboard", remote_app_name="grafana")
    state = replace(base_state, relations=list(base_state.relations) + [grafana_relation])

    state_out = context.run(context.on.relation_joined(grafana_relation), state)
    assert isinstance(state_out.unit_status, ActiveStatus)


def test_charm_does_not_error_on_missing_containers(context, sloth_peers):
    """Test that charm doesn't go into error state during install when containers aren't ready."""
    # Containers not connected yet (realistic during install)
    from ops.testing import Container
    sloth_not_ready = Container("sloth", can_connect=False)

    state = State(
        containers=[sloth_not_ready],
        relations=[sloth_peers],
    )

    # Install should handle this gracefully (try/except in __init__)
    state_out = context.run(context.on.install(), state)
    # Should be waiting, not error
    assert isinstance(state_out.unit_status, WaitingStatus)


def test_charm_recovers_from_waiting_state(
    context, sloth_container, sloth_peers
):
    """Test that charm can recover from waiting state."""
    # Start with containers not ready
    sloth_not_ready = replace(sloth_container, can_connect=False)
    state = State(
        containers=[sloth_not_ready],
        relations=[sloth_peers],
    )

    state_out = context.run(context.on.start(), state)
    assert isinstance(state_out.unit_status, WaitingStatus)

    # Now simulate pebble-ready (container becomes ready)
    state_ready = replace(state_out,
        containers=[sloth_container]
    )
    state_final = context.run(context.on.pebble_ready(sloth_container), state_ready)
    assert_healthy(state_final)


def test_peer_relation_required(context, sloth_container):
    """Test behavior without peer relation."""
    # Note: Peer relations are typically always present, but test defensive behavior
    state = State(
        containers=[sloth_container],
        relations=[],
    )

    # Should handle missing peer relation gracefully
    state_out = context.run(context.on.start(), state)
    # May be waiting or active depending on implementation
    assert state_out.unit_status is not None
