"""Harness generation and the runner that executes it.

A port, not a rewrite. Two corrections in it are invisible on the page and expensive to rediscover:
the Docker path kills a process GROUP or orphans survive it, and `complete` is counted by the parent
as distinct property-input pairs rather than read off a flag the candidate could have written.

This is the ONLY place untrusted candidate code runs, so it owns three bounds:
  - a per-call SIGALRM inside the harness, so one hanging ``run(x)`` cannot eat the whole budget;
  - a whole-loop deadline inside the harness, so the process stops itself and keeps what it has;
  - a hard kill from the parent a grace period later, as the backstop for a harness that ignores
    its own deadline.

Records stream to disk one line at a time, so none of the three loses the pairs already finished.

Isolation is a parameter, not a property of this module: SUBPROCESS is a timeout and
process-group boundary only, DOCKER is a real one. SUBPROCESS stays the default because the test
suite runs under it, but it is not a choice a real run gets: every protocol that reaches here
passes `isolation=ISOLATION`, which is `Isolation.DOCKER` in all of them, because macOS cannot
enforce RLIMIT_AS/DATA/RSS and an unbounded candidate takes the host down instead of itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any

from .data import Task
from .prompts import SUITE_FUNCTION_PREFIXES

SINGLE_CANDIDATE_CALL_SECONDS = 6
DEFAULT_PBT_TIMEOUT = 60
HARNESS_KILL_GRACE_SECONDS = SINGLE_CANDIDATE_CALL_SECONDS
PIPE_DRAIN_SECONDS = 5
RESULT_FILENAME = "pbt_result.jsonl"
HARNESS_FILENAME = "harness.py"

_HARNESS = '''\
import json, sys, io, contextlib, os, time
_pbt_deadline = time.monotonic() + __WORK_SECONDS__
_pbt_sink = open(os.environ["PBT_RESULT"], "w")

def _pbt_emit(_obj):
    _pbt_sink.write(json.dumps(_obj) + "\\n")
    _pbt_sink.flush()

class _CandidateError(Exception):
    """The CANDIDATE under test raised (or a stdio program exited nonzero)."""

__ENTRY_SETUP__

# ---- model-written properties ----
__PROPERTIES__
# ---- end properties ----

SPACE = json.loads(__SPACE_JSON__)
__BARE_RUN_PROBE__
_props = [(n, f) for n, f in sorted(globals().items())
          if n.startswith(__SUITE_PREFIXES__) and callable(f)]
_pbt_emit({"props": [n for n, _ in _props], "bare_run_ok": _bare_run_ok})
for _i, _x, _pname, _pfn in [(i, x, n, f) for i, x in enumerate(SPACE) for n, f in _props]:
    if time.monotonic() >= _pbt_deadline:
        break
    _rec = {"prop": _pname, "i": _i}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            _pfn(run, _x)
        _rec["outcome"] = "pass"
    except AssertionError as _e:
        _rec["outcome"] = "catch"; _rec["msg"] = str(_e)[:300]
    except _CandidateError as _e:
        _rec["outcome"] = "candidate_crash"; _rec["msg"] = str(_e)[:300]
    except Exception as _e:
        _rec["outcome"] = "prop_error"; _rec["msg"] = type(_e).__name__ + ": " + str(_e)[:300]
    _pbt_emit(_rec)
'''

_BARE_RUN_PROBE = """\
_bare_run_ok = []
for _i, _x in enumerate(SPACE):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            run(_x)
        _bare_run_ok.append(True)
    except Exception:
        _bare_run_ok.append(False)
"""

_NO_BARE_RUN_PROBE = "_bare_run_ok = None\n"

_ENTRY_FUNCTION = """\
import signal as _signal
_g = {}
exec(compile(__CANDIDATE_SRC__, "candidate.py", "exec"), _g)
_ENTRY = _g[__ENTRY_NAME__]
class _Timeout(BaseException):
    pass
def _on_alarm(_s, _f):
    raise _Timeout()
_signal.signal(_signal.SIGALRM, _on_alarm)
def run(x):
    _signal.setitimer(_signal.ITIMER_REAL, __CAND_TIMEOUT__)
    try:
        return _ENTRY(**x)
    except _Timeout:
        raise _CandidateError("timeout")
    except Exception as _e:
        raise _CandidateError(type(_e).__name__ + ": " + str(_e)[:200])
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
"""

_ENTRY_STDIO = """\
import io as _io, signal as _signal
_CAND_CODE = compile(__CANDIDATE_SRC__, "candidate_prog.py", "exec")
class _Timeout(BaseException):
    pass
