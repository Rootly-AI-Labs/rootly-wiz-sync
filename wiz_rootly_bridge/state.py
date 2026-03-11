"""Local state and item filtering helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Config
from .utils import now_iso

StateRecord = dict[str, str]
StateMetadata = dict[str, str]


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
    if cfg.severity_filter is None:
        severity_matches = True
    else:
        severity = str(item.get("severity", "")).strip().lower()
        severity_matches = severity in cfg.severity_filter
    if not severity_matches:
        return False
    if cfg.match_keywords is None:
        return True
    text = stringify(item).lower()
    return any(keyword in text for keyword in cfg.match_keywords)


def item_status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "OPEN").strip().upper()


def item_updated_at(item: dict[str, Any]) -> str:
    return str(item.get("updatedAt") or item.get("createdAt") or "").strip()


def is_resolved_status(cfg: Config, status: str) -> bool:
    return status.strip().lower() in cfg.resolved_statuses


def timestamp_sort_value(value: str) -> tuple[int, int]:
    text = value.strip()
    if not text:
        return (0, 0)
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return (0, 0)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_value = parsed.astimezone(timezone.utc)
    return (1, int(utc_value.timestamp() * 1_000_000))


def should_forward_event(cfg: Config, item: dict[str, Any], previous: StateRecord | None) -> bool:
    status = item_status(item)
    resolved = is_resolved_status(cfg, status)
    updated_at = item_updated_at(item)
    if previous is None:
        # Avoid creating already-resolved alerts during initial backfill.
        return not resolved
    previous_status = str(previous.get("status", "")).strip().upper()
    if previous_status != status:
        return True
    previous_updated_at = str(previous.get("updated_at", "")).strip()
    if not resolved and previous_updated_at and updated_at and previous_updated_at != updated_at:
        return True
    return False


def load_state_data(path: Path) -> tuple[StateMetadata, dict[str, StateRecord]]:
    if not path.exists():
        return {}, {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, {}
    if isinstance(data, list):
        migrated_at = now_iso()
        return {}, {
            str(value): {
                "status": "OPEN",
                "updated_at": "",
                "last_seen_at": migrated_at,
            }
            for value in data
        }
    if not isinstance(data, dict):
        return {}, {}
    metadata: StateMetadata = {}
    raw_metadata = data.get("metadata")
    if isinstance(raw_metadata, dict):
        for field in ("last_successful_run_at", "last_completed_run_at"):
            field_value = raw_metadata.get(field)
            if isinstance(field_value, str) and field_value.strip():
                metadata[field] = field_value.strip()
    items = data["items"] if "items" in data else data
    if not isinstance(items, dict):
        return metadata, {}
    state: dict[str, StateRecord] = {}
    for key, value in items.items():
        if not isinstance(value, dict):
            continue
        record: StateRecord = {}
        for field in ("status", "updated_at", "last_seen_at", "last_forwarded_at"):
            field_value = value.get(field)
            if isinstance(field_value, str) and field_value.strip():
                record[field] = field_value.strip()
        if "last_seen_at" not in record:
            fallback_timestamp = record.get("last_forwarded_at") or record.get("updated_at")
            if fallback_timestamp:
                record["last_seen_at"] = fallback_timestamp
        state[str(key)] = record
    return metadata, state


def load_state(path: Path) -> dict[str, StateRecord]:
    _, state = load_state_data(path)
    return state


def load_state_metadata(path: Path) -> StateMetadata:
    metadata, _ = load_state_data(path)
    return metadata


def update_state_record(
    current: StateRecord | None,
    item: dict[str, Any],
    *,
    was_forwarded: bool,
) -> StateRecord:
    record = dict(current or {})
    timestamp = now_iso()
    record["status"] = item_status(item)
    updated_at = item_updated_at(item)
    if updated_at:
        record["updated_at"] = updated_at
    record["last_seen_at"] = timestamp
    if was_forwarded:
        record["last_forwarded_at"] = timestamp
    return record


def save_state(
    path: Path,
    state: dict[str, StateRecord],
    *,
    metadata: StateMetadata | None = None,
    max_items: int = 5000,
) -> None:
    def sort_key(indexed_item: tuple[int, tuple[str, StateRecord]]) -> tuple[tuple[int, int], ...]:
        index, (_, record) = indexed_item
        return (
            timestamp_sort_value(record.get("last_seen_at", "")),
            timestamp_sort_value(record.get("last_forwarded_at", "")),
            timestamp_sort_value(record.get("updated_at", "")),
            (1, index),
        )

    trimmed_items = sorted(enumerate(state.items()), key=sort_key)[-max_items:]
    payload = {
        "version": 3,
        "items": {key: value for _, (key, value) in trimmed_items},
    }
    metadata = metadata or {}
    persisted_metadata = {
        key: value.strip()
        for key, value in metadata.items()
        if isinstance(value, str) and value.strip()
    }
    if persisted_metadata:
        payload["metadata"] = persisted_metadata
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
