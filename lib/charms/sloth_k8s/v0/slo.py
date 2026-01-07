#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""SLO Provider and Requirer Library.

This library provides a way for charms to share SLO (Service Level Objective)
specifications with the Sloth charm, which will convert them into Prometheus
recording and alerting rules.

## Getting Started

### Provider Side (Charms providing SLO specs)

To provide SLO specifications to Sloth, use the `SLOProvider` class:

```python
from charms.sloth_k8s.v0.slo import SLOProvider

class MyCharm(ops.CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.slo_provider = SLOProvider(self)
        
    def _provide_slos(self):
        slo_spec = {
            "version": "prometheus/v1",
            "service": "my-service",
            "labels": {"team": "my-team"},
            "slos": [
                {
                    "name": "requests-availability",
                    "objective": 99.9,
                    "description": "99.9% of requests should succeed",
                    "sli": {
                        "events": {
                            "error_query": 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))',
                            "total_query": 'sum(rate(http_requests_total[{{.window}}]))',
                        }
                    },
                    "alerting": {
                        "name": "MyServiceHighErrorRate",
                        "labels": {"severity": "critical"},
                    },
                }
            ],
        }
        self.slo_provider.provide_slo(slo_spec)
```

### Requirer Side (Sloth charm)

The Sloth charm uses `SLORequirer` to collect SLO specifications:

```python
from charms.sloth_k8s.v0.slo import SLORequirer

class SlothCharm(ops.CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.slo_requirer = SLORequirer(self)
        self.framework.observe(
            self.slo_requirer.on.slos_changed,
            self._on_slos_changed
        )
        
    def _on_slos_changed(self, event):
        slos = self.slo_requirer.get_slos()
        # Process SLOs and generate rules
```

## Relation Data Format

SLO specifications are stored in the relation databag as YAML strings under the
`slo_spec` key. Each provider unit can provide one SLO specification.

```yaml
slo_spec: |
  version: prometheus/v1
  service: my-service
  labels:
    team: my-team
  slos:
    - name: requests-availability
      objective: 99.9
      description: "99.9% of requests should succeed"
      sli:
        events:
          error_query: 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))'
          total_query: 'sum(rate(http_requests_total[{{.window}}]))'
      alerting:
        name: MyServiceHighErrorRate
        labels:
          severity: critical
```
"""

import logging
from typing import Any, Dict, List

import ops
import yaml

# The unique Charmhub library identifier, never change it
LIBID = "placeholder"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

# Pydantic is loaded as a dependency of ops.
from pydantic import BaseModel, Field, ValidationError, field_validator

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "placeholder"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

DEFAULT_RELATION_NAME = "slos"


class SLOSpec(BaseModel):
    """Pydantic model for SLO specification validation."""

    version: str = Field(description="Sloth spec version, e.g., 'prometheus/v1'")
    service: str = Field(description="Service name for the SLO")
    labels: Dict[str, str] = Field(default_factory=dict, description="Labels for the SLO")
    slos: List[Dict[str, Any]] = Field(description="List of SLO definitions")

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate that version follows expected format."""
        if not v or "/" not in v:
            raise ValueError("Version must be in format 'prometheus/v1'")
        return v

    @field_validator("slos")
    @classmethod
    def validate_slos(cls, v: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate that at least one SLO is defined."""
        if not v:
            raise ValueError("At least one SLO must be defined")
        return v


class SLOsChangedEvent(ops.EventBase):
    """Event emitted when SLO specifications change."""

    pass


class SLOProviderEvents(ops.ObjectEvents):
    """Events for SLO provider."""

    pass


class SLORequirerEvents(ops.ObjectEvents):
    """Events for SLO requirer."""

    slos_changed = ops.EventSource(SLOsChangedEvent)


class SLOProvider(ops.Object):
    """Provider side of the SLO relation.
    
    Charms should use this class to provide SLO specifications to Sloth.
    
    Args:
        charm: The charm instance.
        relation_name: Name of the relation (default: "slos").
    """

    on = SLOProviderEvents()

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

    def provide_slo(self, slo_spec: Dict[str, Any]) -> None:
        """Provide an SLO specification to Sloth.
        
        Args:
            slo_spec: Dictionary containing the SLO specification in Sloth format.
                Must include: version, service, slos (list).
                
        Raises:
            ValidationError: If the SLO specification is invalid.
        """
        # Validate the SLO spec
        try:
            SLOSpec(**slo_spec)
        except ValidationError as e:
            logger.error(f"Invalid SLO specification: {e}")
            raise

        relations = self._charm.model.relations.get(self._relation_name, [])
        if not relations:
            logger.warning(f"No {self._relation_name} relation found")
            return

        # Convert to YAML for storage in relation databag
        slo_yaml = yaml.safe_dump(slo_spec)

        for relation in relations:
            # Each unit provides its SLO spec in its own databag
            relation.data[self._charm.unit]["slo_spec"] = slo_yaml
            logger.info(
                f"Provided SLO spec for service '{slo_spec['service']}' "
                f"to relation {relation.id}"
            )


class SLORequirer(ops.Object):
    """Requirer side of the SLO relation.
    
    The Sloth charm uses this class to collect SLO specifications from
    related charms.
    
    Args:
        charm: The charm instance.
        relation_name: Name of the relation (default: "slos").
    """

    on = SLORequirerEvents()

    def __init__(
        self,
        charm: ops.CharmBase,
        relation_name: str = DEFAULT_RELATION_NAME,
    ):
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        # Observe relation events
        self.framework.observe(
            charm.on[relation_name].relation_joined,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[relation_name].relation_changed,
            self._on_relation_changed,
        )
        self.framework.observe(
            charm.on[relation_name].relation_departed,
            self._on_relation_changed,
        )

    def _on_relation_changed(self, event: ops.RelationEvent) -> None:
        """Handle relation changed events."""
        self.on.slos_changed.emit()

    def get_slos(self) -> List[Dict[str, Any]]:
        """Collect all SLO specifications from related charms.
        
        Returns:
            List of SLO specification dictionaries from all related units.
        """
        slos = []
        relations = self._charm.model.relations.get(self._relation_name, [])

        for relation in relations:
            for unit in relation.units:
                try:
                    slo_yaml = relation.data[unit].get("slo_spec")
                    if not slo_yaml:
                        continue

                    slo_spec = yaml.safe_load(slo_yaml)

                    # Validate the SLO spec
                    try:
                        SLOSpec(**slo_spec)
                        slos.append(slo_spec)
                        logger.debug(
                            f"Collected SLO spec for service '{slo_spec['service']}' "
                            f"from {unit.name}"
                        )
                    except ValidationError as e:
                        logger.error(
                            f"Invalid SLO spec from {unit.name}: {e}"
                        )
                        continue

                except Exception as e:
                    logger.error(
                        f"Failed to parse SLO spec from {unit.name}: {e}"
                    )
                    continue

        logger.info(f"Collected {len(slos)} SLO specifications")
        return slos