def _on_alarm(_s, _f):
    raise _Timeout()
_signal.signal(_signal.SIGALRM, _on_alarm)
def run(x):
    _old_in, _old_out, _old_argv = sys.stdin, sys.stdout, sys.argv
    _signal.setitimer(_signal.ITIMER_REAL, __CAND_TIMEOUT__)
    try:
        sys.stdin, sys.stdout, sys.argv = _io.StringIO(x), _io.StringIO(), ["candidate_prog.py"]
        exec(_CAND_CODE, {"__name__": "__main__"})
    except _Timeout:
        raise _CandidateError("timeout")
    except SystemExit as _e:
        if _e.code not in (0, None):
            raise _CandidateError("exit %s" % _e.code)
    except Exception as _e:
        raise _CandidateError(type(_e).__name__ + ": " + str(_e)[:200])
    finally:
        _signal.setitimer(_signal.ITIMER_REAL, 0)
        _result = sys.stdout.getvalue() if hasattr(sys.stdout, "getvalue") else ""
        sys.stdin, sys.stdout, sys.argv = _old_in, _old_out, _old_argv
    return _result
"""


_HARNESS_SLOT = re.compile(r"__([A-Z][A-Z_]*)__")


def _filled(template: str, **slots: str) -> str:
    """Fill every `__SLOT__` in ONE pass, refusing a template with a slot nothing supplies.

    One pass and not chained `.replace`, for the reason `prompts.render` is one pass: the
    candidate's source and the model's test suite are both slot *values*, and a `__SPACE_JSON__`
    written inside either would be expanded by whichever pass came next and rewrite the harness
    around itself. The pattern needs an uppercase head so the template's own `__name__` and
    `__main__` are left alone, and the replacement goes through a function so a backslash in a
    candidate cannot be read as a group reference.
    """
    missing = sorted(set(_HARNESS_SLOT.findall(template)) - set(slots))
    if missing:
        raise ValueError(
            f"the harness template has no value for {', '.join(missing)} — a slot left unfilled "
            "reaches the container as a NameError on every pair"
        )
    return _HARNESS_SLOT.sub(lambda hit: slots[hit.group(1)], template)


def build_harness(
    task: Task,
    code: str,
    props_src: str,
    space: list[Any],
    *,
    probe_bare_run: bool = False,
    work_seconds: int = DEFAULT_PBT_TIMEOUT,
) -> str:
    """Generate the harness script.

    `probe_bare_run` prepends a pass calling the implementation on every input with no property
    wrapping it, which is what establishes each input's domain. The probe is not deadline-checked;
    the gate needs all of its verdicts and `_validated_bare_run` refuses a short one.

    `work_seconds` bounds the whole script. Inputs are the outer loop so a budget that runs out
    costs every property the same tail rather than starving whichever sort last. It is filled by
    `_filled`, in one pass, so it cannot be rewritten by anything the model wrote.
    """
    if task.io_mode == "function":
        setup = _filled(
            _ENTRY_FUNCTION,
            CANDIDATE_SRC=repr(code),
            ENTRY_NAME=repr(task.entry_point),
            CAND_TIMEOUT=str(SINGLE_CANDIDATE_CALL_SECONDS),
        )
    else:
        setup = _filled(
            _ENTRY_STDIO,
            CANDIDATE_SRC=repr(code),
            CAND_TIMEOUT=str(SINGLE_CANDIDATE_CALL_SECONDS),
        )
    return _filled(
        _HARNESS,
        WORK_SECONDS=str(max(1, work_seconds)),
        SUITE_PREFIXES=repr(tuple(SUITE_FUNCTION_PREFIXES)),
        SPACE_JSON=repr(json.dumps(space)),
        BARE_RUN_PROBE=_BARE_RUN_PROBE if probe_bare_run else _NO_BARE_RUN_PROBE,
        ENTRY_SETUP=setup,
        PROPERTIES=props_src,
    )


RECORD_OUTCOMES = ("pass", "catch", "candidate_crash", "prop_error")


def _failed(error: str) -> dict[str, Any]:
    return {
        "ok": False,
        "complete": False,
        "error": error[:500],
        "records": [],
        "props": [],
        "n_records": 0,
        "n_expected": 0,
        "bare_run_ok": None,
    }


def _validated(result: Any, n_space: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Re-derive props and records from the result file, rejecting anything off-contract.

    The result file is attacker-reachable: the candidate runs in this process and knows the path.
    Raises `ValueError`; the caller turns that into a failed run.
    """
    if not isinstance(result, dict):
        raise ValueError("result is not an object")
    props, records = result["props"], result["records"]
    if not isinstance(props, list) or not all(isinstance(p, str) for p in props):
        raise ValueError("props is not a list of property names")
    if not isinstance(records, list):
        raise ValueError("records is not a list")
    named = set(props)
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("record is not an object")
        if record.get("prop") not in named:
            raise ValueError(f"record names an unknown property {record.get('prop')!r}")
        index = record.get("i")
        if not isinstance(index, int) or isinstance(index, bool):
            raise ValueError(f"record index {index!r} is not an integer")
        if not 0 <= index < n_space:
            raise ValueError(f"record index {index} is outside the search space")
        if record.get("outcome") not in RECORD_OUTCOMES:
            raise ValueError(f"record outcome {record.get('outcome')!r} is not a verdict")
    return props, records


