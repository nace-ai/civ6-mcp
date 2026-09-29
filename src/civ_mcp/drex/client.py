"""HTTP client for Drex's ``POST /v1/systemone`` Choice question.

Wire contract (verified 2026-09-29 from https://nace.ai/drex.md and the
wire-compatible TypeSafe API reference, https://docs.typesafe.ai/api.md):

- Base URL ``https://drex.nace.ai``; ``Authorization: Bearer <key>``.
- Request: ``{"model", "state", "questions": {id: {"type": "choice",
  "instructions", "criteria": {option: string|null}}}}``. Drex rejects
  object descriptions with 422 (TypeSafe accepts them).
- Response: ``{"model", "answers": {id: {"type": "choice", "choice",
  "probabilities": {option: p}, "confidence"}}, "usage": {...}}``.
- 401 bad key, 422 invalid body, 429 rate limited, 529 overloaded.
- At most 255 options per Choice (confirmed on Drex: 256 returns 422).

Each call is a single attempt; callers own the retry budget so that
transport and answer-validation failures share one bound.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from civ_mcp.drex.decision import ChoiceAnswer

DEFAULT_BASE_URL = "https://drex.nace.ai"
DEFAULT_MODEL = "drex-latest"
DEFAULT_ENV_FILE = Path.home() / ".config" / "civ-drex" / "drex.env"
MAX_CHOICE_OPTIONS = 255
QUESTION_ID = "decision"

_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


class DrexError(Exception):
    retryable = False


class DrexConfigError(DrexError):
    pass


class DrexAuthError(DrexError):
    pass


class DrexRequestRejected(DrexError):
    pass


class DrexRequestInvalid(DrexError, ValueError):
    """The request violates a local limit and was not sent."""


class DrexModelMismatch(DrexError):
    pass


class DrexProtocolError(DrexError):
    """Malformed response body; retried within the caller's bound."""

    retryable = True


