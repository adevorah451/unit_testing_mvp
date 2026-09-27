# `tests/`

Run with `.venv/bin/python -m pytest -q` — **5 passed**, ~5 s. `pytest.ini` sets `pythonpath = .`
and `testpaths = tests`, so imports are the same here as at the repo root.

## Five tests, and why not four hundred

`test_end_to_end.py` is the whole suite: one test per protocol — `TM`, `TriggerSearch`,
`UnitTesting`, `TrustedSolve` — plus `test_a_real_detached_run_rebuilds_itself_in_the_subprocess`,
which drives the real `Run.run()` on `mockllm/model` and is the only coverage `launch.py` has.
`MonitorWithTests` and `UPlansTWrites` have no test; the gap is tracked in `REVIEW_PLAN.md`.
Metrics live in the generated `notebooks/results*.ipynb` cells and nothing here touches them.

The per-module suite this replaced was **484 tests, green**, while `UnitTesting` could not parse a
single answer its own prompt asked for. `prompts.OUTPUT_SPLIT_TESTS` demands *"A single JSON object
matching the schema you have been given. No prose, no fences"*; `parse.parse_properties` reads a
fenced code block. Handed that object it finds none — 0 of the 566 stored answers under `runs/m-*`
recovered, booked as a 100% model-failure rate against the thing under study. Every unit was
correct. The fixtures never caught it because they fed canned completions straight to the parser,
so the round trip from prompt to parser was the one path nothing ran.

The policy that follows is about where a double is installed, not how many tests there are.

- **Fake at the process boundary, never between two of our own modules.** The scripted `Client`
  replaces `resolve` and `Script.run_raw` replaces `sandbox.run_raw` — the provider and Docker.
  Everything from the rendered prompt to the record on disk is the real code.
- **Where the fixture would be asserting against itself, replace Docker and keep the harness.**
  `TrustedSolve`'s property is the oracle — an expected-output table we build, embed in the harness
  source and key back into inside the container — so a canned grid would decide every outcome the
  test then checks. `Script.run_raw` runs that one grid for real under `Isolation.SUBPROCESS` and
  records the isolation it was *asked* for, which is what pins Docker on the real path.
- **Write a canned answer in the shape the provider returns, not the shape the parser wants.**
  `suite_answer()` is the schema object; `trigger_answer()` is one JSON value per line. A fixture
  written backwards from the parser cannot fail the way the run failed.
- **Patch every spelling of a name.** `tm` imported `resolve`; the other two reach it through
  `pipeline.model`. Patching one leaves a protocol calling the real endpoint — which is why the
  model string is `mockllm/...` besides: `TrustedModel` short-circuits any name starting with
  `mock` to a canned `SUSPICION_SCORE: 3` before a client is built, so a missed call site is free
  rather than billed.
- **A fifth test has to name a boundary the four do not already cross.** Splitting one of them into
  per-module cases is how the suite got to 484.

## What `conftest.py` already does for you

Two autouse fixtures, so you do not write either by hand:

- `isolate_working_directory` chdirs every test into its own `tmp_path`. `data.RUNS_DIR` is
  `Path("runs")` — relative, resolved against the CWD each time a `Run` touches it — so without
  this a test writes `runs/<name>/{config.json,records.jsonl}` into the real `runs/`, and
  `write_config` then either refuses the name or the run resumes leftovers it did not write. This
  matters more than it used to: verification agents have written scratch runs into the real
  `runs/`, whose six `m-*` arms are the numbers the port is required to reproduce and cannot be
  re-measured for free.
- `restore_environment` snapshots and restores `os.environ`, because `model.resolve` calls
  `os.environ.setdefault` on six credential variables and that outlives the test that triggered it.

`settings_from_cli` is gone with `pipeline/config.py`, and so is `Settings`. A run's configuration
is its constructor arguments; `write_config()` serialises them to `runs/<name>/config.json` and
`Run.from_config` rebuilds the class from that document.

## What to test, and where the double lives

- **A test double belongs in the test that uses it.** `Script` and `Client` are defined in
  `test_end_to_end.py`. The counterexample was `pipeline/backends/mock.py`, a library module whose
  only remaining caller was a test; it is deleted.
- **Assert against the module's own constant, never a hand-copy.** The `UnitTesting` test checks
  `"fail" not in sandbox.RECORD_OUTCOMES`, because a grid agreeing only with the protocol is what
  let a filter for `"fail"` — an outcome the sandbox has never emitted — score every suite clean.
- **Name the contract in the test name.**
  `test_a_real_detached_run_rebuilds_itself_in_the_subprocess` says what breaks; `test_run_2` does
  not.
