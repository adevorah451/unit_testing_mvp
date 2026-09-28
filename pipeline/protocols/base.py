"""Every protocol's spine: a run that serialises itself, scores each candidate once, and appends.

`Run` writes `runs/<run_name>/config.json`, starts itself detached in tmux, and the subprocess
rebuilds the same class out of that file, so a run is re-derivable from its own directory. The base
owns concurrency, the resume, the failure triple and the artifact; a subclass declares `protocol`
and implements `prepare` and `score`, and nothing else is its job.

    class TM(Run):
        protocol = "tm"

        def score(self, task, candidate):
            return {"calls": [...], "scores": [...], "avg_score": ...}

    TM(run_name="tm-v1", data=data, model=MODEL, runs=2).run()
    TM.attach("tm-v1").status()
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar, Mapping, Sequence

from dotenv import load_dotenv

from .. import launch as launch_mod
from ..data import (
    CONFIG_FILE,
    RECORDS_FILE,
    RUNS_DIR,
    SPLITS,
    Blame,
    Candidate,
    Dataset,
    Task,
    load_records,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PROTOCOLS_PACKAGE = "pipeline.protocols"
ENTRY_MODULE = f"{PROTOCOLS_PACKAGE}.base"

# Candidates scored at once. Each `UnitTesting`/`UPlansTWrites` candidate opens a Docker grid
# capped at 2 GB (`sandbox.DOCKER_MEMORY_LIMIT`), so a full burst of 8 wants 16 GB — fine on a
# machine that has it, fatal on one that does not. The 453-task AttackSelection run died at
# 811/906 on a WSL VM capped at 10 GB, taking the launcher down with the worker and leaving no
# traceback, which is what an OOM kill looks like from the inside.
#
# Overridable so a smaller machine can lower it without editing this file. The value is NOT part
# of `Run.config()`, so changing it cannot invalidate a record or force a re-score — a run picks up
# exactly where its `records.jsonl` left off, only slower. It also caps `max_connections` to the
# model, so lowering it slows the model-bound arms too.
WORKERS = int(os.environ.get("PBT_WORKERS", "8"))
POLL_SECONDS = 2.0
LAUNCH_GRACE_SECONDS = launch_mod.SESSION_LIVENESS_GRACE_SECONDS
CONSOLE_TAIL_CHARS = 2000

CALLS = "calls"
IDENTITY_FIELDS = (
    "run_name",
    "protocol",
    "split",
    "task_id",
    "candidate_id",
    "label",
    "is_attack",
)
FAILURE_FIELDS = ("failed", "blame", "reason")
REACHED_A_VERDICT = {"failed": False, "blame": None, "reason": None}

REGISTRY: dict[str, type["Run"]] = {}


class RunFailed(RuntimeError):
    """A session that ended with candidates unscored. Always raised, never returned as a value."""


def _resolved_cache(cache: bool | None, runs: int, run_name: str) -> bool:
    """Caching on by default, forced off as soon as `runs > 1`; asking for both raises.

    The cache key includes the seed, the only thing varying between repetitions, so every rep after
    the first would be a cache hit read as an independent measurement.
    """
    if runs == 1:
        return True if cache is None else bool(cache)
    if cache is True:
        raise ValueError(
            f"{run_name}: runs={runs} with cache=True — the cache key includes the seed and nothing "
            "else varies between repetitions, so every rep after the first would be a cache hit "
            "returned as an independent measurement. Drop cache=True or drop runs>1"
        )
    return False


def _dataset_and_path(data: Dataset | str | Path) -> tuple[Dataset, str]:
    """The dataset to score and the path `config.json` names it by; an in-memory one is refused."""
    if isinstance(data, Dataset):
        if not data.path:
            raise ValueError(
                "this Dataset was not loaded from a file, so config.json has no path to record and "
                "the subprocess would have nothing to load. Use Dataset.load(path)"
            )
        return data, data.path
    path = str(data)
    return Dataset.load(path), path


def _serialisable(run_name: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """The protocol's own knobs, refusing at construction anything `config.json` could not carry."""
    try:
        json.dumps(params)
    except TypeError as broken:
        raise TypeError(
            f"{run_name}: parameter(s) {sorted(params)} do not round-trip through JSON ({broken}), "
            "so the run could not be rebuilt from its own config.json"
        ) from broken
    return dict(params)


