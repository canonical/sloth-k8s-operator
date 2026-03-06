.. meta::
    :description: Understand SLOs, SLIs, and how sloth-k8s generates Prometheus rules in the COS ecosystem.

.. _explanation-slos:

SLOs and how sloth-k8s works
=============================

This page explains the concepts behind Service Level Objectives (SLOs) and Service Level
Indicators (SLIs), how ``sloth-k8s`` fits into the Canonical Observability Stack (COS), and
the trade-offs involved in choosing how to provide SLO definitions to it.

What are SLIs, SLOs, and error budgets?
----------------------------------------

A **Service Level Indicator (SLI)** is a quantitative measure of some aspect of a service's
behaviour — for example, the fraction of HTTP requests that succeed, or the fraction of
database queries completed within 100 ms.

A **Service Level Objective (SLO)** is a target value (or range) for an SLI. For instance,
"99.9% of HTTP requests succeed over a rolling 30-day window." SLOs translate abstract
reliability goals into concrete, measurable commitments.

An **error budget** is the allowable amount of unreliability implied by the SLO. If your SLO
is 99.9%, then 0.1% of requests may fail — that is your error budget. When the error budget is
depleted, it signals that the service has been more unreliable than the agreed target and that
remediation should be prioritised.

These concepts are described in depth in the
`Google SRE book <https://sre.google/sre-book/service-level-objectives/>`_ and the
`Google SRE Workbook <https://sre.google/workbook/alerting-on-slos/>`_.

How sloth-k8s works
--------------------

``sloth-k8s`` is a Juju operator that wraps the
`Sloth <https://github.com/slok/sloth>`_ SLO generator. Its role in the
Canonical Observability Stack (COS) is to act as a rules factory:

1. **Charm operators** (your application charm, or ``cos-configuration-k8s``) send SLO
   specifications over the ``sloth`` relation interface.
2. **sloth-k8s** runs ``sloth generate`` to convert each SLO spec into Prometheus recording
   rules and alerting rules.
3. The generated rules are pushed to **Prometheus** via the ``metrics-endpoint`` relation.
4. **Grafana** receives pre-built SLO dashboards via the ``grafana-dashboard`` relation,
   visualising error-budget burn for each SLO.

.. code-block:: text

    [Your charm / cos-configuration-k8s]
             |  sloth relation (SLO YAML specs)
             ▼
        [sloth-k8s]
         /        \
        ▼          ▼
    [Prometheus]  [Grafana]
    (recording &  (SLO dashboards)
     alert rules)

Because Sloth generates the Prometheus rule groups from your SLO definitions, you never need
to write the low-level multi-window, multi-burn-rate alert expressions by hand.

Design space for providing SLOs to sloth-k8s
---------------------------------------------

When adding SLO support to a charm (or a deployment), you have several options with different
trade-offs:

1. **Via** ``cos-configuration-k8s`` **(no charm changes required)**

   `cos-configuration-k8s <https://charmhub.io/cos-configuration-k8s>`_ is a Canonical
   charm that syncs a git repository and forwards SLO files it finds there to ``sloth-k8s``.
   This approach is ideal when you want to add SLOs to an existing deployment without touching
   application charm code. SLO definitions live in a version-controlled git repository and can
   be updated independently of charm releases.

2. **Hardcode SLOs in the charm with a tier/preset knob**

   Bundle one or more SLO spec files with the charm and expose a single user-facing config
   option (e.g., ``slo-tier: critical|standard|low``) that selects among them. This gives
   operators a simple knob without requiring them to understand the Sloth YAML format, while
   keeping the SLO logic inside the charm codebase.

3. **Expose individual SLO objective config options**

   Provide separate Juju config options for each SLO objective value (e.g.,
   ``availability-target: 99.9``), while keeping the SLO structure hardcoded. This separates
   *what* is measured from *how strictly* it is measured, letting the operator tune targets
   to their risk tolerance without rewriting the spec.

4. **Accept a raw SLO YAML string as a config option**

   Expose a multi-line string config option (e.g., ``slos:``) where the operator pastes a
   complete Sloth spec. This gives maximum flexibility at the cost of requiring the operator to
   understand the Sloth format. It is most useful as a fallback alongside one of the other
   approaches.

In practice, the most resilient charms combine options 2–4: they ship sensible presets, allow
objective tuning via config, and accept a raw override for advanced operators. See
:ref:`how-to-guides-integrate` for step-by-step instructions on each approach.

Understanding alert windows
----------------------------

When Sloth generates alerting rules it uses **alert windows** — pairs of short and long
observation windows — to detect error-budget burn at multiple rates. The combination of a fast
and a slow window suppresses false positives while remaining sensitive enough to catch genuine
incidents quickly.

Each SLO generates four alert types:

* **Page — quick**: Short window (e.g., 5 min / 1 h). Fires when a large fraction of the error
  budget is consumed rapidly. Intended to wake someone up.
* **Page — slow**: Longer window (e.g., 30 min / 6 h). Fires when sustained consumption
  threatens the budget even if the rate is not immediately catastrophic.
* **Ticket — quick**: Medium window (e.g., 2 h / 1 d). Creates a ticket-level alert for
  moderate burn that needs attention but is not yet urgent.
* **Ticket — slow**: Long window (e.g., 6 h / 3 d). Detects slow, chronic erosion of the error
  budget before it becomes critical.

Sloth ships built-in alert window defaults only for the ``30d`` and ``28d`` SLO periods (based
on Google's SRE Workbook recommendations). For any other period (e.g., ``7d``) you must provide
custom alert windows via the ``slo-period-windows`` configuration option, otherwise the charm
will enter a blocked state.

See :ref:`how-to-guides-configure-slo-periods` for instructions on setting custom alert
windows, and the
`Sloth SLO Period Windows documentation <https://sloth.dev/usage/slo-period-windows/>`_ and
`Google SRE Workbook — Alerting on SLOs <https://sre.google/workbook/alerting-on-slos/>`_
for background on choosing appropriate thresholds.
