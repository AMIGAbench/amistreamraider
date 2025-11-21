"""HTTP helper utilities shared across endpoints."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Tuple

from fastapi.responses import JSONResponse, PlainTextResponse, Response


KV_MEDIA_TYPE = "text/plain; charset=iso-8859-1"


def _flatten_kv(prefix: str, value: Any, collector: List[Tuple[str, Any]]) -> None:
    """Flatten nested payloads into dotted key paths."""
    if isinstance(value, dict):
        for key, nested in value.items():
            new_prefix = f"{prefix}.{key}" if prefix else key
            _flatten_kv(new_prefix, nested, collector)
        return

    if isinstance(value, list):
        base_key = prefix
        if base_key.endswith("s"):
            base_key = base_key[:-1]
        for idx, item in enumerate(value):
            new_prefix = f"{base_key}{idx}" if base_key else str(idx)
            _flatten_kv(new_prefix, item, collector)
        if not value and base_key:
            collector.append((f"{base_key}.COUNT", 0))
        return

    collector.append((prefix, value))


def _coerce_value(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    return "" if value is None else value


def _to_kv_lines(payload: Dict[str, Any]) -> Iterable[str]:
    """Convert a mapping into KEY=VALUE lines suitable for the Amiga parser."""
    flattened: List[Tuple[str, Any]] = []
    for key, value in payload.items():
        _flatten_kv(key, value, flattened)
    for key, value in flattened:
        key_fmt = key.replace(".", ".").upper()
        yield f"{key_fmt}={_coerce_value(value)}"


def _latin1_safe(text: str) -> str:
    """Coerce UTF-8 strings to ISO-8859-1 compatible output."""
    return text.encode("latin-1", "replace").decode("latin-1")


def format_response(
    payload: Dict[str, Any], format_hint: str, status_code: int = 200
) -> Response:
    """Return either JSON or KV formatted HTTP response objects."""
    if format_hint.lower() == "kv":
        lines = "\n".join(_to_kv_lines(payload))
        body = _latin1_safe(lines)
        return PlainTextResponse(
            body,
            media_type="text/plain; charset=iso-8859-1",
            status_code=status_code,
        )
    return JSONResponse(payload, status_code=status_code)
