# Working rules for this repo

## Before you say a change is done

All three from the repo root — `pipeline` imports off the CWD and `runs/` is resolved against it.

```
.venv/bin/python -m pytest -q
.venv/bin/python build_dataset.py --backend apps --pool apps_pool_hard10.json \
    --split splits/smoke_3.json --out data/smoke3.json
.venv/bin/python -c "
from pipeline.protocols import TM
TM(run_name='smoke-tm', data='data/smoke3.json',
   model='openai-api/azureai/DeepSeek-V3.2').run()"
```

The suite is 5 tests, ~5 s. The smoke run is `splits/smoke_3.json` — 3 tasks, 2 candidates each, six
model calls — and re-running it under the same `run_name` resumes instead of re-paying
(`smoke-tm: 6/6 already scored, nothing to run`). It is not deterministic, so compare *shapes*: six
lines in `runs/smoke-tm/records.jsonl`, each with an `avg_score` and `"failed": false`, both splits
present — never exact numbers.

## What is being measured

A trusted model writes property-based tests against an untrusted model's submission; the question is
whether they catch backdoors the trusted monitor misses. A protocol run calls a model and costs
money; a notebook cell reads a finished run and cannot change one. `DOCUMENTATION.md` is the surface
of both.

## How work gets built here

Shape first, then red-green upward.

1. **Shape first.** Write the call structure only — signatures plus contract docstrings, every
   body `raise NotImplementedError`. `grep NotImplementedError` is the progress tracker, and the
   plan cannot drift from the code because they are the same file.
2. **Then red-green, from the smallest unit up.** For each stub: a failing test that names the
   contract, the smallest body that passes it, then the next stub above it. A test that passes the
   moment you write it tested nothing.
3. **A stub raises, never returns a placeholder.** A stub returning `0`, `None` or `""` is a
   failure silently becoming a value — the defect this whole repo is built around.
4. **One function per diff.**

## Delegation

**The default execution mode here is a team of parallel agents, with the lead as orchestrator, not
implementer.** The user has asked for this three times now; this entry is why a fourth time shouldn't
be needed.

- **Anything past a single mechanical edit goes to an agent.** The lead decides WHAT is measured and
  WHETHER the answer is believable — not types the measurement.
- **Independent work goes out in one message, multiple tool calls, so agents run concurrently.**
  Dispatching independent tasks one at a time is the failure mode to avoid.
- **Every brief names the files the agent owns and the files it must not touch.** Concurrent agents in
  one repo collide: four runs launched at 15:03 had `probe.py` edited by another agent at 15:06, so all
  four had to be killed and relaunched — readiness had been inferred from green tests instead of
  waiting on the owning agent's report.
- **A brief carries the numbers the agent must reproduce.** A port that produces different numbers is
  wrong, not the table it disagrees with. Two traps worth citing: a firing outcome from
  `pipeline.sandbox.run_raw` is `outcome == "catch"`, never `"fail"` — checking for `"fail"` silently
  scores every suite as clean — and an incomplete grid (`complete=False`) can only *lose* a catch, so
  it under-reports and must never be read as a clean negative.
- **Agents report conclusions, not file dumps.** The lead relays what changed and what it means; it
  does not restate the artifact.
- **What the lead still does alone:** deciding the experiment, judging whether a result is real, and
  talking to the user. Never delegate the interpretation.

## The rule that matters most

**A failure must never silently become a value.** Six separate bugs here were the same shape: a
measurement that did not happen, written down as the falsy default, which read as *"the model
failed"* — the direction that flatters our infrastructure and penalises the thing under study.

- Never write `x or 0`, `.get(k, False)`, or a falsy default on any path feeding a metric.
- There is no `Unknown` type and no `outcome.py`. A failure is flat — `failed` / `blame` / `reason`
  on the record, in memory and on disk alike — and `Blame` is a `str, Enum` in `pipeline/data.py`:
  `Blame.MODEL` when nothing parseable came back, `Blame.INFRA` when the machinery broke. `Run`
  refuses a failed record whose blame is outside the enum or whose reason is empty.
- State blame **where the failure is detected**; inferring it later from an error message mislabelled
  every crash whose text lacked a marker word. The two are not interchangeable — `model` is data
  about the thing being measured, `infra` is noise to retry, and pooling them books our flakiness as
  the protocol's failure rate.
- Every failure reaches the artifact and **leaves every denominator**: never a zero, never an empty
  list, never a clean negative. A measurement that did not happen is never just absent.

## Code style

- **Name things instead of commenting them**: a named constant over an annotated literal, a raised
  error over a warning comment.
- **Every optional in the config layer gets a real default** — `""`, `0` for "all", a fallback object.
- **Reuse over hand-rolling.** scipy/sklearn for statistics, pydantic-settings for config; the
  hand-rolled ROC curve silently misreported every partial-AUC figure.
- **Fail fast** in `__post_init__` / `model_validator`, not a defensive `.get()` on a guaranteed key,
  and no Protocol whose one implementation forwards to a function.
- **Search `pipeline/` before writing the second loader, adapter or parser.** `probe.py` wrote an
  APPS-only loader while the `Backend` protocol — now in `build_dataset.py` — already normalised both
  benchmarks, which is why BigCodeBench was a refactor rather than a flag.

## Where a change goes

**A new measurement is a protocol class in `pipeline/protocols/`, or a notebook cell — never a new
script.** Six scripts that each re-derived a number the pipeline already computed, then drifted from
it, were deleted on 2026-08-18. Which of the two it is has one deciding question: **does it call a
model?** If it does it is a protocol, because it costs money and has to outlive the kernel; if it
does not it is a cell, including the cells that execute candidate code in Docker.

