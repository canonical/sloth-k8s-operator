.. meta::
    :description: Configuration guide for `sloth-k8s`.

.. _reference-slos:


SLOs: data flow from your charm to `sloth-k8s`
==============================================

This is a sample SLO yaml spec that Sloth needs to generate configurations for Prometheus and Grafana in order to
monitor, in this case, a Tempo process.

.. code-block:: yaml

    version: "prometheus/v1"
    service: "tempo-coordinator"
    labels:
      repo: "canonical/tempo-operators"
    slos:
      # Span ingestion availability - Track discarded spans
      - name: "span-ingestion-availability"
        objective: 99.5
        description: "Track the availability of span ingestion (excluding discarded spans)"
        sli:
          events:
            error_query: sum(rate(tempo_discarded_spans_total[{{.window}}]))
            total_query: sum(rate(tempo_distributor_spans_received_total[{{.window}}]))
        alerting:
          name: TempoSpanIngestion
          page_alert:
            disable: true
          ticket_alert:
            disable: true

One way or another, the Tempo charm needs to provide this SLO definition to Sloth over the `slos` interface.
There are multiple ways to do this, from easy but rigid to more complex but powerful:

1. Hardcoding one or more SLOs in the charm code, so that every time the charm is deployed, it provides the same SLOs to Sloth (allowing the user to choose among the predefined SLOs via a charm config option, for example). This way, the charm could expose a single user-facing knob based on how critical the service is in their infrastructure (e.g., "crucial", "standard", "low-risk"), and each option would correspond to predefined SLO definitions with varying target objectives.
2. Exposing a Juju config option for each SLO objective, allowing the user to set their desired objectives while the SLO definitions remain hardcoded in the charm.
3. Allowing the user to provide a custom SLO definition via a charm config option (e.g., a multi-line string config option where the user can paste their raw SLO yaml spec).