"""Local state and item filtering helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import Config


def fingerprint(item: dict[str, Any]) -> str:
    raw_id = item.get("id")
    if isinstance(raw_id, str) and raw_id.strip():
        return raw_id.strip()
    canonical = json.dumps(item, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stringify(item: Any) -> str:
    if item is None:
        return ""
    if isinstance(item, str):
        return item
    if isinstance(item, (int, float, bool)):
        return str(item)
    if isinstance(item, dict):
        return " ".join(stringify(v) for v in item.values())
    if isinstance(item, list):
        return " ".join(stringify(v) for v in item)
    return str(item)


def should_forward(cfg: Config, item: dict[str, Any]) -> bool:
    text = stringify(item).lower()
    if not any(keyword in text for keyword in cfg.match_keywords):
        return False
    if cfg.severity_filter is None:
        return True
    severity = str(item.get("severity", "")).strip().lower()
    return severity in cfg.severity_filter


def load_seen_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(v) for v in data}


def save_seen_ids(path: Path, seen_ids: set[str], max_items: int = 5000) -> None:
    trimmed = sorted(seen_ids)[-max_items:]
    path.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")

