Configuration
=============

This document provides detailed information about the configuration options available
for the Sloth Kubernetes operator.

SLO Period Configuration
-------------------------

The charm supports configuration options for controlling SLO period windows and alert
generation.

slo-period
^^^^^^^^^^

**Type:** string

**Default:** ``30d``

**Description:**

The default SLO period for calculations. This determines the time window over which SLO
compliance is measured. Common values include:

- ``30d`` - 30 days (default, recommended for most use cases)
- ``28d`` - 28 days (4-week rolling window)
- ``7d`` - 7 days (for shorter-term SLOs)

**Example:**

.. code-block:: bash

    juju config sloth-k8s slo-period=7d

slo-period-windows
^^^^^^^^^^^^^^^^^^

**Type:** string (YAML format)

**Default:** ``""`` (empty, uses Sloth defaults)

**Description:**

Custom SLO period windows configuration in YAML format. This allows you to define custom
alerting windows that override Sloth's default alert window calculations.

When provided, this configuration defines:

- **Quick page alerts**: Fast detection of significant error budget consumption
- **Slow page alerts**: Detection of sustained error budget consumption
- **Quick ticket alerts**: Early warning of moderate error budget consumption
- **Slow ticket alerts**: Long-term trend monitoring

The YAML must follow the Sloth AlertWindows specification (``apiVersion: sloth.slok.dev/v1``,
``kind: AlertWindows``). The charm validates the configuration against the AlertWindows spec
to ensure all required fields are present and correctly formatted.

**Validation:**

The charm validates:

- ``kind`` must be "AlertWindows"
- ``apiVersion`` must be "sloth.slok.dev/v1"
- ``sloPeriod`` must be a valid duration (e.g., "7d", "30d")
- All time windows (``shortWindow``, ``longWindow``) must use valid Prometheus duration format
- ``errorBudgetPercent`` must be between 0 and 100
- All required fields (page.quick, page.slow, ticket.quick, ticket.slow) must be present

Invalid configurations are logged as errors and ignored.

**Configuration Parameters:**

- ``sloPeriod``: Must match your ``slo-period`` config value (e.g., "7d", "30d")
- ``errorBudgetPercent``: Percentage of error budget consumed to trigger alert (0-100)
- ``shortWindow``: Shorter time window for detecting transient issues (e.g., "5m", "30m")
- ``longWindow``: Longer time window for overall trend (e.g., "1h", "6h")

**Example (7-day SLO period):**

.. code-block:: bash

    juju config sloth-k8s slo-period-windows='
    apiVersion: sloth.slok.dev/v1
    kind: AlertWindows
    spec:
      sloPeriod: 7d
      page:
        quick:
          errorBudgetPercent: 8
          shortWindow: 5m
          longWindow: 1h
        slow:
          errorBudgetPercent: 12.5
          shortWindow: 30m
          longWindow: 6h
      ticket:
        quick:
          errorBudgetPercent: 20
          shortWindow: 2h
          longWindow: 1d
        slow:
          errorBudgetPercent: 42
          shortWindow: 6h
          longWindow: 3d
    '

**Example (custom 30-day thresholds):**

.. code-block:: bash

    juju config sloth-k8s slo-period-windows='
    apiVersion: sloth.slok.dev/v1
    kind: AlertWindows
    spec:
      sloPeriod: 30d
      page:
        quick:
          errorBudgetPercent: 2
          shortWindow: 5m
          longWindow: 1h
        slow:
          errorBudgetPercent: 5
          shortWindow: 30m
          longWindow: 6h
      ticket:
        quick:
          errorBudgetPercent: 10
          shortWindow: 2h
          longWindow: 1d
        slow:
          errorBudgetPercent: 10
          shortWindow: 6h
          longWindow: 3d
    '

**Notes:**

- The default 30d and 28d periods use Google's SRE Workbook recommended parameters
- Only configure custom windows if you need different alerting thresholds or are using
  non-standard SLO periods
- Invalid YAML will be logged as an error and ignored
- Changes to this configuration trigger rule regeneration

**References:**

- `Sloth SLO Period Windows Documentation <https://sloth.dev/usage/slo-period-windows/>`_
- `Google SRE Workbook - Alerting on SLOs <https://sre.google/workbook/alerting-on-slos/>`_
