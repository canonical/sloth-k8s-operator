.. meta::
    :description: A hands-on tutorial for adding SLI/SLO monitoring to a Kubernetes deployment with sloth-k8s.

Get started with SLI/SLOs
==========================

In this tutorial we will deploy a monitoring stack on Kubernetes, write an SLO
specification for a running service, and watch Sloth transform that specification into
Prometheus recording rules and a Grafana dashboard.

By the end of the tutorial you will have:

- A working Kubernetes model with **prometheus-k8s**, **grafana-k8s**, and **sloth-k8s**
- An SLO specification file that defines an availability target
- Recording rules and alerting rules generated in Prometheus
- An SLO dashboard visible in Grafana

.. note::

    This tutorial uses **prometheus-k8s** itself as the monitored service. Prometheus
    already exposes HTTP request metrics, so no extra application is needed to see live
    SLO data.

Prerequisites
-------------

- A Juju controller with a registered Kubernetes cloud (e.g., MicroK8s).
  Run ``juju clouds`` to confirm a cloud is available.
- A public git repository (e.g., on GitHub or GitLab) where you can push a YAML file.
  ``cos-configuration-k8s`` will clone this repository to read your SLO specs.

Deploy the stack
----------------

Create a new Kubernetes model for this tutorial:

.. code-block:: bash

    juju add-model welcome-k8s <your-k8s-cloud>

Replace ``<your-k8s-cloud>`` with the name of your Kubernetes cloud (e.g.,
``microk8s``). ``juju clouds`` lists your registered clouds.

Now deploy prometheus-k8s, grafana-k8s, alertmanager-k8s, and sloth-k8s:

.. code-block:: bash

    juju deploy prometheus-k8s prom --trust --channel 2/stable
    juju deploy grafana-k8s grafana --trust --channel 2/stable
    juju deploy alertmanager-k8s alertmanager --trust --channel 2/stable
    juju deploy sloth-k8s sloth --trust --channel latest/edge

Wait for the applications to become active (this may take a few minutes while container
images are pulled):

.. code-block:: bash

    juju status --watch 5s

You should eventually see all four applications in ``active/idle`` status:

.. code-block:: text

    App           Version  Status  Scale  Charm
    alertmanager  0.28.0   active      1  alertmanager-k8s
    grafana       12.0.2   active      1  grafana-k8s
    prom          2.53.3   active      1  prometheus-k8s
    sloth         0.15.0   active      1  sloth-k8s

Press ``Ctrl-C`` to stop watching once all four are active.

Connect the components
----------------------

The monitoring stack components need to be integrated with each other. Run all of the
following ``juju integrate`` commands:

.. code-block:: bash

    # Wire Prometheus into Grafana
    juju integrate prom:grafana-dashboard grafana:grafana-dashboard
    juju integrate prom:grafana-source grafana:grafana-source
    juju integrate prom:metrics-endpoint grafana:metrics-endpoint

    # Wire Alertmanager into Grafana and Prometheus
    juju integrate alertmanager:grafana-dashboard grafana:grafana-dashboard
    juju integrate alertmanager:grafana-source grafana:grafana-source
    juju integrate prom:alertmanager alertmanager:alerting

    # Wire Sloth into Prometheus and Grafana
    juju integrate sloth:metrics-endpoint prom:metrics-endpoint
    juju integrate sloth:grafana-dashboard grafana:grafana-dashboard

Run ``juju status`` once more. Every application should still show ``active/idle``.
Sloth will show ``active`` even without SLO specs — it is ready and waiting.

Write your first SLO specification
-----------------------------------

An SLO specification is a YAML file that describes what *good behaviour* looks like for a
service. Sloth reads this file and generates the Prometheus rules needed to measure and
alert on it.

Create a file called ``slos/prometheus.yaml`` in your git repository with the following
content:

.. code-block:: yaml

    version: "prometheus/v1"
    service: "prometheus-k8s"
    labels:
      team: platform
    slos:
      - name: "requests-availability"
        objective: 99.9
        description: "99.9% of HTTP requests to Prometheus succeed."
        sli:
          events:
            error_query: >
              (sum(rate(prometheus_http_requests_total{code=~"5.."}[{{.window}}]))
              or vector(0))
            total_query: >
              sum(rate(prometheus_http_requests_total[{{.window}}]))
        alerting:
          name: "PrometheusHighErrorRate"
          annotations:
            summary: "Prometheus HTTP API has a high error rate"
          page_alert:
            labels:
              severity: critical
          ticket_alert:
            labels:
              severity: warning

The ``sli.events`` block defines the SLI using two PromQL expressions:

- ``error_query`` counts the rate of failed requests (HTTP 5xx responses).
- ``total_query`` counts the rate of all requests.

