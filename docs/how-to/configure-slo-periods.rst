.. meta::
    :description: How to configure `sloth-k8s` SLO periods.

.. _how-to-guides-configure-slo-periods:

How-to configure SLO periods
=============================

This guide explains how to configure the SLO period and custom alerting windows for
`sloth-k8s`.

Change the default SLO period
------------------------------

By default, `sloth-k8s` uses a 30-day SLO period. You can change this to match your
organization's SLO requirements:

.. code-block:: bash

    # Set a 7-day SLO period
    juju config sloth-k8s slo-period=7d

    # Set a 28-day SLO period (4-week rolling window)
    juju config sloth-k8s slo-period=28d

Common SLO periods:

- ``30d`` - 30 days (default, Google's recommended standard)
- ``28d`` - 28 days (4-week rolling window)
- ``7d`` - 7 days (for shorter-term SLOs or testing)

Configure custom alert windows
-------------------------------

For advanced use cases, you can configure custom alerting windows that define when alerts
should fire based on error budget consumption. This is useful when:

- You're using a non-standard SLO period
- You need different alerting thresholds than the defaults
- You want to tune alert sensitivity for your specific use case

Basic example (7-day period)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    juju config sloth-k8s slo-period=7d
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

Understanding alert windows
^^^^^^^^^^^^^^^^^^^^^^^^^^^

The configuration defines four types of alerts:

1. **Page Quick**: Fast detection (5m window) of significant error budget consumption (8%)
2. **Page Slow**: Detection over a longer period (6h window) of sustained issues (12.5%)
3. **Ticket Quick**: Early warning (1d window) of moderate consumption (20%)
4. **Ticket Slow**: Long-term monitoring (3d window) for trends (42%)

Each alert has:

- ``errorBudgetPercent``: Percentage of error budget consumed to trigger the alert
- ``shortWindow``: Shorter window to detect when the issue has resolved
- ``longWindow``: Longer window to measure overall error budget consumption

Custom thresholds example
^^^^^^^^^^^^^^^^^^^^^^^^^^

To make alerts more sensitive to errors:

.. code-block:: bash

    juju config sloth-k8s slo-period-windows='
    apiVersion: sloth.slok.dev/v1
    kind: AlertWindows
    spec:
      sloPeriod: 30d
      page:
        quick:
          errorBudgetPercent: 1      # Very sensitive
          shortWindow: 2m
          longWindow: 30m
        slow:
          errorBudgetPercent: 2
          shortWindow: 15m
          longWindow: 3h
      ticket:
        quick:
          errorBudgetPercent: 5
          shortWindow: 1h
          longWindow: 12h
        slow:
          errorBudgetPercent: 5
          shortWindow: 3h
          longWindow: 36h
    '

Reset to defaults
-----------------

To return to using Sloth's default alert windows:

.. code-block:: bash

    # Clear custom windows
    juju config sloth-k8s slo-period-windows=''
    
    # Reset to default 30-day period
    juju config sloth-k8s --reset slo-period

Verify configuration
--------------------

After changing the configuration, verify that the rules are regenerated:

.. code-block:: bash

    # Check the charm status
    juju status sloth-k8s
    
    # Check the logs for regeneration
    juju debug-log --replay --include sloth-k8s

You should see log messages indicating that:

1. Custom SLO period windows configuration was updated (if configured)
2. Prometheus rules were generated

**Note**: Changes to these configuration options trigger automatic regeneration of all
SLO rules, which will be pushed to Prometheus via the metrics-endpoint relation.

Learn more
----------

- :ref:`reference-configuration` - Detailed configuration reference
- `Sloth SLO Period Windows <https://sloth.dev/usage/slo-period-windows/>`_ - Upstream documentation
- `Google SRE Workbook <https://sre.google/workbook/alerting-on-slos/>`_ - Recommended alert window parameters