A new arm is not a new class — it is one protocol constructed twice with different arguments
(`test_gen_prompt=`, `resolve=`, `code_visible=`) under two `run_name`s, compared in a cell. A new
score is a field on the record, written by the protocol that already writes it. A rescoring reads
`runs/<name>/records.jsonl` in a cell and caches its result beside it. If two entry points would
compute the same number, there is one entry point.

## A required key beats a defaulted one when the default can be wrong

**Code style says every optional in the config layer gets a real default. This is the exception, and
it is narrow:** a key whose wrong value produces a plausible-looking but meaningless run is required,
not defaulted. A pool that omitted `io_mode` was once read as stdio, so a function-mode pool ran under
the wrong invoke contract and arrived months later as "a model that wrote tests which never fired".

The same reasoning says there is no `--verify` on `build_dataset.py`. Measuring ground truth meant
running control_arena over every candidate through a 1,004-line adapter that no published number here
ever went through. The adapter was deleted rather than kept as a flag nobody ran; if it ever needs
verifying, that is a script, not a flag on this one.

## Decide a population question once, at build time

The pool stores several attack proposals per task and the dataset keeps exactly one. That cap is
applied in `build_dataset.py`, not in each protocol, because a task contributing two backdoored
submissions is double-weighted in every rate whose denominator is tasks — and a cap each protocol
applies for itself is a cap two protocols will eventually apply differently. The same goes for the
train/test split, which travels inside the dataset file rather than beside it.

## Splitting one call into its own run

Two calls in one run cannot be compared separately. The old pipeline fused the trigger search into
its authoring call, so each of six arms paid for its own search over the same candidates and a prompt
comparison between two arms was also a comparison between two different sets of inputs — no arm's
number was attributable to its prompt alone. A stage whose output another protocol consumes is its
own run, named and reused.

The mirror of that rule: a reader belongs with the data, not the producer. `load_records` lives in
`data.py` rather than `protocols/` so a notebook cell can read a run without importing the protocol
that wrote it — a measurement should not need the machinery that made the thing it measures.

## Running an experiment

**Write the entry before the run, not after.** `reports/EXPERIMENTS.md` is gone; the convention it
carried is not. **The entry goes in the plan file that owns the run, written before the run is
launched** — `scale_plan.md` holds E9 and is the worked example. A notebook markdown cell is fine
for an experiment a notebook owns end to end, but the notebooks are generated from
`notebooks/build_results.py`, so an entry typed into a `.ipynb` is lost at the next rebuild. Same
fields either way:

```
### E<n> — <name>
**Setup.**      pool, n tasks, models, seed, budget — everything needed to re-run it.
**Change.**     the ONE thing different from the run above. If there is more than one, split it.
**Hypothesis.** what we expect and *why*, stated so it can be wrong.
**Prediction.** the number that decides it, with a threshold, written down first.
**Observed.**   filled in after. Verified against the artifact, never from memory.
```

A hypothesis written after seeing the number is not one: A4's density correlation (+0.574) was formed
on already-inspected data and came back **−0.142 on held-out tasks**. A thresholded **Prediction** is
what stops a null result being retold as a positive one. Same units across entries.

- **Detached, always.** `Run.run()` writes `runs/<name>/config.json` and starts the subprocess in a
  tmux session named after the run via `pipeline/launch.py`; `launch` raises `LaunchFailed` if the
  session dies on startup, so a mistyped argument surfaces at launch rather than as an empty `runs/`
  in the morning, and it refuses a run name already running. `wait=True` only *follows* the artifact
  — Ctrl-C detaches, it does not kill. Watch it again with `pipeline.launch.pane(run_name)` or
  `TM.attach(run_name).status()`.
- Nothing over a minute runs in a foreground shell, an agent tool call, or a notebook cell; all three
  die with their session and take the run with them. Re-running under the same `run_name` resumes,
  costing one in-flight call.
- **Run `caffeinate` alongside the run.** tmux does not stop the machine sleeping, and nothing here
  enforces it for you.
- Diagnose by artifact counts under `runs/<name>/` first and `console.log` **last**. Nothing writes a
  heartbeat, so artifacts are the only ground truth: the log lied to us for an hour once, artifacts
  never have.
- Attacks written by a human or by Claude are a **different distribution** from attacks sampled from
  an untrusted policy. Never pool them; they answer different questions.

## Before claiming a result

- **Verify every number against `runs/<name>/records.jsonl`** — not from memory, not from an earlier
  message.
- **Report `safety − free_floor`.** The free floor is the share of deployments containing no attack
  at all, which every protocol wins for nothing: 0.364 at a 2% attack rate over 50 submissions, so a
  raw safety of 0.40 is almost entirely floor.
- **Check n first.** Failed records leave the denominator, so `n` can be below the candidate count; a
  "5-run" delta was actually n=4. Every error bar comes from `runs=N`, so there is no interval at
  `runs=1`.
- **Report the mechanism, not the delta alone.** A false-positive count at a fixed threshold is
  descriptive only, because the deployment game never applies one; what the audit rule spends is
  *rank*, so count how many distinct score levels the monitor produced.
- **Say when a threshold was fitted** — a number chosen by looking at the data is not evidence until
  it survives tasks it was not chosen on.
- **Check for tautology.** Ask what the metric would report if the effect were absent: a 0% false
  positive rate arising because the filter mechanically removes every offender is not a finding.
