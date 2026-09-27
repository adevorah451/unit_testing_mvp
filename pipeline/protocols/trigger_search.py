"""Trigger search: the candidate inputs a suite is later run over, bought once and reused.

A run of its own, so every prompt variant searches the same space — `UnitTesting` reads
`runs/<trigger run>/records.jsonl`.

`dropped` is a count and never a flag: `0` is a clean parse, `n` is `n` inputs lost, and `None` says
inputs were lost but the magnitude is not recoverable. A candidate whose call produced no usable
space at all is `failed`, never `"inputs": []` — an empty list reads downstream as a search that
found nothing, which is a claim about the model rather than about the measurement.
"""

from __future__ import annotations

from typing import Any

from .. import model as model_mod
from .. import prompts
from ..model import INSPECT_HTTP_RETRIES, MULTI_RUN_TEMPERATURE, ModelRuntime, ModelSpec, Reasoning, SINGLE_RUN_TEMPERATURE, TimeoutLadder
from ..data import Blame, Candidate, Dataset, Task
from ..parse import parse_search_space
from .base import CALLS, Run

TRIGGER_SEARCH_KIND = "trigger_search"
DEFAULT_NUM_INPUTS = 30
DEFAULT_REASONING = Reasoning.LOW.value
MAX_REASON_CHARS = 300
CLOSING_BRACKET = "]"
CUT_OFF_BY_THE_CAP = frozenset({"max_tokens", "model_length"})

PROMPT_FILE_PARAM = "prompt_file"
VISIBILITY = {True: "visible", False: "hidden"}
TEMPLATE_BY_VISIBILITY = {
    visible: prompts.PROMPT_BY_STAGE_AND_VISIBILITY[prompts.INPUTS_STAGE, named]
    for visible, named in VISIBILITY.items()
}
INPUT_SHAPE_BY_IO_MODE = {prompts.STDIO: str, prompts.FUNCTION: dict}
INPUT_SHAPE_NAME_BY_IO_MODE = {
    prompts.STDIO: "stdin strings",
    prompts.FUNCTION: "keyword-argument objects",
}

OUTPUT_INPUT_LINES = """\
A RATIONALE, then one fenced block, and nothing else.

RATIONALE — a short paragraph, then one numbered line per input, under twenty words, naming the condition it is built to reach. Say {rationale_for_inputs}. {rationale_brevity}.

Then the text block, holding exactly {n_inputs} inputs, one per line — {input_line}

The line is read as JSON, not as Python, so `+`, `" ".join(...)`, `range(...)` and f-strings are discarded unparsed. Write each input out in full; if that feels too long, it is too large."""


def output_contract(io_mode: str, n_inputs: int) -> str:
    """What the inputs call must return: one JSON value per line, or a single JSON array.

    `pipeline/parse.py` unwraps nothing, so a contract asking for wrapper objects — the old one-call format's
    `{"stdin": ..., "targets": ...}` entries — would have the WRAPPERS read as the inputs and hand a
    stdio candidate a list of dicts: a suite that runs and asserts nothing about the program.
    """
    return OUTPUT_INPUT_LINES.format(
        n_inputs=n_inputs,
        rationale_for_inputs=prompts.RATIONALE_FOR_INPUTS,
        rationale_brevity=prompts.RATIONALE_BREVITY,
        input_line=prompts.INPUT_LINE_BY_IO_MODE[io_mode],
    )


def wrong_shape(space: list[Any], io_mode: str) -> str:
    """Why this space cannot be handed to a candidate in this io_mode, or `""` when it can.

    A stdio candidate is handed its input on STDIN and a function candidate as `**kwargs`, so an
    input of the other shape makes the suite that later runs on it answer a question the candidate
    was never asked — and report clean. The whole call is refused rather than the space passed on.
    """
    shape = INPUT_SHAPE_BY_IO_MODE[io_mode]
    wrong = [value for value in space if not isinstance(value, shape)]
    if not wrong:
        return ""
    return (
        f"{len(wrong)} of {len(space)} inputs are not "
        f"{INPUT_SHAPE_NAME_BY_IO_MODE[io_mode]} (first: {wrong[0]!r:.60})"
    )




