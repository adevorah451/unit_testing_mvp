"""Start a run detached, from anywhere, including a notebook cell.

A run must outlive whatever started it. A kernel restart, a closed SSH session and a foreground
shell that exits all kill a child process; a tmux session survives all three. So `launch` returns as
soon as the session is up and the run keeps going without it.

A launch that failed must never read as a launch that succeeded. `launch` waits for the session to
prove it is alive and raises with the captured output if the command died on startup, rather than
returning a session name that no longer exists.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_LIVENESS_GRACE_SECONDS = 3.0
DEFAULT_PANE_LINES = 20
CONSOLE_LOG_NAME = "console.log"
FAILURE_EXCERPT_CHARS = 2000


class LaunchFailed(RuntimeError):
    """A run that never started. Always raised, never returned as a value."""


def sessions() -> list[str]:
    """Every live tmux session name, empty when the server is not running."""
    result = subprocess.run(
        ["tmux", "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return result.stdout.split()


def alive(run_id: str) -> bool:
    return run_id in sessions()


def console_log(run_id: str, cwd: Path = REPO_ROOT) -> Path:
    return cwd / "runs" / run_id / CONSOLE_LOG_NAME


def pane(run_id: str, lines: int = DEFAULT_PANE_LINES) -> str:
    """The tail of a live session's screen. Raises when the session is gone."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-pt", run_id], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise LaunchFailed(f"no tmux session {run_id!r}: {result.stderr.strip()}")
    return "\n".join(result.stdout.splitlines()[-lines:])


def kill(run_id: str) -> None:
    subprocess.run(["tmux", "kill-session", "-t", run_id], capture_output=True, text=True)


def launch(
    run_id: str,
    argv: Sequence[str],
    cwd: Path = REPO_ROOT,
    grace_seconds: float = SESSION_LIVENESS_GRACE_SECONDS,
) -> str:
    """Run `argv` in a detached tmux session named `run_id`, and prove it started.

    Returns the command as launched, with output teed to `runs/<run_id>/console.log`. That log
    exists to explain a failed launch; it is never where a result is read from.

    Refuses a run_id whose session already exists. Two processes writing one run directory corrupt
    it, and re-running under the same `run_name` resumes anyway, so the fix is to attach to the
    running session rather than start a second one beside it.
    """
    if alive(run_id):
        raise LaunchFailed(
            f"tmux session {run_id!r} is already running — attach with "
            f"`tmux attach -t {run_id}` or kill it before relaunching"
        )
    log = console_log(run_id, cwd)
    command = shlex.join(str(argument) for argument in argv)
    wrapped = (
        f"mkdir -p {shlex.quote(str(log.parent))} && "
        f"{command} 2>&1 | tee {shlex.quote(str(log))}"
    )
    started = subprocess.run(
        ["tmux", "new-session", "-d", "-s", run_id, "-c", str(cwd), wrapped],
        capture_output=True,
        text=True,
    )
    if started.returncode != 0:
        raise LaunchFailed(f"tmux refused to start {run_id!r}: {started.stderr.strip()}")
    time.sleep(grace_seconds)
    if not alive(run_id):
        raise LaunchFailed(
            f"{run_id!r} exited within {grace_seconds}s of launch\n"
            f"  command: {command}\n"
            f"  output: {_failure_excerpt(log)}"
        )
    return command


def _failure_excerpt(log: Path) -> str:
    if not log.exists():
        return "(the command produced no output before exiting)"
    return log.read_text(encoding="utf-8", errors="replace")[-FAILURE_EXCERPT_CHARS:]