class DrexUnavailable(DrexError):
    retryable = True

    def __init__(self, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.retry_after_s = retry_after_s


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class DrexConfig:
    api_key: str = field(repr=False)
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = 15.0
    max_retries: int = 2
    backoff_s: float = 1.0
    max_backoff_s: float = 60.0
    max_options: int = MAX_CHOICE_OPTIONS
    max_state_chars: int = 60_000
    require_model_prefix: str | None = "drex"

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str],
        env_file: Path | None = DEFAULT_ENV_FILE,
    ) -> DrexConfig:
        merged = dict(load_env_file(env_file)) if env_file else {}
        merged.update({k: v for k, v in environ.items() if k.startswith("DREX_")})
        key = merged.get("DREX_API_KEY", "").strip()
        if not key:
            raise DrexConfigError(
                "DREX_API_KEY is not set (environment or env file "
                f"{env_file}); create a key at https://drex.nace.ai"
            )
        return cls(
            api_key=key,
            base_url=merged.get("DREX_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            model=merged.get("DREX_MODEL", DEFAULT_MODEL),
            timeout_s=float(merged.get("DREX_TIMEOUT_S", 15.0)),
            max_retries=int(merged.get("DREX_MAX_RETRIES", 2)),
            max_backoff_s=float(merged.get("DREX_MAX_BACKOFF_S", 60.0)),
            max_options=int(merged.get("DREX_MAX_OPTIONS", MAX_CHOICE_OPTIONS)),
        )


def _reject_constant(name: str) -> Any:
    raise DrexProtocolError(f"non-finite JSON constant in response: {name}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise DrexProtocolError(f"duplicate key in response JSON: {key!r}")
        out[key] = value
    return out


def parse_choice_response(
    body: bytes | str, question_id: str = QUESTION_ID
) -> ChoiceAnswer:
    """Parse and structurally validate a /v1/systemone Choice response."""
    try:
        doc = json.loads(
            body, object_pairs_hook=_strict_object, parse_constant=_reject_constant
        )
    except DrexProtocolError:
        raise
    except (ValueError, TypeError) as e:
        raise DrexProtocolError(f"response is not JSON: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("answers"), dict):
        raise DrexProtocolError("response has no 'answers' object")
    answer = doc["answers"].get(question_id)
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise DrexProtocolError(f"answer {question_id!r} is missing or not a choice")
    probs = answer.get("probabilities")
    if not isinstance(probs, dict) or not probs:
        raise DrexProtocolError("choice answer has no probabilities")
    choice = answer.get("choice")
    if choice is not None and not isinstance(choice, str):
        raise DrexProtocolError("choice is not a string")
    confidence = answer.get("confidence")
    if confidence is not None and (
        isinstance(confidence, bool) or not isinstance(confidence, (int, float))
    ):
        raise DrexProtocolError("confidence is not a number")
    usage_raw = doc.get("usage")
    usage = None
    if isinstance(usage_raw, dict):
        usage = {
            k: int(v)
            for k, v in usage_raw.items()
            if isinstance(v, (int, float))
            and not isinstance(v, bool)
            and math.isfinite(v)
        }
    model = doc.get("model")
    return ChoiceAnswer(
        probabilities=tuple(probs.items()),
        choice=choice,
        confidence=float(confidence) if confidence is not None else None,
        model=model if isinstance(model, str) else None,
        usage=usage,
    )


def _server_message(response: httpx.Response) -> str:
    try:
        doc = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(doc, dict):
        err = doc.get("error") or doc.get("detail")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        if err is not None:
            return str(err)[:300]
    return str(doc)[:300]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


class DrexClient:
    def __init__(
        self,
        config: DrexConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.timeout_s,
            transport=transport,
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Accept": "application/json",
            },
        )

    def __repr__(self) -> str:
        return f"DrexClient(base_url={self.config.base_url!r}, model={self.config.model!r})"

    async def aclose(self) -> None:
        await self._http.aclose()

    def _check_request(self, state: Any, options: Mapping[str, Any]) -> None:
        if len(options) < 2:
            raise DrexRequestInvalid("a Choice needs at least 2 options")
        if len(options) > self.config.max_options:
            raise DrexRequestInvalid(
                f"{len(options)} options exceed the limit of {self.config.max_options}"
            )
        size = len(json.dumps(state, default=str))
        if size > self.config.max_state_chars:
            raise DrexRequestInvalid(
                f"state is {size} chars, over the {self.config.max_state_chars} budget"
            )

    async def choose(
        self,
        *,
        state: Any,
        instructions: str | Mapping[str, Any],
        options: Mapping[str, Any],
        question_id: str = QUESTION_ID,
    ) -> ChoiceAnswer:
        self._check_request(state, options)
        payload = {
            "model": self.config.model,
            "state": state,
            "questions": {
                question_id: {
                    "type": "choice",
                    "instructions": instructions,
                    "criteria": dict(options),
                }
            },
        }
        started = time.perf_counter()
        try:
            response = await self._http.post("/v1/systemone", json=payload)
        except httpx.TimeoutException as e:
            raise DrexUnavailable(f"timeout after {self.config.timeout_s}s") from e
        except httpx.HTTPError as e:
            raise DrexUnavailable(f"transport error: {type(e).__name__}") from e
        latency_ms = (time.perf_counter() - started) * 1000.0

        status = response.status_code
        if status in (401, 403):
            raise DrexAuthError(f"HTTP {status}: {_server_message(response)}")
        if status in _RETRYABLE_STATUS:
            raise DrexUnavailable(
                f"HTTP {status}: {_server_message(response)}",
                retry_after_s=_retry_after(response),
            )
        if status != 200:
            raise DrexRequestRejected(f"HTTP {status}: {_server_message(response)}")

        answer = parse_choice_response(response.content, question_id)
        prefix = self.config.require_model_prefix
        if prefix and not (answer.model or "").startswith(prefix):
            raise DrexModelMismatch(
                f"response model {answer.model!r} does not start with {prefix!r}"
            )
        request_id = response.headers.get(
            "x-typesafe-request-id"
        ) or response.headers.get("x-request-id")
        return ChoiceAnswer(
            probabilities=answer.probabilities,
            choice=answer.choice,
            confidence=answer.confidence,
            model=answer.model,
            usage=answer.usage,
            request_id=request_id,
            latency_ms=latency_ms,
        )

    async def list_models(self) -> list[dict[str, Any]]:
        try:
            response = await self._http.get("/v1/models")
        except httpx.HTTPError as e:
            raise DrexUnavailable(f"transport error: {type(e).__name__}") from e
        if response.status_code in (401, 403):
            raise DrexAuthError(
                f"HTTP {response.status_code}: {_server_message(response)}"
            )
        if response.status_code != 200:
            raise DrexRequestRejected(
                f"HTTP {response.status_code}: {_server_message(response)}"
            )
        doc = response.json()
        models = doc.get("models") if isinstance(doc, dict) else None
        if not isinstance(models, list):
            raise DrexProtocolError("models response has no 'models' list")
        return models
