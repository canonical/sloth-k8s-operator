# Sloth Kubernetes Operator

Sloth provides SLI/SLO (Service Level Indicator/Service Level Objective) generation for Prometheus.
It generates Prometheus alerting and recording rules based on SLO specifications, helping teams
maintain and monitor their service reliability targets.

This operator builds a simple deployment of the Sloth server and provides a relation interface such
that it can be integrated with other Juju charms in a model.

## Architecture

```mermaid
graph LR
    subgraph "Application Charms"
        App1[Application Charm 1]
        App2[Application Charm 2]
        AppN[Application Charm N]
    end
    
    Sloth[Sloth K8s Operator]
    Prometheus[Prometheus]
    Grafana[Grafana]
    
    App1 -->|slo interface<br/>SLO expressions| Sloth
    App2 -->|slo interface<br/>SLO expressions| Sloth
    AppN -->|slo interface<br/>SLO expressions| Sloth
    
    Sloth -->|metrics-endpoint<br/>recording rules| Prometheus
    Sloth -->|grafana_dashboard<br/>SLO dashboards| Grafana
    Prometheus -->|queries<br/>metrics data| Sloth
    
    style Sloth fill:#326CE5,stroke:#fff,stroke-width:2px,color:#fff
    style Prometheus fill:#E6522C,stroke:#fff,stroke-width:2px,color:#fff
    style Grafana fill:#F46800,stroke:#fff,stroke-width:2px,color:#fff
```

**How it works:**
1. **Application charms** that implement SLI/SLOs relate to Sloth over the `slo` interface, providing SLO expressions
2. **Sloth** converts the SLO expressions to generate Prometheus recording rules
3. **Recording rules** are pushed to Prometheus via the `metrics-endpoint` relation
4. **Dashboards** are sent to Grafana via the `grafana_dashboard` relation for visualization
5. **Status page** is created by Sloth fetching metrics data from Prometheus

## Usage

You can deploy the operator as such:

```shell
# Deploy the charm
$ juju deploy sloth-k8s --trust --channel edge
```

Once the deployment is complete, grab the address of the Sloth application:

```bash
# assuming juju 3.6:
$ juju show-unit sloth-k8s/0 --format=json | jq -r '.["sloth-k8s/0"]["address"]'
```

## Configuration

Sloth generates SLO rules based on provided SLO specifications. The generated rules can be
consumed by Prometheus for monitoring service reliability.

## Implementing SLO Support in Your Charm

To implement SLO support in a charm that defines its own SLI/SLO expressions, you need:

### 1. Add the Sloth library dependency

Add `charmlibs-interfaces-sloth` to your dependencies in your charm's preferred manner.

### 2. Import and instantiate SlothProvider

```python
from charmlibs.interfaces.sloth import SlothProvider

class YourCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.slo_provider = SlothProvider(self)
```

### 3. Define your SLO specification

Follow Sloth's format (as YAML string):

```python
slo_yaml = """
version: prometheus/v1
service: your-service-name
labels:
  team: your-team
slos:
  - name: availability
    objective: 99.9
    description: "99.9% availability"
    sli:
      events:
        error_query: 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))'
        total_query: 'sum(rate(http_requests_total[{{.window}}]))'
    alerting:
      name: YourServiceHighErrorRate
      labels:
        severity: page
"""
```

### 4. Provide the SLO spec

Provide the SLO spec when appropriate (e.g., on pebble-ready, config-changed):

```python
self.slo_provider.provide_slos(slo_yaml)
```

### 5. Add metadata

In your charm's `charmcraft.yaml`:

```yaml
provides:
  slos:
    interface: slo
```

### 6. Relate to Sloth

```bash
juju relate your-charm:slos sloth-k8s:slos
```

The Sloth library supports dynamic SLO updates, Pydantic validation, and is designed for easy integration with any charm that wants to provide SLO specifications.