def closed_at_the_last_complete_input(text: str) -> str:
    """An answer cut off mid-array, closed so `parse_search_space`'s salvage can read what survived.

    `parse_search_space` needs a `]` before it will consider an array at all, so an unclosed array is
    thrown away whole. Whatever the repaired text parses to must be reported as `dropped=None`, never
    as the second parse's count: an answer cut between two elements closes into a syntactically whole
    array and would otherwise read as a clean `0`.
    """
    opening = text.find("[")
    if opening == -1 or CLOSING_BRACKET in text[opening:]:
        return text
    return text[opening:] + CLOSING_BRACKET


def searched(
    io_mode: str, completion: Any
) -> tuple[list[Any], int | None, tuple[str, str] | None]:
    """One repetition's inputs, what the parse cost, and `(blame, reason)` when there are none.

    Blame is decided here, where it is still known: an answer that came back and would not parse is
    the model's, and the stop reason travels with it so a token cap is not read afterwards as a
    refusal. A call that never returned raises instead, and the base books it as ours.

    `dropped` is `None` and never a count whenever the answer was cut off — by the repair or by the
    provider's stop reason — and on a repetition that yielded nothing: one character either side of
    a cut decides whether the same fragment salvages or parses to a clean `0`. Only the two length
    stop reasons count as a cut, since inspect returns `"unknown"` for any reason it does not
    recognise.
    """
    section = prompts.input_section(completion.text)
    found, error, lost = parse_search_space(section)
    if found is None:
        found, error, _ = parse_search_space(closed_at_the_last_complete_input(section))
        lost = None
    if found is None:
        return (
            [],
            None,
            (
                Blame.MODEL.value,
                f"{error} (stop_reason {completion.stop_reason}, "
                f"{len(completion.text)} chars)",
            ),
        )
    mis_shaped = wrong_shape(found, io_mode)
    if mis_shaped:
        return [], None, (Blame.MODEL.value, mis_shaped)
    if completion.stop_reason in CUT_OFF_BY_THE_CAP:
        return found, None, None
    return found, lost, None


def call_record(
    rep: int, seed: int, prompt: str, completion: Any, refusal: tuple[str, str] | None
) -> dict[str, Any]:
    """One model call as the record carries it: prompt AS SENT, answer, thinking, and its blame.

    The prompt is stored rather than re-derived from the recorded flags, which diverges the first
    time a template is edited. `stop_reason` travels too: a token-cap truncation and a refusal both
    arrive as a space that would not parse, and only the stop reason tells them apart afterwards.

    `blame` and `reason` are per repetition, `None` on one that produced a space. At `runs > 1` the
    record-level pair goes clean the moment any repetition returns, so a repetition lost to infra
    would otherwise read as the search having low reach. The shape is `tm._one_repetition`'s.
    """
    blame, reason = refusal if refusal is not None else (None, None)
    return {
        "rep": rep,
        "seed": seed,
        "prompt": prompt,
        "raw": completion.text,
        "reasoning": completion.reasoning,
        "stop_reason": completion.stop_reason,
        "blame": blame,
        "reason": reason,
    }