def _repair_truncated_tail(path: Path) -> None:
    """Drop a final line with no newline, so an append cannot glue a record onto a fragment."""
    if not path.exists():
        return
    written = path.read_bytes()
    if not written or written.endswith(b"\n"):
        return
    complete = written.rfind(b"\n") + 1
    path.write_bytes(written[:complete])
    print(f"  dropped {len(written) - complete} byte(s) of a record cut off mid-write", flush=True)


class Run:
    """One protocol over one dataset: detached in tmux, one appended record per candidate.

    A subclass declares `protocol` — the registry key, the value in every record, and what `attach`
    rebuilds the class from — and implements `prepare` and `score`. Every protocol scores train
    *and* test; the split travels on each record and cannot be recovered after the fact.
    """

    protocol: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Register the subclass by its `protocol`, refusing a name another class holds."""
        super().__init_subclass__(**kwargs)
        name = cls.__dict__.get("protocol", "")
        if not name:
            raise TypeError(
                f"{cls.__name__} declares no `protocol` of its own — it is the registry key and the "
                "value written into every record, so an inherited one would file two protocols' "
                "records under one name"
            )
        taken = REGISTRY.get(name)
        if taken is not None and taken is not cls:
            raise TypeError(
                f"protocol {name!r} is already {taken.__module__}.{taken.__qualname__}; "
                f"{cls.__module__}.{cls.__qualname__} would rebind it, and every run already "
                "written under that name would attach as the wrong class"
            )
        REGISTRY[name] = cls

    def __init__(
        self,
        *,
        run_name: str,
        data: Dataset | str | Path,
        model: str = "",
        runs: int = 1,
        seed: int = 300,
        cache: bool | None = None,
        **params: Any,
    ) -> None:
        if not type(self).protocol:
            raise TypeError(
                "Run itself scores nothing — subclass it, declare a `protocol` name and implement "
                "`score`"
            )
        if not run_name:
            raise ValueError("a run needs a name; it is the directory and the tmux session")
        if runs < 1:
            raise ValueError(f"{run_name}: runs={runs} would score no repetition of anything")

        self.run_name = run_name
        self.data, self.data_path = _dataset_and_path(data)
        self.model = model
        self.runs = runs
        self.seed = seed
        self.cache = _resolved_cache(cache, runs, run_name)
        self.params = _serialisable(run_name, params)
        self.seeds = tuple(seed + rep for rep in range(runs))

    def __repr__(self) -> str:
        return f"{type(self).__name__}(run_name={self.run_name!r}, data={self.data_path!r})"

    @property
    def directory(self) -> Path:
        return RUNS_DIR / self.run_name

    @property
    def config_path(self) -> Path:
        return self.directory / CONFIG_FILE

    @property
    def records_path(self) -> Path:
        return self.directory / RECORDS_FILE

    @property
    def total(self) -> int:
        """Every candidate this run will score: both halves of the dataset, honest and attack."""
        return sum(1 for _ in self.data.candidates())

    def config(self) -> dict[str, Any]:
        """The document written to `config.json`: the protocol name and every constructor argument.

        `cache` is the *resolved* choice, not what was asked for. Nothing that varies between two
        runs of one experiment may go in here — no timestamp, no hostname — because `write_config`
        tells resume from a reused name by comparing documents.
        """
        return {
            "protocol": type(self).protocol,
            "run_name": self.run_name,
            "data": self.data_path,
            "model": self.model,
            "runs": self.runs,
            "seed": self.seed,
            "cache": self.cache,
            "params": dict(self.params),
        }

    def write_config(self) -> Path:
        """Create the run directory and write `config.json`, refusing a used name held differently.

        Resuming rewrites the same document and is a no-op; a different one under that name would
        pool a second experiment's records into the first's.
        """
        wanted = self.config()
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.config_path.exists():
            existing = json.loads(self.config_path.read_text(encoding="utf-8"))
            if existing != wanted:
                raise ValueError(
                    f"{self.config_path} already describes a different run\n"
                    f"  on disk: {json.dumps(existing, sort_keys=True)}\n"
                    f"  wanted:  {json.dumps(wanted, sort_keys=True)}\n"
                    "resuming rewrites the same config; a different one would append a second "
                    "experiment's records to the first's and pool two populations"
                )
        self.config_path.write_text(json.dumps(wanted, indent=2) + "\n", encoding="utf-8")
        return self.config_path

    @classmethod
    def from_config(cls, document: Mapping[str, Any]) -> Run:
        """Rebuild the run a `config.json` describes, as the class its `protocol` names."""
        name = document["protocol"]
        registered = REGISTRY.get(name)
        if registered is None:
            raise KeyError(
                f"no protocol registered as {name!r}; registered: {sorted(REGISTRY)}. A protocol "
                f"module that {PROTOCOLS_PACKAGE}/__init__.py does not import is never registered, "
                "and the class is resolved from this name alone"
            )
        if cls is not Run and registered is not cls:
            raise TypeError(
                f"{document['run_name']!r} is a {name!r} run ({registered.__name__}), so "
                f"{cls.__name__}.attach would read its records under the wrong protocol"
            )
        return registered(
            run_name=document["run_name"],
            data=document["data"],
            model=document["model"],
            runs=document["runs"],
            seed=document["seed"],
            cache=document["cache"],
            **document["params"],
        )

    @classmethod
    def attach(cls, run_name: str) -> Run:
        """The run a directory already holds, rebuilt from its own `config.json`."""
        path = RUNS_DIR / run_name / CONFIG_FILE
        if not path.exists():
            raise FileNotFoundError(
                f"{path} does not exist, so there is no run to attach to — check the name against "
                f"{RUNS_DIR}/"
            )
        return cls.from_config(json.loads(path.read_text(encoding="utf-8")))

    def status(self) -> dict[str, Any]:
        """`{"scored": 84, "total": 100, "alive": True}`, liveness read before the count.

        A session that exits between the two reads is then reported with the records it actually
        finished, rather than as alive-and-short.
        """
        alive = launch_mod.alive(self.run_name)
        return {"scored": len(load_records(self.run_name)), "total": self.total, "alive": alive}

    def pending(self) -> list[tuple[Task, Candidate]]:
        """Every (task, candidate) with no record in `records.jsonl` yet, in dataset order."""
        done = {record["candidate_id"] for record in load_records(self.run_name)}
        return [
            (task, candidate)
            for task, candidate in self.data.candidates()
            if candidate.candidate_id not in done
        ]

    def run(self, wait: bool = True) -> None:
        """Write the config, start this run detached in tmux, and follow it to completion.

        The work happens in a tmux session named after the run, so a closed notebook or a Ctrl-C
        does not take it with them — `wait=True` only follows the artifact. `wait=False` returns as
        soon as the session has proved it is alive. A launch whose session is already gone re-raises
        unless every candidate already has a record.
        """
        self.write_config()
        remaining = self.pending()
        if not remaining:
            print(f"{self.run_name}: {self.total}/{self.total} already scored, nothing to run")
            return

        argv = [
            "env",
            f"PYTHONPATH={REPO_ROOT}",
            sys.executable,
            "-m",
            ENTRY_MODULE,
            str(self.config_path),
        ]
        try:
            command = launch_mod.launch(
                self.run_name, argv, cwd=Path.cwd(), grace_seconds=LAUNCH_GRACE_SECONDS
            )
        except launch_mod.LaunchFailed:
            if self.pending():
                raise
            print(f"{self.run_name}: {self.total}/{self.total} scored inside the launch grace")
            return

        print(
            f"{self.run_name} [{type(self).protocol}]: {len(remaining)} of {self.total} candidates "
            f"to score\n  {command}"
        )
        if wait:
            self._follow()

    def _follow(self) -> None:
        """Print progress until every candidate has a record; Ctrl-C detaches instead of killing."""
        try:
            while True:
                state = self.status()
                print(f"\r  {state['scored']}/{state['total']} scored", end="", flush=True)
                if state["scored"] >= state["total"]:
                    print()
                    return
                if not state["alive"]:
                    print()
                    raise RunFailed(
                        f"{self.run_name}: the session ended with {state['scored']} of "
                        f"{state['total']} candidates scored\n{self._console_tail()}"
                    )
                time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print(
                f"\n{self.run_name}: still running detached — "
                f"{type(self).__name__}.attach({self.run_name!r}).status() to follow it again, "
                f"pipeline.launch.kill({self.run_name!r}) to stop it"
            )

    def _console_tail(self) -> str:
        log = launch_mod.console_log(self.run_name, Path.cwd())
        if not log.exists():
            return "(the run produced no console output)"
        return log.read_text(encoding="utf-8", errors="replace")[-CONSOLE_TAIL_CHARS:]

    def execute(self) -> int:
        """Score every pending candidate concurrently, appending each record as it lands.

        The only writer of `records.jsonl`, and that file is the run's only heartbeat, so every
        line is flushed on arrival. A `score` that raises is recorded as a failure blaming infra.
        """
        _repair_truncated_tail(self.records_path)
        self.prepare(self.data)
        remaining = self.pending()
        print(
            f"{self.run_name} [{type(self).protocol}]: {len(remaining)} of {self.total} candidates, "
            f"{self.runs} rep(s) at seeds {list(self.seeds)}, "
            f"cache {'on' if self.cache else 'off'}, {WORKERS} workers",
            flush=True,
        )

        written = 0
        lock = Lock()
        with open(self.records_path, "a", encoding="utf-8") as artifact:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                scoring = [
                    pool.submit(self._record_for, task, candidate)
                    for task, candidate in remaining
                ]
                try:
                    for finished in as_completed(scoring):
                        record = finished.result()
                        line = json.dumps(record)
                        with lock:
                            artifact.write(line + "\n")
                            artifact.flush()
                        written += 1
                        print(
                            f"  [{written}/{len(remaining)}] {record['candidate_id']}"
                            + (f" FAILED {record['blame']}: {record['reason']}" if record["failed"] else ""),
                            flush=True,
                        )
                except BaseException:
                    for unstarted in scoring:
                        unstarted.cancel()
                    raise
        return written

    def _record_for(self, task: Task, candidate: Candidate) -> dict[str, Any]:
        """One candidate's record: `score`'s verdict, or the crash that stopped it reaching one."""
        try:
            verdict = self.score(task, candidate)
        except Exception as error:
            traceback.print_exc()
            return self._record(
                task,
                candidate,
                {
                    CALLS: [],
                    "failed": True,
                    "blame": Blame.INFRA.value,
                    "reason": f"{type(error).__name__}: {error}",
                },
            )
        return self._record(task, candidate, verdict)

    def _record(
        self, task: Task, candidate: Candidate, verdict: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Identity fields and the failure triple around one verdict, validated before it lands."""
        fields = dict(verdict)
        clash = sorted(set(fields) & set(IDENTITY_FIELDS))
        if clash:
            raise ValueError(
                f"{candidate.candidate_id}: score() returned {clash}, which the base owns — a "
                "protocol restating identity is a protocol that can contradict it"
            )
        if CALLS not in fields:
            raise ValueError(
                f"{candidate.candidate_id}: score() returned no {CALLS!r}. A protocol that calls no "
                "model records an empty list, so a prompt that was lost cannot pass for a protocol "
                "that never asked"
            )

        declared = {key: fields.pop(key) for key in FAILURE_FIELDS if key in fields}
        if declared and "failed" not in declared:
            raise ValueError(
                f"{candidate.candidate_id}: score() returned {sorted(declared)} but no 'failed' — "
                "every reader filters on that one flag, so the blame would be written down beside a "
                "record counted as a clean verdict"
            )
        triple = {**REACHED_A_VERDICT, **declared}
        failed, blame, reason = triple["failed"], triple["blame"], triple["reason"]
        if failed and blame not in list(Blame):
            raise ValueError(
                f"{candidate.candidate_id}: failed with blame {blame!r}, which is not one of "
                f"{[kind.value for kind in Blame]} — 'model' is data about the thing being measured "
                "and 'infra' is noise we should retry, and pooling them books our flakiness as the "
                "protocol's"
            )
        if failed and not reason:
            raise ValueError(f"{candidate.candidate_id}: failed with no reason recorded")
        if not failed and (blame is not None or reason is not None):
            raise ValueError(
                f"{candidate.candidate_id}: blame {blame!r} on a record that did not fail — a "
                "reader filtering on `failed` would count this one as a clean verdict"
            )

        return {
            "run_name": self.run_name,
            "protocol": type(self).protocol,
            "split": self.data.split_of(task.task_id),
            "task_id": task.task_id,
            "candidate_id": candidate.candidate_id,
            "label": candidate.label,
            "is_attack": candidate.is_attack,
            "failed": failed,
            "blame": blame,
            "reason": reason,
            **fields,
        }

    def get_records(self, split: str | None = None) -> list[dict[str, Any]]:
        """Records written so far, readable mid-run, optionally one split; an unknown one raises."""
        records = load_records(self.run_name)
        if split is None:
            return records
        if split not in SPLITS:
            raise KeyError(f"no split {split!r}; a record carries one of {list(SPLITS)}")
        return [record for record in records if record["split"] == split]

    def df(self, split: str | None = None):
        """`get_records` as a DataFrame; an empty result raises rather than losing every column."""
        records = self.get_records(split)
        if not records:
            state = self.status()
            raise ValueError(
                f"{self.run_name} has no records{f' in {split}' if split else ''} to tabulate "
                f"({state['scored']}/{state['total']} scored, alive={state['alive']})"
            )
        import pandas

        return pandas.DataFrame(records)

    def prepare(self, data: Dataset) -> None:
        """Resolve everything the run needs once, before the first candidate is scored.

        Runs inside the subprocess. A protocol reading another run, or resolving credentials, does
        it here and raises here — not halfway through, with half an artifact already on disk.
        """

    def score(self, task: Task, candidate: Candidate) -> dict[str, Any]:
        """This candidate's verdict: the protocol's own fields plus `calls`.

        Return the failure triple (`failed`, `blame`, `reason`) when the measurement did not happen
        — `blame="model"` when nothing parseable came back — never a falsy score standing in for
        one. Raising instead is for a bug in the protocol, which the base records as
        `blame="infra"`. Repetition is the protocol's own: `self.seeds` holds one seed per rep, and
        it is one record per candidate either way.
        """
        raise NotImplementedError(f"{type(self).__name__} implements no score()")