Sloth will compute the error ratio and use it to track the error budget against the
99.9% objective.

Commit and push the file:

.. code-block:: bash

    git add slos/prometheus.yaml
    git commit -m "Add Prometheus availability SLO"
    git push

Make a note of your repository URL and branch name — you will need them in the next step.

Provide the SLO specs to Sloth
-------------------------------

``cos-configuration-k8s`` is a charm that periodically clones a git repository and
forwards any SLO files it finds to ``sloth-k8s``. Deploy it using the ``dev/edge``
channel, which includes Sloth support:

.. code-block:: bash

    juju deploy cos-configuration-k8s cos-config --trust --channel dev/edge

Configure it to point at your repository:

.. code-block:: bash

    juju config cos-config \
        git_repo=https://github.com/<your-org>/<your-repo> \
        git_branch=main \
        slos_path=slos

Replace ``git_repo`` and ``git_branch`` with your repository URL and branch name.
``slos_path`` is the directory inside the repository where the SLO YAML files live.

Now connect ``cos-config`` to ``sloth``:

.. code-block:: bash

    juju integrate cos-config:sloth sloth:sloth

Wait for ``cos-config`` to become active — it will clone the repository and forward the
SLO specs to Sloth automatically:

.. code-block:: bash

    juju status --watch 5s

You should see ``cos-config`` transition from ``blocked`` to ``active``:

.. code-block:: text

    App         Version  Status  Scale  Charm
    cos-config  3.6.9    active      1  cos-configuration-k8s

Press ``Ctrl-C`` once it is active. If you want to trigger an immediate sync rather than
waiting for the next scheduled poll, run:

.. code-block:: bash

    juju run cos-config/0 sync-now

Verify the recording rules in Prometheus
-----------------------------------------

Sloth has now generated Prometheus recording rules from your SLO spec. Query Prometheus
directly to confirm the rules are present:

.. code-block:: bash

    juju exec --unit prom/0 -- \
        curl -s http://localhost:9090/api/v1/rules \
        | python3 -c "
    import sys, json
    groups = json.load(sys.stdin)['data']['groups']
    for g in groups:
        if 'sloth' in g['name'] and 'prometheus_k8s' in g['name']:
            print(g['name'])
    "

You should see six rule groups — two alert rule groups and four recording rule groups:

.. code-block:: text

    welcome_k8s_<uuid>_sloth_sloth_slo_alerts_prometheus_k8s_requests_availability_alerts
    welcome_k8s_<uuid>_sloth_sloth_slo_meta_recordings_prometheus_k8s_requests_availability_alerts
    welcome_k8s_<uuid>_sloth_sloth_slo_sli_recordings_prometheus_k8s_requests_availability_alerts

Notice how the rule group names include the model name (``welcome_k8s``) and a short
model UUID — Sloth injects these as labels so rules from different Juju models never
collide.

The **SLI recording rules** (``slo_sli_recordings``) track the error-budget burn rate
over multiple time windows. The **meta recording rules** (``slo_meta_recordings``) expose
metadata such as the SLO objective and the service name as Prometheus metrics. The
**alert rules** (``slo_alerts``) fire when error-budget consumption exceeds thresholds.

View the SLO dashboard in Grafana
----------------------------------

Grafana already has the SLO dashboards installed. First, retrieve the admin password:

.. code-block:: bash

    juju run grafana/0 get-admin-password

The output looks like:

.. code-block:: text

    Running operation 1 with 1 task
      - task 2 on unit-grafana-0

    Waiting for task 2...
    admin-password: <generated-password>
    url: http://grafana-0.grafana-endpoints.welcome-k8s.svc.cluster.local:3000

Use the ``url`` to open Grafana in your browser (you may need to expose it via ingress or
port-forward if you are not on the same network as the cluster). Log in with username
``admin`` and the generated password.

Navigate to **Dashboards → High level Sloth SLOs**. You will see the error budget burn
rates for each SLO across the time windows that Sloth generated. The **SLO / Detail**
dashboard gives a per-SLO breakdown with availability and error-budget consumption graphs.

Clean up
--------

When you are finished, remove the model to delete all the deployed applications and
free the resources:

.. code-block:: bash

    juju destroy-model welcome-k8s --no-prompt

Next steps
----------

Now that you have seen the full SLI/SLO workflow end to end, explore the rest of the
documentation:

- :ref:`how-to-guides-integrate` — add SLOs to a real application charm, including
  how to use the ``SlothProvider`` library for dynamic specs
- :ref:`reference-slos` — full reference for the SLO specification format and all
  available fields
- :ref:`explanation-slos` — background on SLIs, error budgets, and multi-window
  alerting
- :ref:`how-to-guides-configure-slo-periods` — use a non-standard SLO period such as
  7 days or 28 days
