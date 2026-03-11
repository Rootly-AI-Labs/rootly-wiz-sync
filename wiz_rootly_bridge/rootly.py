"""Rootly payload mapping and webhook posting."""

from __future__ import annotations

from typing import Any

from .config import Config
from .http_client import http_json
from .state import is_resolved_status, item_status
from .utils import now_iso


def to_rootly_payload(cfg: Config, item: dict[str, Any], event_id: str) -> dict[str, Any]:
    source_rule = item.get("sourceRule") or {}
    source_rules = item.get("sourceRules") or []
    first_source_rule = source_rule if isinstance(source_rule, dict) else {}
    if not first_source_rule and isinstance(source_rules, list):
        for candidate in source_rules:
            if isinstance(candidate, dict):
                first_source_rule = candidate
                break
    control = item.get("control") or {}
    if not control and isinstance(first_source_rule, dict):
        nested_control = first_source_rule.get("control")
        if isinstance(nested_control, dict):
            control = nested_control
    entity = item.get("entitySnapshot") or {}
    projects = item.get("projects") or []
    project_names: list[str] = []
    for project in projects:
        if isinstance(project, dict):
            name = project.get("name")
            if name:
                project_names.append(str(name))
    if isinstance(item.get("project"), dict):
        single_project_name = item["project"].get("name")
        if single_project_name:
            project_names.append(str(single_project_name))
    project_names = list(dict.fromkeys(project_names))

    base_title = (
        item.get("title")
        or (first_source_rule.get("name") if isinstance(first_source_rule, dict) else None)
        or (control.get("name") if isinstance(control, dict) else None)
        or item.get("name")
        or "Wiz security alert"
    )
    entity_name = entity.get("name") if isinstance(entity, dict) else None
    issue_id = item.get("id")
    title = str(base_title)
    if entity_name:
        title = f"{title} ({entity_name})"
    if issue_id:
        title = f"{title} [{str(issue_id)[:8]}]"
    item_type = item.get("type") or "ISSUE"
    severity = item.get("severity") or "unknown"
    created_at = item.get("createdAt") or now_iso()
    updated_at = item.get("updatedAt") or created_at
    resolved_at = item.get("resolvedAt") or updated_at
    status = item_status(item)
    resolved = is_resolved_status(cfg, status)
    rule_name = first_source_rule.get("name") if isinstance(first_source_rule, dict) else None
    if not rule_name and isinstance(control, dict):
        rule_name = control.get("name")
    if resolved:
        summary = f"Wiz marked a {item_type} item as {status}."
    else:
        summary = f"Wiz reported a {item_type} item with {severity} severity."
    urgency = str(severity).capitalize()

    return {
        "source": "wiz",
        "event_type": "wiz.security.alert",
        "event_action": "resolved" if resolved else "opened",
        "event_id": event_id,
        "dedupe_key": event_id,
        "timestamp": now_iso(),
        "status": str(status),
        "resolved": resolved,
        "created_at": str(created_at),
        "updated_at": str(updated_at),
        "resolved_at": str(resolved_at) if resolved else None,
        # Top-level fields help Rootly Generic Webhook map alert metadata directly.
        "title": str(title),
        "description": summary,
        "urgency": urgency,
        "alert": {
            "title": str(title),
            "summary": summary,
            "severity": str(severity),
            "status": str(status),
            "detected_at": str(created_at),
            "updated_at": str(updated_at),
            "resolved": resolved,
            "resolved_at": str(resolved_at) if resolved else None,
            "dedupe_key": event_id,
            "category": str(item_type),
            "rule_name": str(rule_name) if rule_name else None,
            "resource": {
                "id": entity.get("id") if isinstance(entity, dict) else None,
                "name": entity.get("name") if isinstance(entity, dict) else None,
                "type": entity.get("type") if isinstance(entity, dict) else None,
            },
            "projects": project_names,
        },
        "raw": item,
    }


def post_to_rootly(cfg: Config, payload: dict[str, Any]) -> None:
    headers = {"Content-Type": "application/json"}
    if cfg.rootly_auth_header and cfg.rootly_auth_value:
        headers[cfg.rootly_auth_header] = cfg.rootly_auth_value
    _ = http_json(
        url=cfg.rootly_webhook_url,
        method="POST",
        payload=payload,
        headers=headers,
        timeout_secs=cfg.request_timeout_secs,
        max_retries=cfg.rootly_max_retries,
        retry_base_secs=cfg.rootly_retry_base_secs,
        retry_max_secs=cfg.rootly_retry_max_secs,
        retry_on_statuses={429, 500, 502, 503, 504},
        throttle_per_sec=cfg.rootly_max_rps,
        throttle_key="rootly",
        request_label="rootly webhook request",
    )
