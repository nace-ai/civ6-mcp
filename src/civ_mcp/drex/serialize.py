"""Lossless JSON conversion for observation dataclasses (offline replay)."""

from __future__ import annotations

import dataclasses
import enum
import types
import typing
from functools import cache
from typing import Any, Union, get_args, get_origin


def to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {
            f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)
        }
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_jsonable(v) for v in obj)
    return obj


@cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls)


def from_jsonable(tp: Any, data: Any) -> Any:
    if data is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, types.UnionType):
        errors = []
        for arg in get_args(tp):
            if arg is type(None):
                continue
            try:
                return from_jsonable(arg, data)
            except (TypeError, ValueError, KeyError) as e:
                errors.append(e)
        raise ValueError(f"no union member of {tp} accepts {data!r}: {errors}")
    if dataclasses.is_dataclass(tp):
        if not isinstance(data, dict):
            raise TypeError(
                f"{tp.__name__} expects an object, got {type(data).__name__}"
            )
        hints = _hints(tp)
        return tp(
            **{
                f.name: from_jsonable(hints[f.name], data[f.name])
                for f in dataclasses.fields(tp)
                if f.name in data
            }
        )
    if origin is list:
        (arg,) = get_args(tp) or (Any,)
        return [from_jsonable(arg, v) for v in data]
    if origin in (set, frozenset):
        (arg,) = get_args(tp) or (Any,)
        return {from_jsonable(arg, v) for v in data}
    if origin is tuple:
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(from_jsonable(args[0], v) for v in data)
        return tuple(from_jsonable(a, v) for a, v in zip(args, data, strict=True))
    if origin is dict:
        key_t, val_t = get_args(tp) or (Any, Any)
        return {
            from_jsonable(key_t, k): from_jsonable(val_t, v) for k, v in data.items()
        }
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return tp(data)
    if tp is int and isinstance(data, str):
        return int(data)
    if tp is float and isinstance(data, int) and not isinstance(data, bool):
        return float(data)
    if tp in (int, float, str, bool) and not isinstance(data, tp):
        raise TypeError(f"expected {tp.__name__}, got {type(data).__name__}")
    return data
