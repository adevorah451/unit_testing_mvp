"""Turning one completion into the two things a protocol can run: test source, and trigger inputs.

`read_input_lines` is the single place a line becomes a value; both the search space and the
dropped-input diagnostic read it, so an explanation cannot disagree with the parse it explains.
Both parsers are tolerant, and a shortened search space is not an error — so the count of inputs
lost travels with the space rather than a bare flag.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

from .prompts import SUITE_FUNCTION_PREFIXES

_SINGLE_WORD_INFO_STRING = r"(?:[A-Za-z0-9_+#.-]*[ \t]*\r?\n)?"
FENCED_CODE = rf"```{_SINGLE_WORD_INFO_STRING}\s*(.*?)```"
_FENCED_TAGGED = r"```(?:([A-Za-z0-9_+#.-]*)[ \t]*\r?\n)?\s*(.*?)```"
_FENCED_JSON_ARRAY = rf"```{_SINGLE_WORD_INFO_STRING}\s*(\[.*?\])\s*```"
PYTHON_INFO_STRINGS = ("python", "py", "python3")


def _extract_code(completion: str) -> str:
    """The last python-tagged fenced block, with any single-word language tag dropped.

    Tag before length, and last before first. Length picks whichever block is biggest, which is the
    brute-force reference an answer shows before its real solution, or a commented-out outline; both
    parse, so nothing downstream reports the wrong block. Where an answer revises itself the later
    block is the one it means, and an untagged block is prose until proven otherwise.

    A multi-word info string is left in the block, where it surfaces as a SyntaxError rather than
    silently eating the first line of code.
    """
    blocks = re.findall(_FENCED_TAGGED, completion or "", re.DOTALL)
    if blocks:
        python = [body for tag, body in blocks if tag.lower() in PYTHON_INFO_STRINGS]
        return (python or [body for _, body in blocks])[-1].strip()
    text = (completion or "").strip()
    return text if "def " in text else ""


def parse_program(completion: str) -> tuple[str | None, str | None]:
    """Return (source, error) for an answer that is a whole program, not a test suite.

    Unlike `parse_properties` this requires no ``prop_``/``test_`` function — a solver answers with
    a script that reads stdin. The source must still parse, so a truncated answer is an error rather
    than something a sandbox discovers later, and it must contain a statement: a block of comments
    parses to an empty body and would otherwise be recorded as a solution that reads nothing and
    prints nothing.
    """
    source = _extract_code(completion)
    if not source:
        return None, "no fenced code block in the completion"
    try:
        tree = ast.parse(source)
    except SyntaxError as broken:
        return None, f"the program does not parse: {broken}"
    if not tree.body:
        return None, "the block parses but defines and does nothing — comments only"
    return source, None


def parse_properties(completion: str) -> tuple[str | None, str | None]:
    """Return (source, error). Source must parse and define a top-level ``prop_`` or ``test_`` function.

    Async properties are an error, not a suite: the harness calls properties synchronously, so a
    coroutine would record a pass without running its body.
    """
    src = _extract_code(completion)
    if not src:
        return None, "no code block in completion"
    try:
        tree = ast.parse(src)
    except SyntaxError as error:
        return None, f"SyntaxError: {error}"
    funcs = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith(SUITE_FUNCTION_PREFIXES)
    ]
    if not funcs:
        return None, "no top-level prop_/test_ functions defined"
    coroutines = [n.name for n in funcs if isinstance(n, ast.AsyncFunctionDef)]
    if coroutines:
        return None, (
            f"async properties are never awaited by the harness: {', '.join(coroutines)}"
        )
    return src, None


def _salvage_json_array(blob: str) -> list[Any] | None:
    """Parse the leading complete elements of a (possibly truncated) JSON array."""
    blob = blob.strip()
    if not blob.startswith("["):
        return None
    decoder = json.JSONDecoder()
    items: list[Any] = []
    i, n = 1, len(blob)
    while i < n:
        while i < n and blob[i] in " \t\r\n,":
            i += 1
        if i >= n or blob[i] == "]":
            break
        try:
            obj, i = decoder.raw_decode(blob, i)
        except json.JSONDecodeError:
            break
        items.append(obj)
    return items or None


_MIN_LINES_TO_READ_AS_ONE_PER_LINE = 2
STRUCTURAL_LINES = ("[", "]", "[]")


def input_block(completion: str) -> str:
    """The fenced block the inputs live in, or the whole text when the model wrote no fence."""
    fenced = re.findall(FENCED_CODE, completion, re.DOTALL)
    return max(fenced, key=len) if fenced else completion


def _as_json_shapes(value: Any) -> Any:
    """Python literals rewritten into the shapes JSON has, which means tuples become lists.

    A list is not a tuple — it is unhashable, so it cannot key a dict or enter a set, and
    `isinstance(x, tuple)` turns False. Every task reached this way so far only indexes and iterates
    its argument, where the two behave identically; a task that branched on the type would be
    silently handed something else, which is why `read_input_lines` records that the rewrite
    happened rather than doing it invisibly.
    """
    if isinstance(value, (tuple, list)):
        return [_as_json_shapes(item) for item in value]
    if isinstance(value, dict):
        return {key: _as_json_shapes(item) for key, item in value.items()}
    return value


def _literal_line(line: str) -> tuple[Any, bool]:
    """`(value, True)` for a line that is a Python literal JSON cannot spell, else `(None, False)`.

    `ast.literal_eval` and not `eval`: it accepts literals only — no calls, no names, no attribute
    access — so a completion cannot execute anything here. It also refuses arithmetic, which is
    deliberate. `{"xs": [("a", 2)] * 64}` and `2**31` are operations rather than literals and stay
    unread, counted in `dropped` like any other line this parse cannot take.

    The value must also survive `json.dumps`, because the record carrying it is written as JSON.
    `literal_eval` spells types JSON has no form for — `4j`, `b"x"`, `{1, 2}`, a tuple used as a
    dict key — and `_as_json_shapes` only reshapes containers, so one of those passes straight
    through. Accepting it trades a line that used to be dropped for a run that dies at the write,
    after every model call has been paid for, which is how a BigCodeBench trigger search died on a
    complex literal. Dropping it here costs one input out of thirty and keeps the artifact.
    """
    try:
        value = _as_json_shapes(ast.literal_eval(line))
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None, False
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return None, False
    return value, True


def read_input_lines(blob: str) -> list[dict[str, Any]]:
    """Every candidate input line, with what it parsed to or why it did not parse.

    `ok` carries whether the line read, because `value` alone cannot: a line holding `null` parses
    fine and to `None`, indistinguishable from a line that failed.

    JSON first, always. A line JSON accepts is read exactly as before, so this function can only
    turn a line that used to fail into one that reads — no existing parse changes. The fallback is
    for the one shape the prompt asks for but JSON cannot spell: a task whose argument is described
    as a list of *tuples* gets tuples, faithfully, and `(` is not JSON. `rewritten` marks those, so
    a reader can tell a literal-parsed line from a JSON one.
    """
    read: list[dict[str, Any]] = []
    for number, raw in enumerate(blob.splitlines(), 1):
        line = raw.strip().rstrip(",").strip()
        if not line or line in STRUCTURAL_LINES:
            continue
        try:
            read.append({"line": number, "text": line, "ok": True, "value": json.loads(line),
                         "reason": "", "rewritten": False})
        except json.JSONDecodeError as error:
            value, recovered = _literal_line(line)
            read.append({"line": number, "text": line, "ok": recovered, "value": value,
                         "reason": "" if recovered else error.msg, "rewritten": recovered})
    return read


def _parse_one_per_line(blob: str) -> tuple[list[Any] | None, int]:
    """`(items, dropped)` for the one-value-per-line shape, or `(None, 0)` if it is not that shape.

    A line parsing to a list, or fewer than two items, falls through to the array parser. Complete
    elements of a truncated pretty-printed array are rescued here, since each sits on its own line.
    """
    items: list[Any] = []
    dropped = 0
    for entry in read_input_lines(blob):
        if not entry["ok"]:
            dropped += bool(items)
            continue
        if isinstance(entry["value"], list):
            return None, 0
        items.append(entry["value"])
    if len(items) < _MIN_LINES_TO_READ_AS_ONE_PER_LINE:
        return None, 0
    return items, dropped


def parse_search_space(
    completion: str,
) -> tuple[list[Any] | None, str | None, int | None]:
    """Return (space, error, dropped), reading one JSON value per line or a single JSON array.

    `dropped` counts the inputs the model wrote that this parse could not read: ``0`` is nothing
    lost, ``n`` is n inputs lost so the suite searched a smaller space than it was asked for, and
    ``None`` is inputs lost with the count unrecoverable — a truncated array is cut to its leading
    complete elements. A shortened space is not an error, but it is the ceiling on everything
    downstream.
    """
    text = (completion or "").strip()
    per_line, dropped = _parse_one_per_line(input_block(text))
    if per_line:
        return per_line, None, dropped
    fence = re.search(_FENCED_JSON_ARRAY, text, re.DOTALL)
    if fence:
        blob = fence.group(1)
    else:
        start = text.find("[")
        end = text.rfind("]")
        blob = (
            text[start : end + 1] if start != -1 and end != -1 and start < end else None
        )
    if blob is None:
        return None, "no JSON array found", 0
    blob = blob.strip()
    dropped: int | None = 0
    try:
        arr = json.loads(blob)
    except Exception:
        arr = _salvage_json_array(blob)
        dropped = None if arr is not None else 0
        if arr is None:
            return None, "JSON parse error (unsalvageable)", 0
    if not isinstance(arr, list) or not arr:
        return None, "search space is not a non-empty list", 0
    return arr, None, dropped