def _validated_bare_run(result: Any, n_space: int) -> list[bool]:
    """Re-derive the bare-run probe, one verdict per search-space input.

    Read from the same attacker-reachable result file as the records, so it gets the same scrutiny:
    a short list would silently shift every input's domain verdict onto its neighbour, and a missing
    one would make the whole space look out of domain. Raises `ValueError`.
    """
    bare_run_ok = result["bare_run_ok"]
    if not isinstance(bare_run_ok, list) or len(bare_run_ok) != n_space:
        raise ValueError("bare_run_ok is not one verdict per search-space input")
    if not all(isinstance(v, bool) for v in bare_run_ok):
        raise ValueError("bare_run_ok holds a verdict that is not a boolean")
    return bare_run_ok


def _stream(text: str) -> tuple[dict[str, Any] | None, str]:
    """The harness's JSONL output as one result dict, or `(None, reason)`.

    Line 1 is the header; every later line is one property-input record, flushed as written, so a
    killed harness still yields the pairs it finished. A line that does not parse ends the stream —
    only the torn tail of an interrupted write is legitimately incomplete. A header carrying its own
    `records` is refused: the harness never writes that shape, so the candidate wrote the file.
    """
    lines = text.splitlines()
    if not lines:
        return None, "no result file"
    try:
        header = json.loads(lines[0])
    except json.JSONDecodeError:
        return None, "result file does not open with a header line"
    if not isinstance(header, dict) or "props" not in header:
        return None, "result header names no properties"
    if "records" in header:
        return None, "result header carries records, which the harness never writes"
    records = []
    for line in lines[1:]:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return {**header, "records": records}, ""


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


class Isolation(str, Enum):
    """How far the harness is held away from the host while it runs candidate code."""

    SUBPROCESS = "subprocess"
    DOCKER = "docker"


HARNESS_ENV_ALLOWLIST = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "MPLBACKEND",
    "DOCKER_HOST",
    "DOCKER_CONFIG",
)
DOCKER_WORKDIR = "/pbt"
DOCKER_MEMORY_LIMIT = "2g"
DOCKER_CPU_LIMIT = "2"
DOCKER_PID_LIMIT = 256


def _harness_env() -> dict[str, str]:
    """What a Python program needs, and nothing else.

    The parent's environment holds every provider API key, and the candidate's module-level code
    runs inside the harness process. Inheriting the whole environment handed untrusted code our
    credentials for no gain: the harness imports only the standard library.

    The DOCKER_* entries are read by the `docker run` CLI on this side of the boundary, never by
    the container, which is handed `PBT_RESULT` and nothing else.
    """
    allowed = {key: os.environ[key] for key in HARNESS_ENV_ALLOWLIST if key in os.environ}
    return {**allowed, "PBT_RESULT": RESULT_FILENAME}


