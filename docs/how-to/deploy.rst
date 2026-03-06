.. meta::
    :description: How to deploy `sloth-k8s`.

.. _how-to-guides-deploy:

How-to deploy `sloth-k8s`
=========================

**Prerequisites**: Ensure you have a Juju k8s model up and running with COS or at least a `grafana-k8s` and a `prometheus-k8s` charm deployed.

To deploy `sloth-k8s`, follow these steps:

:code:`juju deploy sloth-k8s sloth`

Wait for the `juju status` to show that the `sloth` application is active\idle.

Next, relate `sloth-k8s` to `prometheus-k8s` and `grafana-k8s`:

.. code-block:: bash

    # if grafana and prometheus aren't integrated already:
    juju integrate prom:metrics-endpoint grafana:metrics-endpoint
    juju integrate prom:grafana-source grafana:grafana-source
    juju integrate prom:grafana-dashboard grafana:grafana-dashboard

    # integrate sloth with prometheus and grafana
    juju integrate sloth:metrics-endpoint prom:metrics-endpoint
    juju integrate sloth:grafana-dashboard grafana:grafana-dashboard

Now `sloth-k8s` is deployed and integrated with your monitoring stack.

At the moment Sloth does not have a user interface.
In order to start using SLIs and SLOs you'll have to configure Sloth by integrating it over the
`slos` interface with a charm that can provide SLO definitions to it.

For instructions on how to provide SLO definitions to `sloth-k8s`, see :ref:`how-to-guides-integrate`.
For a reference example of the SLO specification format, see :ref:`reference-slos`.
