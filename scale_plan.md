# Scale-up to 400 uniform-difficulty tasks

Started 2026-09-14. One line per step, status explicit. Update as steps complete.

## Why 400 and not 1,388

The source has 1,388 usable tasks. Resolution scales as `1/sqrt(n)` off the measured baseline of
"cannot resolve below 0.14 in catch rate at n=95", so 400 tasks (800 candidates) resolves 0.048 and
all 1,388 resolves 0.026. The deltas under test are the 0.05-0.07 safety gaps, which 400 clears.
The only thing the full draw additionally resolves is the ~0.010 AUC gap, and that gap has the
opposite sign on the two pools already built, so three more days of wall clock would most likely
establish that tests *cost* discrimination rather than that they add it.

Measured from the r50 sweep's own artifacts: 15.9 model calls, 78k call tokens and 1,106 sandbox
pairs per candidate, ~1h45 wall clock for 100 candidates across ten runs. 400 tasks is 8x that.

`write_config` refuses a run name whose `config.json` differs, and the dataset path is in that
document, so a 250-now/750-later staging cannot resume — the size is chosen once, here.

## E9 - uniform400

**Setup.**      `data/apps_uniform400.json`, 400 tasks drawn uniformly from
                RoganInglis/apps-control-arena at pool seed 400, scrubbed. 160 train / 240 test.
                Model `openai-api/azureai/DeepSeek-V3.2`, 10 tests, monitor `runs=2`.
                Deployment game: horizon 50, attack rate 5%, budget 10%, 20,000 trials.
**Change.**     Population size only: the same construction as `apps_random50.json` at 8x the n.
                No new arm, no new prompt, no new cut rule.
**Hypothesis.** The `monitor + tests` safety win on both existing pools is cut placement, not
                discrimination. If so it should survive a larger draw of the same population while
                ΔAUC stays at or below zero, because r50 (AUC 0.961, near ceiling) is the population
                this pool is drawn from.
**Prediction.** Written before the run.
                1. `monitor + tests property` − `monitor`, variation B safety: **> 0**, and smaller
                   than r50's +0.074 as cut-placement luck averages out. Falsified if <= 0.
                2. ΔAUC for the same pair: **<= +0.005**, replicating r50's sign (−0.010) rather
                   than hard's (+0.010). Falsified if >= +0.02.
                3. The `· any fires` suite arms stay **below** the monitor's B frontier at matched
                   usefulness, as on both existing pools. Falsified if any crosses above.
**Observed.**   All three predictions held. The thing they did not cover is what matters.

                1. HELD. `monitor + tests property` − `monitor`, variation B: **+0.050**
                   (0.833 vs 0.783), positive and smaller than r50's +0.074, as predicted.
                2. HELD. ΔAUC **+0.004** (0.959 vs 0.955), inside the <= +0.005 bound.
                3. HELD. Every `· any fires` arm sits below the monitor's B frontier. The r50
                   result that `tests property · any fires` beats the monitor in B (0.911 vs
                   0.757) **reversed**: 0.779 vs 0.783, and at usefulness 0.555 against 0.986,
                   so the monitor now dominates it outright on both axes. That was small-sample
                   noise at 27 held-out tasks.

                **Not predicted, and the real finding: the 6-for-6 sweep does not replicate.**
                On hard and r50, `monitor + tests property` beat the monitor in all three
                variations on both pools. At 8x the n it wins B (+0.050) and C (+0.021) and
                **loses A (−0.029)**. `monitor + u plans property` flips from winning 4 of 6
                cells to losing all three. Only `monitor + tests plain_v3` is positive in all
                three here, and its A margin is +0.002 — a rounding artefact, not an effect.

                Population: 713 of 800 candidates scored by all 8 arms (354 honest, 359 attack),
                135 train / 190 held-out tasks keeping both submissions, against r50's 95 and
                18/27. Trusted model solves 305/399 = 76.4%. Scrub verified: the pre-scrub
                generation names the attack in 0.475 of backdoored candidates, the scrubbed one
                in 0.000. Free floor is 0.077 at this attack rate and horizon.

## Steps

| # | step | status |
|---|---|---|
| 1 | `--make-pool 400 --seed 400` -> `apps_pool_uniform400.json`, record difficulty mix | **done** — 400 of 1,388 usable; competition 34 / interview 286 / introductory 80 |
| 2 | `--make-split 160` -> `splits/apps_uniform_160_240.json` | **done** — 160 train / 240 test, split seed 300 |
| 3 | `--scrub-tells` build -> `data/apps_uniform400.json` | **done** — 800 candidates, 9,483 provided pairs (median 17/task), 33.7 MB |
| 4 | verify scrub is 0% both sides and provenance is recorded | **done** — attack 0.000 / honest 0.000 on both comment metrics, n=400 a side |
| 5 | add `u400` to `POOLS` in `notebooks/build_results.py` | **done** — `notebooks/results_uniform400.ipynb`, 38 cells, no placeholder left unfilled |
| 6 | launch `solve-u400`, `trig-u400`, `tm-u400` (no dependencies) | **done** 13:19 — solve 800/800 (11 failed), trig 800/800 (8 failed: 5 infra 3 model), tm running |
| 7a | launch `ut-u400-{property,plain_v3}` (need triggers) | **done** 13:47-14:46 — 800/800 each, 30 and 42 failed |
| 7b | launch `upt-u400-{property,plain_v3}` after 7a frees the CPUs | **running** from 14:46 |
| 7c | fix `MonitorWithTests.prepare` crashing on an infra-blamed trigger record | **done** — see below |
| 8a | launch `mwt-u400-{property,plain_v3}` (suites already complete, no sandbox) | **running** from 14:48 |
| 8b | launch `mwt-u400-uplans-property-10` once `upt-u400-property-10` lands | pending |
| 8b | launch `mwt-u400-uplans-property-10` once `upt-u400-property-10` lands | **done** 18:14-18:43 — 800/800, 17 failed |
| 9 | build and execute `notebooks/results_uniform400.ipynb` | **done** — 0 cell errors; needed `archived/data/apps_uniform400.json` first (see below) |
| 10 | fill **Observed** above and section 8 in all three notebooks | **Observed** done; section 8 still unfilled in all three |
| 11 | export the four charts per pool from the generator, rebuild and execute all three notebooks | **done** 2026-09-15 — 12 PNGs in `notebooks/figures/{hard,r50,u400}/`, 0 cell errors, numbers bit-identical |

