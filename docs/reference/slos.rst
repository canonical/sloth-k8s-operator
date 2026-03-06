.. meta::
    :description: Reference for the Sloth SLO specification format used by `sloth-k8s`.

.. _reference-slos:

SLO specification format
=========================

``sloth-k8s`` accepts SLO specifications in the **Prometheus Sloth** format
(``version: "prometheus/v1"``). This page describes the structure of a specification file
and the fields available within it.

For the authoritative upstream specification, see:

* `Sloth Prometheus SLO spec — Go API reference <https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1>`_
* `Sloth documentation <https://sloth.dev>`_

Annotated example
-----------------

.. code-block:: yaml

    # Required. Must be "prometheus/v1" for the Prometheus backend.
    version: "prometheus/v1"

    # Required. A logical name for the service being measured.
    service: "my-service"

    # Optional. Arbitrary key/value labels attached to every generated rule.
    labels:
      team: my-team
      repo: my-org/my-service

    # Required. One or more SLO definitions.
    slos:
      - # Required. Unique name for this SLO within the service. Used in rule names.
        name: "requests-availability"

        # Required. Target success percentage over the SLO period (0–100).
        objective: 99.9

        # Optional. Human-readable description included in rule annotations.
        description: "99.9% of HTTP requests succeed."

        sli:
          # "events" SLI: ratio of bad events to total events.
          events:
            # Required. PromQL expression that evaluates to the rate of bad events.
            # Use {{.window}} as a placeholder — Sloth substitutes the correct window duration.
            error_query: |
              sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))
            # Required. PromQL expression that evaluates to the rate of all events.
            total_query: |
              sum(rate(http_requests_total[{{.window}}]))

        alerting:
          # Required (when alerting is enabled). Alert name prefix used in generated rules.
          name: MyServiceHighErrorRate

          # Optional. Extra labels added to generated alert rules.
          labels:
            category: availability

          # Optional. Annotations added to generated alert rules.
          annotations:
            summary: "High error rate on 'my-service' requests"

          # Optional. Override labels/annotations for the page-level alert only,
          # or disable it entirely.
          page_alert:
            labels:
              severity: page
          #   disable: true   # uncomment to suppress the page alert

          # Optional. Override labels/annotations for the ticket-level alert only,
          # or disable it entirely.
          ticket_alert:
            labels:
              severity: ticket
          #   disable: true   # uncomment to suppress the ticket alert

Top-level fields
----------------

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Field
     - Required
     - Description
   * - ``version``
     - Yes
     - Must be ``"prometheus/v1"``.
   * - ``service``
     - Yes
     - Logical name of the service. Included in all generated rule labels as
       ``sloth_service``.
   * - ``labels``
     - No
     - Arbitrary key/value map. Propagated as labels on every generated recording
       rule and alert rule.
   * - ``slos``
     - Yes
     - List of SLO definitions. Must contain at least one entry.

SLO fields (``slos[*]``)
------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Field
     - Required
     - Description
   * - ``name``
     - Yes
     - Unique identifier within the service. Used in rule group names and the
       ``sloth_slo`` label. Must be lowercase alphanumeric with hyphens.
   * - ``objective``
     - Yes
     - Target success rate as a percentage (e.g., ``99.9``). The error budget is
       ``100 - objective``.
   * - ``description``
     - No
     - Human-readable description included in generated rule annotations.
   * - ``sli``
     - Yes
     - Defines the SLI measurement. See :ref:`sli-fields` below.
   * - ``alerting``
     - No
     - Configures alerting rules. If omitted, no alerts are generated.

.. _sli-fields:

SLI fields (``slos[*].sli``)
-----------------------------

Sloth supports two SLI types. Only one may be specified per SLO.

**Events-based SLI** (``sli.events``)

Measures the ratio of bad events to all events. Suitable for request-based services.

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Field
     - Required
     - Description
   * - ``error_query``
     - Yes
     - PromQL expression returning the rate of bad (error) events. Must use
       ``[{{.window}}]`` as the range vector duration; Sloth substitutes the
       appropriate window for each generated rule.
   * - ``total_query``
     - Yes
     - PromQL expression returning the rate of all events. Same ``{{.window}}``
       placeholder requirement.

**Raw-ratio SLI** (``sli.raw``)

Provides a pre-computed error ratio directly, when you already have an expression that
returns a value between 0 and 1. Consult the
`upstream API reference <https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1>`_
for the ``raw`` field structure.

Alerting fields (``slos[*].alerting``)
---------------------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 10 70

   * - Field
     - Required
     - Description
   * - ``name``
     - Yes (if ``alerting`` is present)
     - Prefix for generated alert rule names.
   * - ``labels``
     - No
     - Labels applied to all generated alert rules for this SLO.
   * - ``annotations``
     - No
     - Annotations applied to all generated alert rules.
   * - ``page_alert``
     - No
     - Overrides for the page-level (high-urgency) alert. Supports ``labels``,
       ``annotations``, and ``disable: true``.
   * - ``ticket_alert``
     - No
     - Overrides for the ticket-level (lower-urgency) alert. Supports ``labels``,
       ``annotations``, and ``disable: true``.

Related
-------

* :ref:`how-to-guides-integrate` — step-by-step guide to providing SLOs to ``sloth-k8s``
* :ref:`explanation-slos` — conceptual overview of SLOs, error budgets, and the design space
  for providing SLOs to ``sloth-k8s``
* :ref:`reference-configuration` — configuration options for ``sloth-k8s``

