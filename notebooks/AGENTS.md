# `notebooks/`

**Calls a model → a run. Everything else → a cell.** Every metric is computed in a cell: the
threshold sweep (§5), the deployment game (§6), the budget × attack-rate surface (§7). This file
used to say the opposite — *"a notebook reads artifacts; it never produces a number"* — and that
rule died with the three scripts it routed numbers through. `probe.py`, `audit.py` and
`analyse_probes.py` are deleted; what it was protecting is now held by the run directory, which
carries its own `config.json` and so needs no external module to say what an arm meant.

## The notebooks are generated, never hand-edited

`notebooks/build_results.py {hard|r50|u400}` writes `results.ipynb`, `results_random50.ipynb` and
`results_uniform400.ipynb` from **one** cell list, so the pools cannot drift apart. Edit the
generator and rebuild; an edit made in a `.ipynb` is lost at the next build and silently makes that
pool disagree with the other two. Run names differ per pool and come from `POOLS` via `@@key@@`.

```
.venv/bin/python notebooks/build_results.py hard
PYTHONPATH=$PWD .venv/bin/python -m jupyter nbconvert --to notebook --execute --inplace \
    notebooks/results.ipynb
```

Executing costs nothing: every `.run()` finds its records already on disk and returns.

## Why the line sits at "calls a model"

Only a model call has to outlive the kernel. The passes that execute code in Docker do not, and the
cost is measured, not assumed:

| pass | cost |
|---|---|
| trigger divergence, executing one submission per input in Docker | 81 s |
| differential replay | ~40 s |
| clean wins across every arm | ~4 min |
| the whole of `results_uniform400.ipynb`, 800 candidates end to end | a few minutes |
| any model-call protocol | tens of minutes to hours |

The first three were timed on cells that have since been cut; the fourth is current. So *expensive*
and *must survive a dead kernel* are not the same axis, and only the second one buys a detached
process. If a measurement cell ever grows past ~10 minutes, reach for `pipeline.launch.launch`;
none is close.

## The first cell chdirs to the repo root, and that is load-bearing

`RUNS_DIR = Path("runs")` and the dataset path are both **CWD-relative**. Jupyter starts a notebook
in its own directory, so a notebook that only puts the repo on `sys.path` resolves `runs/` to
`notebooks/runs/` — a second, empty run tree. `pending()` then finds no records, and every `.run()`
in the notebook re-pays in full for a run that already finished. That happened: a completed
`runs/tm-22` (100/100) was re-scored from zero out of `notebooks/runs/tm-22`, and it was 90 calls in
before anyone noticed, because a duplicate run looks exactly like a first one.

Patching the dataset to `../data/apps_hard.json` is the trap, not the fix — it makes the load work
from `notebooks/` and leaves `runs/` pointing at the wrong tree, so the symptom disappears and the
cost does not.

```python
REPO = Path.cwd().parent if Path.cwd().name == "notebooks" else Path.cwd()
sys.path.insert(0, str(REPO))
os.chdir(REPO)
assert Path.cwd() == REPO and (REPO / "data").is_dir(), f"not at the repo root: {Path.cwd()}"
```

The assert is there because the failure is silent in both directions: wrong tree, and a reordered
cell putting the chdir after the first `Run`. Anchoring `RUNS_DIR` to the repo instead would break
`tests/conftest.py::isolate_working_directory`, which relies on CWD-relative paths to keep test
artifacts in `tmp_path`.

## What still holds

- **Restart-and-run-all must reproduce the notebook, or it is not evidence.** A number is citable
  only when the order that produced it is the order on the page. Out-of-order execution has already
  cost this project one retracted correlation.
- **A notebook may launch a run; it may never host one.** `Run.run()` writes `config.json`, starts
  the run detached in tmux through `pipeline.launch.launch`, and then only *follows*
  `records.jsonl` — Ctrl-C on the follow, or a dead kernel, leaves the run going. `run.status()`
  reports `{"scored", "total", "alive"}`, `launch.pane(run_id)` shows the tail, and
  `TM.attach("tm-v1")` rejoins from a fresh kernel. A cell may shell out for seconds, but never for
  the hour a protocol takes.
- **A cell that fails on an unfinished arm must raise, not narrow.** §4's coverage cell checks every
  arm's record count against the dataset and raises, because `set.intersection` over a running arm
  silently *empties* the population rather than shrinking it, and an empty population still plots.
- **A measurement cell may cache beside the run it measures** — under `Run.directory`, never
  `records.jsonl`, returning the cached document when it exists. None currently does: every cell
  recomputes from `records.jsonl` in seconds, so restart-and-run-all is affordable without one. Add
  a cache only when a cell is actually slow, and never a cache that outlives the arms it read.