def _docker_command(directory: str, image: str, container: str) -> list[str]:
    """`docker run` for the harness: bounded CPU, memory, processes and network.

    Every resource the candidate can consume is capped here, because nothing outside the container
    can cap it: macOS does not enforce RLIMIT_AS/DATA/RSS, so an unbounded candidate takes the host
    down rather than itself. Memory alone is not enough — an uncapped container saturates every core
    it can see and hangs the machine without ever exceeding its memory budget.

    The container name is what lets a timeout `docker kill` exactly this harness — SIGKILLing the
    host `docker run` CLI leaves the container running.
    """
    if not image:
        raise ValueError("Isolation.DOCKER requires a docker image")
    return [
        "docker", "run", "--rm", "--name", container,
        "--network", "none",
        "--memory", DOCKER_MEMORY_LIMIT,
        "--memory-swap", DOCKER_MEMORY_LIMIT,
        "--cpus", DOCKER_CPU_LIMIT,
        "--pids-limit", str(DOCKER_PID_LIMIT),
        "-v", f"{directory}:{DOCKER_WORKDIR}",
        "-w", DOCKER_WORKDIR,
        "-e", f"PBT_RESULT={RESULT_FILENAME}",
        image, "python", HARNESS_FILENAME,
    ]


def _docker_kill(container: str) -> None:
    try:
        subprocess.run(["docker", "kill", container], capture_output=True, timeout=20)
    except Exception:
        pass


def run_raw(
    task: Task,
    code: str,
    props_src: str,
    space: list[Any],
    *,
    timeout_s: int = DEFAULT_PBT_TIMEOUT,
    probe_bare_run: bool = False,
    isolation: Isolation = Isolation.SUBPROCESS,
    docker_image: str = "",
) -> dict[str, Any]:
    """Run every property over every search-space input against one implementation.

    `ok` means the result file yielded a usable header and at least one well-formed record.
    `complete` means the whole grid ran, counted by the parent as distinct pairs rather than read
    off a flag the candidate could write. A missing pair can only lose a catch, never invent one,
    so an incomplete run under-reports and must not be read as a negative.

    `bare_run_ok` is the probe's per-input verdict, `None` unless `probe_bare_run` asked for one.
    """
    directory = tempfile.mkdtemp(prefix="pbt_")
    container = f"pbt-{Path(directory).name}"
    try:
        (Path(directory) / HARNESS_FILENAME).write_text(
            build_harness(
                task,
                code,
                props_src,
                space,
                probe_bare_run=probe_bare_run,
                work_seconds=timeout_s,
            ),
            encoding="utf-8",
        )
        command = (
            _docker_command(directory, docker_image, container)
            if isolation is Isolation.DOCKER
            else [sys.executable, HARNESS_FILENAME]
        )
        proc = subprocess.Popen(
            command,
            cwd=directory,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_harness_env(),
            start_new_session=True,
        )
        hard_kill_s = timeout_s + HARNESS_KILL_GRACE_SECONDS
        try:
            _out, err = proc.communicate(timeout=hard_kill_s)
            exit_note = (
                f"harness exited {proc.returncode}: "
                f"{(err or '').strip() or 'no stderr'}"
            )
        except subprocess.TimeoutExpired:
            if isolation is Isolation.DOCKER:
                _docker_kill(container)
            kill_group(proc)
            try:
                proc.communicate(timeout=PIPE_DRAIN_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            exit_note = f"killed after {hard_kill_s}s"
        result, unreadable = _stream(_result_text(directory))
        if result is None:
            return _failed(f"{unreadable} ({exit_note})")
        try:
            props, records = _validated(result, len(space))
            bare_run_ok = (
                _validated_bare_run(result, len(space)) if probe_bare_run else None
            )
        except (ValueError, TypeError, KeyError) as error:
            return _failed(f"malformed harness result: {error}")
        n_expected = len(props) * len(space)
        n_records = len({(r["prop"], r["i"]) for r in records})
        if n_expected and not n_records:
            return _failed(f"harness recorded no property-input pair ({exit_note})")
        complete = n_records >= n_expected
        return {
            "ok": True,
            "complete": complete,
            "error": None if complete else f"{n_records}/{n_expected} pairs ({exit_note})",
            "records": records,
            "props": props,
            "n_records": n_records,
            "n_expected": n_expected,
            "bare_run_ok": bare_run_ok,
        }
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _result_text(directory: str) -> str:
    try:
        return (Path(directory) / RESULT_FILENAME).read_text(encoding="utf-8")
    except OSError:
        return ""
