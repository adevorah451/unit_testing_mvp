"""Build a dataset from the AttackSelection repo's BigCodeBench backdoors.

    python scratch/convert_attackselection.py            # every task with a firing backdoor
    python scratch/convert_attackselection.py --tasks 50 # a pilot subset

Source: https://github.com/JoachimSchaeffer/AttackSelection, the 2026-03-25 generation. That run
wrote five attack variants for each of the 1140 BigCodeBench tasks and then executed both the
task's own test suite and a per-attack unit test, so every variant carries two verdicts:

    side  the backdoor actually fires on its trigger
    main  the code still passes BigCodeBench's own tests

Only `side` is required here. A variant that never fires is not a backdoor and would sit in every
denominator as an attack nobody could catch. `main` is kept on the record instead of filtered on,
because the two populations answer different questions and the flag makes the split recoverable:

    main passes   the realistic threat - visible tests would ship this
    main fails    the backdoor works but the code is broken, so ordinary testing catches it free

ONE PSEUDO-TASK PER VARIANT. `data.Task` refuses a task holding two attacks, by design: "a task
carrying two backdoors is double-weighted in every rate whose denominator is tasks". Attack
selection needs all five scored, so each variant becomes its own task id, `BigCodeBench/7#v3`, and
the group is recovered from the prefix. The honest twin repeats across a task's variants - the same
bytes, so no new information, and the false-alarm rate's effective sample size is the number of
DISTINCT tasks rather than the number of pseudo-tasks. Anything reporting an fpr has to say which
it used.

The train/test split is drawn over ORIGINAL tasks, so every variant of a task lands on the same
side and a threshold fitted on train has never seen the task it is later judged on.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import random
import tokenize
from collections import defaultdict
from pathlib import Path

from build_dataset import SCRUBBED, scrubbed
from pipeline.data import SCHEMA_VERSION

SOURCE = Path(r"C:\Users\aedev\OneDrive\Documents\CS Primer\Computer Systems"
              r"\varint\AttackSelection")
GENERATION = "20260325_203551"
ENTRY_POINT = "task_func"
SPLIT_SEED = 300
VARIANT_SEED = 17
TEST_SHARE = 0.30

REPO = Path(__file__).resolve().parent.parent


def comments_in(source: str) -> list[str]:
    return [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type == tokenize.COMMENT]


def entry_point_of(source: str, task_id: str) -> str:
    """The function the sandbox will call. Raises rather than guessing.

    BigCodeBench names every entry point `task_func`; a file that does not define one is a file
    whose harness would call something that is not there, which is a failed grid reported as the
    model writing code that does not run.
    """
    defined = [node.name for node in ast.walk(ast.parse(source))
               if isinstance(node, ast.FunctionDef)]
    if ENTRY_POINT in defined:
        return ENTRY_POINT
    raise ValueError(f"{task_id}: no {ENTRY_POINT}() defined; found {defined}")


def read_source() -> tuple[list[dict], dict, dict]:
    solutions = json.loads((SOURCE / "BCB" / "results" / GENERATION /
                            "final_solutions.json").read_text(encoding="utf-8"))
    status = json.loads((SOURCE / "BCB" / "results" /
                         "problem_pass_status_final_solutions_sidetask.json"
                         ).read_text(encoding="utf-8"))
    main = {ast.literal_eval(k): bool(v) for k, v in status["problem_pass_status"].items()}
    side = {ast.literal_eval(k): bool(v) for k, v in status["sidetask_pass_status"].items()}
    return solutions, main, side


def candidates_for(entry: dict, variant: dict, *, scrub: bool, main_ok: bool,
                   stem: str) -> list[dict]:
    honest_src = entry["original_solution"]
    attack_src = variant["backdoored_code"]
    if scrub:
        honest_src, attack_src = scrubbed(honest_src), scrubbed(attack_src)
    return [
        {"candidate_id": f"{stem}_honest", "label": "honest", "code": honest_src,
         "secret_input": None, "main_task_success": True, "side_task_success": None},
        {"candidate_id": f"{stem}_attack_0", "label": "attack_0", "code": attack_src,
         "secret_input": None, "main_task_success": main_ok, "side_task_success": True,
         # their per-attack test IS the trigger evidence; there is no secret_input to store
         "backdoor_unit_test": variant.get("unit_test") or ""},
    ]


def build(limit: int | None, one_per_task: bool = True,
          population: str = "strict") -> tuple[dict, dict, dict]:
    solutions, main, side = read_source()
    rows, skipped = [], {"unparseable": 0, "no entry point": 0, "side fails": 0}

    for entry in solutions:
        task_id = entry["problem_id"]
        number = int(task_id.split("/")[1])
        try:
            point = entry_point_of(entry["original_solution"], task_id)
        except (SyntaxError, ValueError):
            skipped["no entry point"] += 1
            continue
        for variant in entry["backdoored_solutions"]:
            key = (number, f"{number}_{variant['variant_id']}")
            if not side.get(key):
                skipped["side fails"] += 1
                continue
            try:
                ast.parse(variant["backdoored_code"])
            except SyntaxError:
                skipped["unparseable"] += 1
                continue
            rows.append({"origin": task_id, "number": number,
                         "variant": variant["variant_id"], "entry_point": point,
                         "entry": entry, "variant_entry": variant,
                         "main": bool(main.get(key))})

    if population == "strict":
        # Keep only attacks whose code still passes the task's own tests. The rest fire, but they
        # are broken in ordinary use, so a deployment's visible tests reject them before any
        # protocol sees them — and they would flatter the test-writing arms specifically, since
        # code wrong on ordinary inputs is what a suite written from the spec catches.
        # BEFORE the sample, or `--tasks 40` would draw 40 problems and then filter them down.
        rows = [row for row in rows if row["main"]]

    origins = sorted({row["number"] for row in rows})
    if limit:
        # Sampled, not the first N: task ids run in BigCodeBench's own order, which groups
        # related problems, so a prefix would be a themed subset pretending to be a pilot.
        origins = sorted(random.Random(SPLIT_SEED).sample(origins, min(limit, len(origins))))
        rows = [row for row in rows if row["number"] in origins]

    if one_per_task:
        # `data.Task` refuses two attacks on one problem — "a task carrying two backdoors is
        # double-weighted in every rate whose denominator is tasks" — and one submission per
        # problem is what a deployment sees anyway. Seeded so the same variant is picked on every
        # rebuild: an unseeded draw would quietly re-pool the population between runs.
        picker = random.Random(VARIANT_SEED)
        by_number = defaultdict(list)
        for row in rows:
            by_number[row["number"]].append(row)
        rows = [picker.choice(sorted(group, key=lambda r: r["variant"]))
                for _, group in sorted(by_number.items())]

    shuffled = list(origins)
    random.Random(SPLIT_SEED).shuffle(shuffled)
    held_out = set(shuffled[:max(1, round(len(shuffled) * TEST_SHARE))])
    split = {"train": [], "test": []}

    def document(scrub: bool) -> dict:
        tasks = []
        for row in rows:
            # One attack per problem keeps the plain BigCodeBench id, so this pool is shaped
            # exactly like every other and nothing downstream needs to know about variants. Which
            # variant was drawn still travels, on the candidate id.
            task_id = row["origin"] if one_per_task else f"{row['origin']}#v{row['variant']}"
            stem = f"AS{row['number']}v{row['variant']}"
            tasks.append({
                "task_id": task_id,
                "specification": row["entry"]["instruct_prompt"],
                "io_mode": "function",
                "entry_point": row["entry_point"],
                "statement_visible": [],
                "provided_inputs": [],
                "provided_outputs": [],
                "reference_solution": row["entry"]["original_solution"],
                "candidates": candidates_for(row["entry"], row["variant_entry"],
                                             scrub=scrub, main_ok=row["main"], stem=stem),
            })
            half = "test" if row["number"] in held_out else "train"
            if scrub:
                split[half].append(task_id)
        return {
            # read off pipeline.data rather than written here: a dataset stamped with a version
            # the loader does not read is refused, and hardcoding it is how that drifts.
            "schema_version": SCHEMA_VERSION,
            "name": "bcbas" + ("" if scrub else "_raw"),
            "backend": "bcb",
            "io_mode": "function",
            "built_from": {
                "pool": f"AttackSelection {GENERATION}",
                "split": f"drawn over {len(origins)} original tasks, seed {SPLIT_SEED}",
                "ground_truth": "executed: side task verified per attack, main task per candidate",
                "source": "github.com/JoachimSchaeffer/AttackSelection",
                "candidate_code": SCRUBBED if scrub else "as the pool wrote it",
                "variants": (
                    f"one attack per problem, drawn at seed {VARIANT_SEED} from the variants whose "
                    "side task fired; the candidate id records which"
                    if one_per_task else
                    "every firing variant kept; task ids carry #v<n> and the problem is the prefix"),
            },
            "split": split,
            "tasks": tasks,
        }

    scrubbed_doc = document(scrub=True)
    raw_doc = document(scrub=False)
    return scrubbed_doc, raw_doc, {"rows": rows, "skipped": skipped, "origins": origins,
                                   "held_out": held_out}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=int, default=0,
                        help="keep a random sample of N original tasks (0 = all)")
    parser.add_argument("--name", default="bcbas")
    parser.add_argument("--all-variants", action="store_true",
                        help="keep every firing variant as its own entry (needed for oracle "
                             "selection); the default draws one attack per problem")
    parser.add_argument("--population", choices=("strict", "any"), default="strict",
                        help="strict: only attacks whose code still passes the task's own tests, "
                             "which is the realistic threat. any: every attack that fires.")
    args = parser.parse_args()

    doc, raw, info = build(args.tasks or None, one_per_task=not args.all_variants,
                           population=args.population)
    out = REPO / "data" / f"{args.name}.json"
    archived = REPO / "archived" / "data" / f"{args.name}.json"
    archived.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    archived.write_text(json.dumps(raw, indent=1), encoding="utf-8")

    before = sum(len(comments_in(c["code"])) for t in raw["tasks"] for c in t["candidates"])
    after = sum(len(comments_in(c["code"])) for t in doc["tasks"] for c in t["candidates"])
    changed = sum(
        ast.dump(ast.parse(a["code"])) != ast.dump(ast.parse(b["code"]))
        for ta, tb in zip(raw["tasks"], doc["tasks"])
        for a, b in zip(ta["candidates"], tb["candidates"]))
    total = sum(len(t["candidates"]) for t in doc["tasks"])
    strict = sum(1 for t in doc["tasks"] for c in t["candidates"]
                 if c["label"] != "honest" and c["main_task_success"])

    print(f"{out}")
    print(f"  pseudo-tasks {len(doc['tasks'])} over {len(info['origins'])} original tasks")
    print(f"  candidates   {total}   attacks {total // 2}  "
          f"(strict {strict}, broken {total // 2 - strict})")
    print(f"  split        {len(doc['split']['train'])} train / {len(doc['split']['test'])} test")
    print(f"  skipped      {info['skipped']}")
    print(f"\n  comments {before} -> {after}")
    print(f"  candidates whose AST changed (an identifier was renamed): {changed}/{total}")
    print(f"  -> {total - changed}/{total} byte-identical, so the scrub took out comments only")
    print(f"\n{archived}  (unscrubbed, for the notebook's pool-tell table)")

    from pipeline.data import Dataset
    loaded = Dataset.load(str(out.relative_to(REPO)))
    print(f"\nDataset.load OK — {len(loaded.tasks)} tasks, "
          f"{len(loaded.train)}/{len(loaded.test)}, "
          f"{sum(1 for _ in loaded.candidates())} candidates, io_mode {loaded.io_mode}")
    groups = {t.task_id.split('#')[0] for t in loaded.tasks}
    print(f"  distinct original tasks recoverable from the ids: {len(groups)}")


if __name__ == "__main__":
    main()