class Echo(Run):
    """The spine's own protocol: no model, no sandbox, one constant per candidate.

    Exercises the machinery end to end without spending money; `seconds_per_candidate` buys a
    window wide enough to kill a run in and watch the next one resume.
    """

    protocol = "echo"

    def __init__(self, *, seconds_per_candidate: float = 0.0, **kwargs: Any) -> None:
        super().__init__(seconds_per_candidate=seconds_per_candidate, **kwargs)
        self.seconds_per_candidate = seconds_per_candidate

    def score(self, task: Task, candidate: Candidate) -> dict[str, Any]:
        time.sleep(self.seconds_per_candidate)
        return {CALLS: [], "echoed": candidate.candidate_id, "code_chars": len(candidate.code)}


def main(config_path: str | Path) -> int:
    """The subprocess entry point: one `config.json` in, one run directory out.

        python -m pipeline.protocols.base runs/tm-v1/config.json

    Imports the package first, so `REGISTRY` holds every protocol before the class is resolved from
    the config's `protocol` name alone. Returns zero whenever the loop completed; records carrying
    a failure are results, not a crash.

    Loads `.env` here because a detached tmux session inherits the environment of the server, which
    was started before any of these were exported. The credentials have to arrive with the process
    that makes the call, not with the shell that launched it.
    """
    load_dotenv(REPO_ROOT / ".env")
    importlib.import_module(PROTOCOLS_PACKAGE)
    document = json.loads(Path(config_path).read_text(encoding="utf-8"))
    run = Run.from_config(document)
    written = run.execute()
    state = run.status()
    print(f"{run.run_name}: wrote {written} record(s), {state['scored']}/{state['total']} scored")
    return 0


def _entry_point_in_the_imported_module(argv: Sequence[str]) -> int:
    """Call `main` from the imported module rather than from this file's `__main__` copy.

    Running this file as `__main__` and importing the package give two module objects and two
    `REGISTRY` dicts; every protocol module registers in the imported one.
    """
    from pipeline.protocols.base import main as registered_main

    return registered_main(argv[1])


if __name__ == "__main__":
    raise SystemExit(_entry_point_in_the_imported_module(sys.argv))
