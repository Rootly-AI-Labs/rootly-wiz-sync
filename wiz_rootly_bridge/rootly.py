"""Rootly payload mapping and webhook posting."""

from __future__ import annotations

from typing import Any

from .config import Config
from .http_client import http_json
from .utils import now_iso


def to_rootly_payload(item: dict[str, Any], event_id: str) -> dict[str, Any]:
    source_rule = item.get("sourceRule") or {}
    control = item.get("control") or {}
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
    status = item.get("status") or "OPEN"
    rule_name = source_rule.get("name") if isinstance(source_rule, dict) else None
    if not rule_name and isinstance(control, dict):
        rule_name = control.get("name")
    summary = f"Wiz reported a {item_type} item with {severity} severity."
    urgency = str(severity).capitalize()

    return {
        "source": "wiz",
        "event_type": "wiz.security.alert",
        "event_id": event_id,
        "timestamp": now_iso(),
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
    )
