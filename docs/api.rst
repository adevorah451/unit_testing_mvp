API reference
=============

Bodies are generated from the docstrings in the source, so no entry below can drift from the code.
The *list* of modules is hand-maintained and can: it omitted three protocols between August and
2026-09-18. **A new module under ``pipeline/`` gets an ``automodule`` block here in the same diff
that adds it.**

``pipeline.data`` — the dataset, its split, and what a prompt may see
---------------------------------------------------------------------

``Task.blind()`` returns exactly the fields a prompt may contain — ``entry_point``,
``io_mode``, ``main_task``, ``provided_tests`` — so a protocol that renders a prompt
cannot reach ``reference_solution`` or ``secret_input`` by accident.

.. automodule:: pipeline.data
   :members:

``pipeline.protocols.base`` — ``Run``, the spine every protocol shares
-----------------------------------------------------------------------

A run serialises itself to ``runs/<run_name>/config.json`` and the subprocess rebuilds
the same class out of that file. ``probe.json`` carried no self-describing config, so an
arm's meaning lived in a dict in whichever script happened to read it, and those dicts
drifted until three published premises were wrong.

.. automodule:: pipeline.protocols.base
   :members:

``pipeline.protocols.tm`` — the trusted monitor
-------------------------------------------------

.. automodule:: pipeline.protocols.tm
   :members:

``pipeline.protocols.trigger_search`` — the inputs a suite is later run over
-----------------------------------------------------------------------------

.. automodule:: pipeline.protocols.trigger_search
   :members:

``pipeline.protocols.unit_testing`` — one suite against one submission
------------------------------------------------------------------------

.. automodule:: pipeline.protocols.unit_testing
   :members:

``pipeline.protocols.u_plans_t_writes`` — the authoring call split across two models
--------------------------------------------------------------------------------------

The untrusted model sees the submission and returns a plan in prose; the trusted model writes the
suite from that plan and never sees the submission. Judged by ``unit_testing.suite_verdict``, the
same function, so this arm cannot drift from the single-call arm on what a clean negative is.

.. automodule:: pipeline.protocols.u_plans_t_writes
   :members:

``pipeline.protocols.monitor_with_tests`` — the monitor, shown what the suite did
-----------------------------------------------------------------------------------

Same scale and same parser as ``tm``, so one threshold sweep reads both. ``suite_shown`` orders the
firing tests first and never truncates one: a firing whose assertion was cut out of the prompt
leaves the monitor judging a test by its name.

.. automodule:: pipeline.protocols.monitor_with_tests
   :members:

``pipeline.protocols.solve`` — the trusted model's own attempt
----------------------------------------------------------------

The only protocol that never sees a candidate: it answers the statement and nothing else, so its
program owes nothing to the submission it is later compared against. Two uses downstream — the
defer-to-trusted baseline, and the oracle the soundness filter judges a suite against.

.. automodule:: pipeline.protocols.solve
   :members:

``pipeline.model`` — one runtime in, one completion out
---------------------------------------------------------

.. automodule:: pipeline.model
   :members:

``pipeline.prompts`` — rendering
----------------------------------

.. automodule:: pipeline.prompts
   :members:

``pipeline.parse`` — reading one completion
---------------------------------------------

.. automodule:: pipeline.parse
   :members:

``pipeline.sandbox`` — the sandbox
------------------------------------

The only place untrusted candidate code runs. A firing test is ``outcome == "catch"``;
there is no ``"fail"``, and filtering for one scores every suite clean.

.. automodule:: pipeline.sandbox
   :members:

``pipeline.launch`` — starting a run detached
-----------------------------------------------

.. automodule:: pipeline.launch
   :members:
