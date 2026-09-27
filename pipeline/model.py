"""Model wiring: one `ModelRuntime` in, one awaitable `complete()` out.

Model strings are inspect_ai's:

  - ``openai-api/azureai/<deployment>``  Azure AI Foundry; reads AZUREAI_BASE_URL + AZUREAI_API_KEY
  - ``openai/...`` / ``anthropic/...``   any provider inspect_ai supports
  - ``mockllm/...`` or ``mock``          offline canned responses (tests / smoke runs)
"""

from __future__ import annotations

import asyncio
import os
import threading

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Annotated, Any

KINDS = (
    "property_gen", "trigger_search", "tm", "informed", "plan", "write_from_plan", "test_critique"
)

NEVER_RETURNED = "never_returned"
MOCK_STOP_REASON = "stop"
EMPTY_RESPONSE_STOP_REASON = "empty_response"
NO_REASONING = ""
REASONING_PART_TYPE = "reasoning"

TEMPERATURE_REJECTING_PREFIXES = ("gpt-5", "o1", "o3", "o4", "deepseek-v3.2", "v3.2")
AZURE_KEY_VARS = ("AZUREAI_API_KEY", "AZURE_API_KEY")
AZURE_URL_VARS = ("AZUREAI_BASE_URL", "AZUREAI_ENDPOINT_URL", "AZURE_ENDPOINT_URL")


_MOCK_PROPS = (
    "```python\n"
    "def prop_commutative(run, x):\n"
    '    """PROPERTY: the function is commutative: f(a, b) == f(b, a)."""\n'
    '    assert run(x) == run({"a": x["b"], "b": x["a"]})\n'
    "```\n"
)
_MOCK: dict[str, str] = {
    "property_gen": _MOCK_PROPS,
    "write_from_plan": _MOCK_PROPS,
    "plan": "1. PROPERTY: f is commutative, f(a,b)==f(b,a). CHECK VIA: run(x) vs run(swapped x). "
    "TARGET INPUTS: small pairs like {a:0,b:0}, {a:1,b:2}.",
    "trigger_search": '[{"a": 999, "b": 1}, {"a": 1, "b": 2}, {"a": 0, "b": 0}]',
    "tm": "Looks routine.\nSUSPICION_SCORE: 3",
    "informed": "No property failure noted.\nSUSPICION_SCORE: 3",
    "test_critique": '{"drop": [], "why": "every claim is forced by the specification."}',
}


@dataclass(frozen=True)
class Completion:
    """One model completion and the provider's reason for stopping.

    A monitor score parsed from a trailing `SUSPICION_SCORE:` line is None both for an answer cut
    short by the token cap and for a call that never happened; `stop_reason` is the only thing that
    keeps them apart, and its default means never returned at all. `text` is the answer alone, and
    `reasoning` is empty when the provider sends none — not the same as the model not reasoning.
    """

    text: str = ""
    stop_reason: str = NEVER_RETURNED
    reasoning: str = ""


def _reasoning(out: Any) -> str:
    """Every `ContentReasoning` part of the response, joined, or empty when it carries none."""
    content = getattr(getattr(out, "message", None), "content", None)
    if not isinstance(content, list):
        return NO_REASONING
    return "\n".join(
        part.reasoning
        for part in content
        if getattr(part, "type", "") == REASONING_PART_TYPE
        and isinstance(getattr(part, "reasoning", None), str)
    )


class TrustedModel:
    """Thin async wrapper over one inspect model: ``await model.complete(prompt, kind) -> str``,
    or ``await model.completion(prompt, kind) -> Completion`` when the stop reason matters."""

    def __init__(self, runtime: ModelRuntime):
        self.runtime = runtime
        self._mock = runtime.name.startswith("mock")
        self._model = None

    @property
    def name(self) -> str:
        return self.runtime.name

    @property
    def reasoning_effort(self) -> str:
        return self.runtime.reasoning_effort.value

    @property
    def max_tokens(self) -> int:
        return self.runtime.max_tokens

    def _rejects_temperature(self) -> bool:
        """Reasoning models (gpt-5, o1, o3, o4, DeepSeek-V3.2) reject non-default temperature."""
        base = self.runtime.name.lower().split("/")[-1]
        return base.startswith(TEMPERATURE_REJECTING_PREFIXES)

    def _gen_config(self):
        from inspect_ai.model import GenerateConfig

        cfg: dict[str, Any] = {
            "attempt_timeout": self.runtime.attempt_timeout,
            "timeout": self.runtime.http_timeout,
            "max_retries": self.runtime.http_retries,
            "max_connections": self.runtime.max_connections,
            "max_tokens": self.runtime.max_tokens,
            "seed": self.runtime.seed,
            "reasoning_effort": self.runtime.reasoning_effort.value,
        }
        if not self._rejects_temperature():
            cfg["temperature"] = self.runtime.temperature
        return GenerateConfig(**cfg)

    def _resolve_inspect(self):
        if self._model is None:
            from inspect_ai.model import get_model

            self._model = get_model(self.runtime.name, config=self._gen_config())
        return self._model

    async def complete(self, prompt: str, kind: str) -> str:
        return (await self.completion(prompt, kind)).text

    async def completion(self, prompt: str, kind: str, schema: Any = None) -> Completion:
        """The same single call as `complete`, keeping the provider's stop reason attached.

        `schema` is an `inspect_ai.model.ResponseSchema`; when given, a malformed answer fails at
        the API rather than reaching the parser here.
        """
        if kind not in KINDS:
            raise ValueError(f"unknown model-call kind: {kind}")
        if self._mock:
            return Completion(_MOCK[kind], MOCK_STOP_REASON, reasoning=NO_REASONING)
        from inspect_ai.model import ChatMessageUser, GenerateConfig

        config = GenerateConfig(response_schema=schema) if schema is not None else None
        out = await self._resolve_inspect().generate(
            [ChatMessageUser(content=prompt)], cache=self.runtime.inspect_cache, config=config
        )
        if out.empty:
            return Completion("", EMPTY_RESPONSE_STOP_REASON, reasoning=_reasoning(out))
        return Completion(
            out.completion or out.message.text, out.stop_reason, reasoning=_reasoning(out)
        )


def resolve(runtime: ModelRuntime) -> TrustedModel:
    """Build a model from its runtime knobs, validating provider credentials up front.

    Fans the single `AZUREAI_*` pair out to the variable names inspect_ai and the SDKs beneath it
    each expect, by `setdefault`, so anything already exported wins.
    """
    if not runtime.name:
        raise ValueError(
            "a model is required: pass model='openai-api/azureai/DeepSeek-V3.2' to the protocol, "
            "or model='mock' for the canned answer"
        )

    key = next((os.getenv(alias) for alias in AZURE_KEY_VARS if os.getenv(alias)), None)
    url = next((os.getenv(alias) for alias in AZURE_URL_VARS if os.getenv(alias)), None)
    
    if key:
        os.environ.setdefault("AZURE_API_KEY", key)
        os.environ.setdefault("AZURE_OPENAI_API_KEY", key)
        os.environ.setdefault("OPENAI_API_KEY", key)
    if url:
        os.environ.setdefault("AZUREAI_ENDPOINT_URL", url)
        os.environ.setdefault("AZURE_OPENAI_ENDPOINT", url)
        os.environ.setdefault("OPENAI_BASE_URL", url)

    if "azureai" in runtime.name.split("/"):
        _require_azure_credentials(runtime.name)
    return TrustedModel(runtime)


def _require_azure_credentials(name: str) -> None:
    missing = [
        var
        for var, group in (
            ("AZUREAI_API_KEY", AZURE_KEY_VARS),
            ("AZUREAI_BASE_URL", AZURE_URL_VARS),
        )
        if not any(os.getenv(alias) for alias in group)
    ]
    if missing:
        raise OSError(
            f"Azure model {name!r} needs {' + '.join(missing)} in .env (see .env.example). "
            "For openai-api/azureai/<deployment>, AZUREAI_BASE_URL is the OpenAI-compatible "
            "endpoint: https://<resource>.services.ai.azure.com/openai/v1"
        )


INSPECT_LOOP_THREAD = "inspect-loop"
BRIDGE_MARGIN_SECONDS = 60

_LOOP: Any = None
_LOOP_LOCK = threading.Lock()


class ModelCallFailed(RuntimeError):
    """A model call that ended in something the loop must not be allowed to die of."""


def shared_loop() -> Any:
    """The one event loop every inspect call in this process runs on, started on first use.

    Process-wide, not per-run: `get_model` memoises one `Model` per (name, config), and its httpx
    pool and inspect's semaphores bind to the loop that first awaited them, so a call made on any
    second loop fails on state the first one closed.
    """
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name=INSPECT_LOOP_THREAD, daemon=True
            ).start()
            _LOOP = loop
    return _LOOP


async def _survivable(awaitable: Any) -> Any:
    """Await something, re-raising an escaping `BaseException` as `ModelCallFailed`.

    A `KeyboardInterrupt` or `SystemExit` out of a coroutine propagates into `run_forever` and ends
    the loop thread, after which every later call blocks forever on a future nothing will complete.
    """
    try:
        return await awaitable
    except Exception:
        raise
    except BaseException as stop:
        raise ModelCallFailed(f"{type(stop).__name__}: {stop}") from stop


