.. _how-to-guides-integrate:

Integrate with Sloth
====================

This guide explains how to provide SLO specifications to `sloth-k8s`.

There are two approaches to integrate with Sloth:

1. **Via** ``cos-configuration-k8s`` **(recommended)** — deploy a dedicated configuration charm
   that reads SLO spec files from a git repository and forwards them to Sloth. No changes to
   your application charm are needed.
2. **Via the SlothProvider library** — add code to your charm so it sends SLO specs directly
   over the ``sloth`` relation. Use this when your charm needs to generate or parametrize SLO
   specs dynamically at runtime.


Option 1: Integrate via ``cos-configuration-k8s`` (no charm changes required)
-------------------------------------------------------------------------------

`cos-configuration-k8s <https://charmhub.io/cos-configuration-k8s>`_ is a Canonical charm that
periodically syncs a git repository and forwards its contents to COS components, including
``sloth-k8s``. This is the recommended approach when you want to add SLOs to an existing
deployment without modifying any application charm.

1. Write your SLO specification files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create one or more YAML files in the `Sloth format <https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1>`_
and commit them to a git repository, inside a directory (default: ``slos/``). For example:

.. code-block:: yaml

    # slos/my-service.yaml
    version: "prometheus/v1"
    service: "my-service"
    labels:
      team: my-team
    slos:
      - name: "availability"
        objective: 99.9
        description: "99.9% of requests succeed"
        sli:
          events:
            error_query: 'sum(rate(http_requests_total{status=~"5.."}[{{.window}}]))'
            total_query: 'sum(rate(http_requests_total[{{.window}}]))'
        alerting:
          name: MyServiceHighErrorRate

See :ref:`reference-slos` for the full field reference and annotated example.

2. Deploy and configure ``cos-configuration-k8s``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    juju deploy cos-configuration-k8s cos-config
    juju config cos-config \
        git_repo=https://github.com/your-org/your-repo \
        git_branch=main \
        slos_path=slos

``slos_path`` is the path inside the repository where the SLO YAML files live (default: ``slos``).
For private repositories, set ``git_ssh_key_secret`` to a Juju secret containing your SSH private
key.

3. Relate ``cos-configuration-k8s`` to ``sloth-k8s``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

    juju integrate cos-config:sloth sloth:sloth

``cos-configuration-k8s`` will sync the repository and forward all SLO files it finds under
``slos_path`` to ``sloth-k8s``. To force an immediate sync without waiting for the next
``update-status`` hook, run:

.. code-block:: bash

    juju run cos-config/0 sync-now


Option 2: Integrate via the SlothProvider library
--------------------------------------------------

Use this approach when your charm generates or parametrizes SLO specs at runtime (for example,
based on charm config or relation data).

1. Add the SLO relation endpoint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In the charm's ``charmcraft.yaml``, under ``provides``, add:

.. code-block:: yaml

   provides:
     slos:
       optional: true
       interface: sloth
       description: |
         Sends SLOs (Service Level Objective) specifications to a Sloth charm.

2. Install the Sloth library
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add the library to the charm's dependencies. For example, with ``uv``:

.. code-block:: bash

    uv add charmlibs-interfaces-sloth

Then update the lockfile (e.g. ``tox -e lock``, ``just lock``, or ``poetry lock``).

3. Set up the SlothProvider
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Import ``SlothProvider`` and wire it up in the charm:

.. code-block:: python

   import ops
   from charmlibs.interfaces.sloth import SlothProvider

   SLOS_RELATION_NAME = "slos"

   class MyCharm(ops.CharmBase):
       def __init__(self, *args):
           super().__init__(*args)

           self.sloth = SlothProvider(self, SLOS_RELATION_NAME)
           # Observe relation changes to send SLOs. Add further observations
           # (e.g. config-changed) if _get_slo_spec depends on charm config.
           # If the charm has a reconciler, call _send_slos from there instead.
           self.framework.observe(self.on.slos_relation_changed, self._send_slos)

       def _get_slo_spec(self) -> str:
           """Return the SLO specification as a YAML string in Sloth format."""
           # OPTION A: Let the operator paste a raw Sloth spec as a config option.
           if spec := self.config["slos"]:
               return spec

           # OPTION B: Read a static spec bundled with the charm.
           with open("./slos/slo_spec.yaml", "r") as f:
               return f.read()

           # OPTION C: Pick a preset based on charm config.
           preset = self.config["preset"]
           with open(f"./slos/{preset}_slo_spec.yaml", "r") as f:
               return f.read()

           # OPTION D: Fill in a template with charm config values.
           with open("./slos/slo_spec_template.yaml", "r") as f:
               template = f.read()
               return template.replace("__OBJECTIVE__", self.config["objective"])

       def _send_slos(self, event: ops.RelationChangedEvent):
           self.sloth.provide_slos(self._get_slo_spec())

The crucial design decision is how to implement ``_get_slo_spec``. SLO objectives often vary
significantly across deployments (the same database can be mission-critical or best-effort
depending on context). We recommend always providing a fallback that accepts a raw Sloth YAML
string as a config option, and optionally adding opinionated presets or a template for operators
who prefer not to write the spec from scratch.

Add any config options to ``charmcraft.yaml`` accordingly:

.. code-block:: yaml

   options:
     slos:
       description: |
         SLO specifications to send to Sloth, as a YAML string in Sloth format.
         See https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1.
       type: string
       default: ""

4. Create SLO specification files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Write the YAML spec file(s) in the format expected by Sloth. See the
`Sloth documentation <https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1>`_ for the
full specification. Save them under ``./src/slos`` and ensure the path matches the one used in
``_get_slo_spec``.

5. Testing
~~~~~~~~~~~

At a minimum:

- Write a unit test that verifies ``_get_slo_spec`` returns a valid non-empty string.
- Write state-transition tests (using ``ops.testing``) that verify:

  - When ``relation-changed`` fires, the output state's ``slos`` databag contains the expected
    spec.
  - When relevant config parameters change, the relation databag updates accordingly.

For end-to-end coverage, consider an integration test that deploys the charm together with
``sloth-k8s``, ``prometheus-k8s``, and ``grafana-k8s``, and verifies that the SLOs are correctly
received, processed, and forwarded.