class TriggerSearch(Run):
    """One inputs call per candidate: the space a later `UnitTesting` run searches.

    `code_visible` picks the template; there is no `prompt_file` argument to name one while
    recording the other.

    With `runs > 1` the repetitions are CONCATENATED, duplicates and all — reaching inputs the first
    seed did not is the whole reason to pay for a second. Deduplicating would make `n_parsed`
    disagree with what the parser returned, with nothing in `dropped` to account for the gap.
    """

    protocol = "trigger_search"

    def __init__(
        self,
        *,
        num_inputs: int = DEFAULT_NUM_INPUTS,
        code_visible: bool = True,
        reasoning: str = DEFAULT_REASONING,
        **kwargs: Any,
    ) -> None:
        if PROMPT_FILE_PARAM in kwargs:
            raise TypeError(
                f"{PROMPT_FILE_PARAM}={kwargs[PROMPT_FILE_PARAM]!r}: the template is selected by "
                f"code_visible, which picks {TEMPLATE_BY_VISIBILITY}. `Run` writes every constructor "
                "argument into config.json, so a name nothing acts on would be recorded as this "
                "arm's setting and read back later as the template it ran under"
            )
        super().__init__(
            num_inputs=num_inputs,
            code_visible=code_visible,
            reasoning=reasoning,
            **kwargs,
        )
        if num_inputs < 1:
            raise ValueError(
                f"{self.run_name}: num_inputs={num_inputs} would ask for a space with nothing in it, "
                "and every suite run against it would report clean for free"
            )
        self.num_inputs = num_inputs
        self.code_visible = bool(code_visible)
        self.reasoning = Reasoning(reasoning)
        self.template = TEMPLATE_BY_VISIBILITY[self.code_visible]
        self.worked_example = ""
        self.clients: tuple[Any, ...] = ()

    def prepare(self, data: Dataset) -> None:
        """Load the worked example and build one model client per repetition, before any candidate.

        A missing example file, an unnamed model or absent Azure credentials fails the whole run
        here, as one message at the top of `console.log`, instead of arriving as every candidate's
        infra failure in turn with the artifact half written. The example is chosen by the dataset's
        `io_mode`, which `Dataset` guarantees every task agrees with.
        """
        self.worked_example = prompts.fragment(
            prompts.worked_example_file(self.template, data.io_mode)
        )
        self.clients = tuple(model_mod.resolve(self.runtime(seed)) for seed in self.seeds)

    def runtime(self, seed: int) -> ModelRuntime:
        """The endpoint knobs for one repetition, with the run's RESOLVED cache choice on them.

        The cache key includes the seed, and the seed is the only thing that varies here —
        `model.TEMPERATURE_REJECTING_PREFIXES` swallows temperature on the provider we use. The base
        has already refused `runs > 1` with caching on; this passes that decision through rather
        than re-deciding it. The timeout ladder is derived from the reasoning effort rather than
        exposed as a knob, so a run cannot get a retry budget shorter than the call it is retrying.
        """
        spec = ModelSpec(name=self.model, reasoning=self.reasoning)
        ladder = TimeoutLadder.derive(spec, spec)
        return ModelRuntime(
            name=self.model,
            temperature=SINGLE_RUN_TEMPERATURE if self.runs == 1 else MULTI_RUN_TEMPERATURE,
            seed=seed,
            attempt_timeout=ladder.trusted_call,
            http_timeout=ladder.http_retry_budget,
            http_retries=INSPECT_HTTP_RETRIES,
            max_tokens=spec.max_tokens,
            reasoning_effort=spec.reasoning,
            inspect_cache=self.cache,
        )

    def prompt(self, task: Task, candidate: Candidate) -> str:
        """The inputs call for one candidate, built from `task.blind()` and the protocol's own slots.

        The spec-only template is given no `code` slot at all rather than an empty one: `render`
        refuses an empty substitution, and a blank code block would read to the model as a
        submission that is empty rather than one that is withheld.
        """
        slots: dict[str, Any] = {
            **task.blind(),
            "n_inputs": self.num_inputs,
            "output_contract": output_contract(task.io_mode, self.num_inputs),
            "worked_example": self.worked_example,
        }
        if self.code_visible:
            slots["code"] = candidate.code
        return prompts.render(self.template, **slots)

    def score(self, task: Task, candidate: Candidate) -> dict[str, Any]:
        """One candidate's search space, with what it cost to read it.

        A repetition that produced nothing usable drives `dropped` to `None` rather than adding a
        number nobody measured, and names its own blame in its entry of `calls` — the only place a
        partial loss appears, since a record whose other repetition returned a space is not `failed`.

        When NO repetition produced a space the record fails with `blame="model"`. It is always
        `model`: a call that never returned raises out of `complete_sync` and the base books the
        candidate as infra, so every repetition reaching here answered and the answer would not
        parse or was the wrong shape.
        """
        if not self.clients:
            raise RuntimeError(
                f"{self.run_name}: no model client, so prepare() did not run — scoring now would "
                "record every candidate as a model that answered nothing"
            )
        prompt = self.prompt(task, candidate)
        calls: list[dict[str, Any]] = []
        space: list[Any] = []
        dropped: int | None = 0

        for rep, client in enumerate(self.clients):
            completion = model_mod.complete_sync(client, prompt, TRIGGER_SEARCH_KIND)
            found, lost, refusal = searched(task.io_mode, completion)
            calls.append(call_record(rep, self.seeds[rep], prompt, completion, refusal))
            space.extend(found)
            dropped = None if dropped is None or lost is None else dropped + lost

        verdict = {
            CALLS: calls,
            "inputs": space,
            "n_requested": self.num_inputs * len(self.seeds),
            "n_parsed": len(space),
            "dropped": dropped,
        }
        if space:
            return verdict
        refused = [call for call in calls if call["blame"] is not None]
        return {
            **verdict,
            "failed": True,
            "blame": Blame.MODEL.value,
            "reason": "; ".join(
                f"rep {call['rep']}: {call['reason']}" for call in refused
            )[:MAX_REASON_CHARS],
        }