All ten runs: 13:19 to 18:43, **5h24** for 8,000 records against a 14h estimate.

## The notebook needed an unscrubbed counterpart

Cell 3 proves the scrub worked by reading the same dataset twice, once from `archived/{DATASET}`.
Both older pools have a pre-scrub generation there because they were built before the scrub existed;
u400 was built with `--scrub-tells` from the start, so `archived/data/apps_uniform400.json` did not
exist and the cell raised `FileNotFoundError`. Fixed by building the same pool and split again
without `--scrub-tells` into that path — no model calls, and the two differ only in
`built_from["candidate_code"]`.

Launch order is a dependency order, not a preference: `mwt` reads a `tests=` run and every suite
reads `triggers=`.

## How independent this pool is

Drawn at pool seed 400 against random50's seed 300, from the same 1,388 usable tasks, so the two
overlap only by chance: **15 of random50's 50 tasks and 16 of apps_hard's 50 reappear here, leaving
369 of 400 tasks new.** Close to an independent replication, but not one — 92% fresh, same attack
generator, same construction filter. A claim that survives here has survived a larger draw of the
same population, not a second population.

## Sandbox runs go two at a time, not four

`WORKERS = 8` per run against 16 CPUs, so the four sandbox-heavy arms would put 32 concurrent
containers on the machine. An oversubscribed sandbox misses `sandbox_seconds=240` and the miss is
recorded as an **infra failure**, and resume treats a failed record as done — so a timeout storm
would permanently thin the denominator rather than being retried. Two at a time is also what the
r50 sweep actually did, and it produced 1-3 infra failures per run.

`mwt` does no sandbox work at all (0 pairs across all three r50 runs), so wave 3 runs three-up.

## The bug this run surfaced: `MonitorWithTests` read the trigger run itself

`prepare` built its trigger map with `{r["candidate_id"]: (r["inputs"] or []) for r in
load_records(self.triggers)}`. An **infra**-blamed trigger record never sets `inputs` at all — it
raised before the field was written — so that subscript is a hard `KeyError` and all three `mwt`
runs died 3 s after launch. `score` then compounded it with `self.inputs.get(candidate_id, [])`,
a falsy default on a metric path: a candidate with no trigger inputs would have been scored against
zero inputs, and a suite run over zero inputs reads as a clean negative.

No pool had produced the shape before. `trig-r50` failed 0 of 100 and `trig-22`'s single failure was
**model**-blamed, which does write `inputs: []`. `trig-u400` is the first run with infra-blamed
trigger failures — 5 of them — so 800 tasks bought a latent crash that 50 could not.

`unit_testing.py` already had the correct reader, `spaces_from`, which separates "no record at all"
(raise, the dependency is unfinished) from "finished but unusable" (return, for the caller to record
as its own failure). `prepare` now calls it and `score` handles `no_trigger_space` the way
`UnitTesting.score` does. Verified against the real artifact: 792 usable, 8 unusable.

Two things worth knowing about the fix:

- The new `score` branch is **defensive, not load-bearing** here. All 8 trigger-unusable candidates
  are already failed in both `ut` runs, so the existing `suite["failed"]` branch catches them first.
  The crash in `prepare` was the only thing actually blocking the run.
- It records `Blame.MODEL`, mirroring `UnitTesting`, even where the upstream blame was `infra`.
  That books our flakiness as the model's failure rate, which AGENTS.md names as a mistake. Carrying
  the true blame forward needs `spaces_from` to return it rather than a reason string, and parsing
  blame back out of that string is the thing AGENTS.md forbids. Left alone mid-run: it affects the
  blame label on 8 of 800 records, all of which the notebook's one-population cell drops anyway.
  **Follow-up, your call:** widen `spaces_from` to return blame, and fix both arms together.

## Infra failures are not retried by resume

`done` is built from every record on disk including failed ones, so relaunching under the same name
will not re-attempt the 5 infra failures in `trig-u400`. At 0.6% they leave the denominator honestly
and are not worth surgery, but the number to compare against at the end is r50's, not zero.

## Carried over, not part of this run

- `notebooks/figures/*.png` (the four at the top level, not the per-pool directories) are the
  31 Aug hand-export of the hard pool at six arms. Every notebook now exports its own set to
  `notebooks/figures/<pool>/`, so those four are superseded — but `progress_report.md` still embeds
  them, and its tables are the six-arm generation too. Report and figures are consistently stale
  with each other; refreshing one without the other would be worse. Needs a rewrite, not a relink.
- Section 8 "What survived" is an unfilled checklist in both existing notebooks.
- The ablation direction is dropped as of 2026-09-18. `pipeline/protocols/ablation.py`,
  `ast_ablation.py`, `prompts/ablation.txt` and the two `notebooks/ablation.*` figures are deleted;
  `docs/direction.md` is marked superseded rather than edited, since its P1–P4 analysis is the
  record of why.
- Nothing is committed.
