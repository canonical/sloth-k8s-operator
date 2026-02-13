Integrate with Sloth
====================

This guide explains how to integrate a charm with Sloth.


Integration Steps
-----------------

1. Add the SLO Relation
~~~~~~~~~~~~~~~~~~~~~~~

If the charm doesn't already have a Sloth integration, add to the charm's ``charmcraft.yaml`` file, under ``requires``, a new section:

.. code-block:: yaml

   slos:
     optional: true
     interface: slo
     description: |
       Sends SLOs (Service Level Objectives) specifications to a Sloth charm.

2. Install the Sloth Library
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Ensure the Sloth library is installed. For example, if you are using ``uv``, run ``uv add charmlibs-interfaces-sloth`` in the charm's directory.

After that, you might want to update the lockfile; for example with ``tox -e lock``, ``just lock``, or ``poetry lock``.

3. Set Up the Sloth Interface
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In the charm's code, import the SlothProvider object and set it up:

.. code-block:: python

   import ops
   # import the relation endpoint wrapper
   from charmlibs.interfaces.sloth import SlothProvider
   from ops import CharmBase

   SLOS_RELATION_NAME = "slos"

   class MyCharm(CharmBase):
       def __init__(self, *args):
           super().__init__(*args)

           # instantiate the provider
           self.sloth = SlothProvider(self, SLOS_RELATION_NAME)
           # observe relation changes to send SLOs; you might want to observe more events depending on how
           # you implement _get_slo_spec (for example, if the SLO spec depends on charm config, you'll need
           # to observe `config-changed` as well)
           # if the charm has a reconciler, you can call _send_slos from there directly instead
           # of observing relation/config-changed events.
           self.framework.observe(self.on.slos_relation_changed, self._send_slos)

       def _get_slo_spec(self) -> str:
           """This function should return the SLO specification in the format expected by Sloth (as yaml string)."""
           # You can read it from a file or generate it dynamically.
           # The implementation of this function will depend on how you want to expose the SLOs to your users and how
           # customizable they should be. These are some examples of how you could implement this function:

           # OPTION 1: The spec could be static and always the same, so you just read it from a file:
           with open("./slos/slo_spec.yaml", "r") as f:
               return f.read()

           # OPTION 2: you could have different presets and select one based on charm config:
           preset = self.config["preset"]
           with open(f"./slos/{preset}_slo_spec.yaml", "r") as f:
               return f.read()

           # OPTION 3: the file could be a template to be parametrized with charm config values
           # (for example, separate config options for each objective):
           with open("./slos/slo_spec_template.yaml", "r") as f:
               template = f.read()
               rendered_template = template.replace(
                   "objective1", self.config["objective1"],
               )
               return rendered_template

           # OPTION 4:
           # finally, you could ask the user to input the whole spec as a config option and just return it.
           # this gives the user full flexibility but also requires them to know the Sloth format and to write the
           # spec themselves, so it might not be the best user experience.
           return self.config["slo_spec"]

       def _send_slos(self, event: ops.RelationChangedEvent):
           spec = self._get_slo_spec()
           self.sloth.provide_slos(spec)

The crucial part here is deciding how to implement ``_get_slo_spec``. You have to choose how the charm should expose to its users the objective values and how customizable they should be.
Often, the objective values can vary dramatically based on the deployment that the charm is used in (the same database software could be super-critical, important, or just a nice-to-have depending on the context).
You should decide how much freedom and control you want to give to the users and implement ``_get_slo_spec`` accordingly.

4. Create SLO Specification Files
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The last part is to write the actual spec file(s) that will be sent to Sloth. The format of the spec should be the one expected by Sloth. You can find examples of this format and a full specification in the `Sloth documentation <https://pkg.go.dev/github.com/slok/sloth/pkg/prometheus/api/v1>`_. If your SLO specifications are currently in a different format (like Pyrra), you'll need to convert them to the Sloth format.

Once you have specs in the Sloth format, save them in ``./src/slos``. Make sure the path to the spec file(s) matches the one used in the ``_get_slo_spec`` function.

5. Testing
~~~~~~~~~~~~

Add tests following the charm's practices and conventions. At a minimum we recommend you to:

- Write a unittest that verifies that ``_get_slo_spec`` returns a valid string.
- Depending on the ``_get_slo_spec`` implementation, write state-transition tests that verify:

  - when relation-changed fires, the output state's ``slos`` databag contains the expected SLO spec.
  - when relevant config parameters (if any) change, the contents of the relation databag change accordingly.

Further you could write an integration test that deploys the charm together with a Sloth charm, Prometheus and Grafana and verifies that the SLOs are correctly received, processed by Sloth, and forwarded to Grafana.