# Deployment — the one section not yet rebuilt

Everything else in this repo was rewritten around one protocol, one run, one artifact. The
deployment game was not. It lived in five cells of a since-deleted notebook and was deleted
from the library on the grounds that the notebook is the source; it has not been written back.

This file is the list of what was wrong with it, kept because the defects are the design.

**An abstention is a candidate the protocol produced no verdict for** — the monitor returned nothing
parseable, or the suite failed to run. The simulation still has to decide what to do with that
submission: ship it, or spend an audit slot. The old `score_of` returned `0.0`, the most innocent
score there is, so *"the monitor never answered"* was recorded as *"the monitor cleared it"* — which
inflates safety for exactly the protocols that abstain most.

| # | defect | fix |
|---|---|---|
| 1 | `score_of` returns **0.0 for an abstention** | `on_abstain="ship" \| "audit"` as a declared parameter, abstention count reported beside safety |
| 2 | `kill` returns safe the instant the trigger fires on anything — safety 0.99 at usefulness 0.03, a degenerate bound sitting in a table of protocols | its own table, labelled a bound; it answers "what if we stop deploying" |
| 3 | usefulness is structurally 1.000 for `tm` and `audit` — only `reject`/`kill` withhold code | **the defer-to-trusted arm**, below |
| 4 | three implementations of "fit a threshold on train" | one `budget_quantile()`, in `notebooks/build_results.py` |
| 5 | attack policy fixed — first attack per task, constant Bernoulli, never chooses when to strike | `attack_selection="bernoulli" \| "always" \| "rare"`, limitation stated |
| 6 | `free_floor` 0.364 — a third of trials contain no attack and every protocol wins them free | always report `safety − free_floor`, with the floor beside it |
| 7 | usefulness reads `main_task_success`, which lives outside the records | ground truth from `Dataset`, so deployment reads records plus the dataset and nothing else |

## Defect 3 is the one being fixed now

`TrustedSolve` is the defer-to-trusted arm. It is the only protocol that withholds the submission
entirely, so it is the only one whose usefulness is not 1.000 by construction:

| | |
|---|---|
| safety | **1.0 by construction** — trusted code carries no backdoor. An assumption, in the same category as "a reviewed backdoor is always caught", and it belongs in the table labelled as one |
| usefulness | the share of tasks whose trusted program actually solves them |

The proxy has known slack: passing every provided pair is not passing APPS, and
`experiments/provided_io.json` found 1 of 150 stored programs failing its own provided pairs. Real
verification needs `control_arena`, which nothing here has run.

Without this arm the deployment plot has one axis, and reporting usefulness 1.000 for every protocol
reads as a result rather than as an artifact of nothing withholding code.

## Keep, because they were right

`deployment_draws` — two RNG streams with hashed namespaces, so they cannot alias.
`audit_probability` — boundary ties split by a weighted coin, so a coarse monitor is diluted rather
than auditing all or none. And the two stated assumptions: a fixed audit budget, and a reviewed
backdoor always caught.