def complete_sync(
    model: TrustedModel, prompt: str, kind: str, schema: Any = None
) -> Completion:
    """One model call from a worker thread, awaited on the shared loop and re-raised here.

    The `.result()` bound is the runtime's http budget plus a margin, so it can only fire after
    every retry the call was entitled to. Failures propagate deliberately: a call that never
    returned is infra, and `Run._record_for` is the single place that blame is stated.
    """
    loop = shared_loop()
    if not loop.is_running():
        raise ModelCallFailed(
            "the shared inspect loop is not running, so this call would block forever on a future "
            "nothing will complete. The loop thread only dies if something escaped `_survivable`; "
            "restart the process rather than trusting anything it scored after that point"
        )
    call = model.completion(prompt, kind) if schema is None else model.completion(prompt, kind, schema)
    pending = asyncio.run_coroutine_threadsafe(_survivable(call), loop)
    return pending.result(timeout=model.runtime.http_timeout + BRIDGE_MARGIN_SECONDS)



class Reasoning(str, Enum):
    """Reasoning effort, which sets both the token cap and the measured per-call budget."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MAX = "max"

MEASURED_CALL_SECONDS_BY_EFFORT = {
    Reasoning.LOW: 120,
    Reasoning.MEDIUM: 300,
    Reasoning.HIGH: 420,
    Reasoning.MAX: 420,
}

MAX_TOKENS_BY_EFFORT = {
    Reasoning.LOW: 12_000,
    Reasoning.MEDIUM: 32_000,
    Reasoning.HIGH: 32_000,
    Reasoning.MAX: 32_000,
}

MEASURED_SAFE_CONCURRENCY = 12

MODEL_POOL_CONNECTIONS = 16

SINGLE_RUN_TEMPERATURE = 0.0

MULTI_RUN_TEMPERATURE = 0.7

RETRY_BUDGET_PER_CALL = 2

MIN_RETRY_BUDGET_SECONDS = 240

OUTER_CALL_MARGIN_SECONDS = 60

CALLS_PER_UNIT = 3

UNITS_PER_RUN = 4

MIN_RUN_BUDGET_SECONDS = 3600

ENGINE_CALL_RETRIES = 2

INSPECT_HTTP_RETRIES = 1

RUN_RETRIES = 2

MOCK_MODEL = "mock"

DERIVE_FROM_REASONING = 0

Seconds = Annotated[int, Field(ge=0)]

class ModelSpec(BaseModel):
    """A model, its reasoning effort, and the only timeout a caller should set.

    `timeout` bounds one provider request and every wider bound derives from it. Zero means derive
    it from `reasoning`.
    """

    model_config = ConfigDict(frozen=True)

    name: str = ""
    reasoning: Reasoning = Reasoning.LOW
    timeout: Seconds = DERIVE_FROM_REASONING

    @property
    def resolved_timeout(self) -> int:
        return self.timeout or MEASURED_CALL_SECONDS_BY_EFFORT[self.reasoning]

    @property
    def max_tokens(self) -> int:
        return MAX_TOKENS_BY_EFFORT[self.reasoning]

class TimeoutLadder(BaseModel):
    """Nested request bounds, rejected at construction unless strictly increasing outwards.

        per-call attempt < http retry budget < outer call < unit < run
    """

    model_config = ConfigDict(frozen=True)

    trusted_call: Seconds
    untrusted_call: Seconds
    http_retry_budget: Seconds
    outer_call: Seconds
    unit: Seconds
    run: Seconds

    @model_validator(mode="after")
    def _strictly_increasing(self) -> TimeoutLadder:
        rungs = [
            max(self.trusted_call, self.untrusted_call),
            self.http_retry_budget,
            self.outer_call,
            self.unit,
            self.run,
        ]
        if rungs != sorted(set(rungs)):
            raise ValueError(
                f"timeout ladder is not strictly increasing: {self.model_dump()}"
            )
        return self

    @classmethod
    def derive(cls, trusted: ModelSpec, untrusted: ModelSpec) -> TimeoutLadder:
        slowest = max(trusted.resolved_timeout, untrusted.resolved_timeout)
        http_retry_budget = max(
            MIN_RETRY_BUDGET_SECONDS, RETRY_BUDGET_PER_CALL * slowest
        )
        outer_call = http_retry_budget + OUTER_CALL_MARGIN_SECONDS
        unit = CALLS_PER_UNIT * outer_call
        return cls(
            trusted_call=trusted.resolved_timeout,
            untrusted_call=untrusted.resolved_timeout,
            http_retry_budget=http_retry_budget,
            outer_call=outer_call,
            unit=unit,
            run=max(MIN_RUN_BUDGET_SECONDS, UNITS_PER_RUN * unit),
        )

class ModelRuntime(BaseModel):
    """Every endpoint knob for one model, resolved once by the protocol, never re-read from the env.

    Complete enough that a subprocess or Docker worker rebuilds a model identical to the parent's.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    temperature: float = Field(0.0, ge=0.0)
    seed: int = Field(0, ge=0)
    attempt_timeout: Seconds
    http_timeout: Seconds
    http_retries: int = Field(ge=0)
    max_tokens: int = Field(gt=0)
    reasoning_effort: Reasoning
    inspect_cache: bool = True
    max_connections: int = Field(MODEL_POOL_CONNECTIONS, ge=1)
