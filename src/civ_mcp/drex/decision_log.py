"""Append-only JSONL decision log with credential redaction."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from civ_mcp.drex.serialize import to_jsonable

_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "drex_api_key", "x-api-key"}
_MIN_SECRET_LEN = 8


def redact(obj: Any, secrets: list[str]) -> Any:
    usable = [s for s in secrets if s and len(s) >= _MIN_SECRET_LEN]
    return _redact(obj, usable)


def _redact(obj: Any, secrets: list[str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: _redact(v, secrets)
            for k, v in obj.items()
            if str(k).lower() not in _SENSITIVE_KEYS
        }
    if isinstance(obj, (list, tuple)):
        return [_redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        for s in secrets:
            obj = obj.replace(s, "[REDACTED]")
        return obj
    return obj


class DecisionLog:
    def __init__(self, path: Path, *, run_id: str, secrets: list[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._secrets = list(secrets)
        self._seq = 0

    def add_secret(self, value: str) -> None:
        """Redact another value (e.g. a rotated API key) from later records."""
        if value and value not in self._secrets:
            self._secrets.append(value)

    def write(self, record_type: str, payload: dict[str, Any]) -> None:
        self._seq += 1
        record = {
            "type": record_type,
            "run_id": self.run_id,
            "seq": self._seq,
            "ts": round(time.time(), 3),
            **to_jsonable(payload),
        }
        line = json.dumps(redact(record, self._secrets), default=str)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
