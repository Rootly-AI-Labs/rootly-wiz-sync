"""Helpers for bootstrapping a Rootly Generic Webhook alert source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import parse

from .config import RootlyBootstrapConfig
from .http_client import HttpRequestError, http_json

GENERIC_WEBHOOK_SOURCE_TYPE = "generic_webhook"
TITLE_TEMPLATE = "{{ alert.data.title }}"
DESCRIPTION_TEMPLATE = "{{ alert.data.description }}"
DEDUPE_KEY_PATH = "$.dedupe_key"
RESOLVED_JSON_PATH = "$.resolved"


@dataclass
class RootlyBootstrapResult:
    mode: str
    source_id: str | None
    source_name: str
    webhook_url: str | None
    webhook_auth_url: str | None
    webhook_bearer_token: str | None
    payload: dict[str, Any]


def _rootly_api_request(
    cfg: RootlyBootstrapConfig,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return http_json(
            url=f"{cfg.rootly_api_url}{path}",
            method=method,
            payload=payload,
            headers={
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json, application/json;q=0.9, */*;q=0.8",
                "Authorization": f"Bearer {cfg.rootly_api_token}",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36 Rootly-Wiz-Bridge/1.0"
                ),
            },
            timeout_secs=20,
            max_retries=5,
            retry_base_secs=1.0,
            retry_max_secs=30.0,
            retry_on_statuses={429, 500, 502, 503, 504},
            request_label="rootly api request",
        )
    except HttpRequestError as exc:
        body = (exc.body or "").lower()
        if exc.status_code == 403 and "error code: 1010" in body:
            raise RuntimeError(
                "Rootly's Cloudflare edge blocked this bootstrap request based on the client signature "
                "(error 1010). The token may be valid, but api.rootly.com is rejecting the automated "
                "request before it reaches the Rootly API. Try the command again after this update. "
                "If it still fails, use the Rootly UI setup path and contact Rootly support to allow "
                "API access for this client."
            ) from exc
        raise


def _attributes(item: dict[str, Any]) -> dict[str, Any]:
    attrs = item.get("attributes")
    return attrs if isinstance(attrs, dict) else {}


def _item_id(item: dict[str, Any]) -> str | None:
    raw_id = item.get("id")
    if raw_id is None:
        return None
    return str(raw_id)


def list_alert_sources(cfg: RootlyBootstrapConfig, *, name: str | None = None) -> list[dict[str, Any]]:
    payload = _rootly_api_request(cfg, "GET", "/v1/alert_sources")
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    items = [item for item in data if isinstance(item, dict)]
    filtered: list[dict[str, Any]] = []
    for item in items:
        attrs = _attributes(item)
        if str(attrs.get("source_type", "")).strip() != GENERIC_WEBHOOK_SOURCE_TYPE:
            continue
        if name and str(attrs.get("name", "")).strip() != name:
            continue
        filtered.append(item)
    return filtered


def get_alert_source(cfg: RootlyBootstrapConfig, source_id: str) -> dict[str, Any] | None:
    payload = _rootly_api_request(cfg, "GET", f"/v1/alert_sources/{parse.quote(source_id)}")
    data = payload.get("data")
    return data if isinstance(data, dict) else None


def list_alert_urgencies(cfg: RootlyBootstrapConfig) -> list[dict[str, Any]]:
    payload = _rootly_api_request(cfg, "GET", "/v1/alert_urgencies")
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def list_alert_fields(cfg: RootlyBootstrapConfig, *, kind: str | None = None) -> list[dict[str, Any]]:
    query = ""
    if kind:
        query = f"?{parse.urlencode({'filter[kind]': kind})}"
    payload = _rootly_api_request(cfg, "GET", f"/v1/alert_fields{query}")
    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    return []


def resolve_target_alert_source(cfg: RootlyBootstrapConfig) -> dict[str, Any] | None:
    if cfg.source_id:
        return get_alert_source(cfg, cfg.source_id)
    candidates = list_alert_sources(cfg, name=cfg.source_name)
    for item in candidates:
        attrs = _attributes(item)
        if str(attrs.get("name", "")).strip() == cfg.source_name and str(
            attrs.get("source_type", "")
        ).strip() == GENERIC_WEBHOOK_SOURCE_TYPE:
            return item
    return None


def _find_urgency_id(urgencies: list[dict[str, Any]], names: list[str]) -> str | None:
    lowered_names = {name.lower() for name in names}
    for item in urgencies:
        attrs = _attributes(item)
        urgency_name = str(attrs.get("name", "")).strip().lower()
        if urgency_name in lowered_names:
            return _item_id(item)
    return None


def _field_id_by_kind(fields: list[dict[str, Any]], kind: str) -> str | None:
    kind_lower = kind.lower()
    for item in fields:
        attrs = _attributes(item)
        if str(attrs.get("kind", "")).strip().lower() == kind_lower:
            return _item_id(item)
    return None


def build_urgency_rules(urgencies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mappings = [
        ("Critical", ["critical", "sev0", "sev 0", "p0"]),
        ("High", ["high", "sev1", "sev 1", "p1"]),
        ("Medium", ["medium", "med", "sev2", "sev 2", "p2"]),
        ("Low", ["low", "sev3", "sev 3", "p3"]),
    ]
    rules: list[dict[str, Any]] = []
    for payload_value, urgency_names in mappings:
        urgency_id = _find_urgency_id(urgencies, urgency_names)
        if urgency_id:
            rules.append(
                {
                    "json_path": "$.urgency",
                    "operator": "is",
                    "value": payload_value,
                    "alert_urgency_id": urgency_id,
                }
            )
    return rules


def build_alert_source_payload(
    cfg: RootlyBootstrapConfig,
    *,
    urgency_rules: list[dict[str, Any]],
    alert_field_ids: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    alert_field_ids = alert_field_ids or {}
    attributes: dict[str, Any] = {
        "name": cfg.source_name,
        "source_type": GENERIC_WEBHOOK_SOURCE_TYPE,
        "deduplicate_alerts_by_key": True,
        "deduplication_key_kind": "payload",
        "deduplication_key_path": DEDUPE_KEY_PATH,
        "alert_template_attributes": {
            "title": TITLE_TEMPLATE,
            "description": DESCRIPTION_TEMPLATE,
        },
        "sourceable_attributes": {
            "auto_resolve": True,
            "resolve_state": "resolved",
            "field_mappings_attributes": [
                {
                    "field": "external_id",
                    "json_path": DEDUPE_KEY_PATH,
                }
            ],
        },
        "resolution_rule_attributes": {
            "enabled": True,
            "condition_type": "all",
            "identifier_reference_kind": "payload",
            "identifier_json_path": DEDUPE_KEY_PATH,
            "conditions_attributes": [
                {
                    "field": "resolved",
                    "json_path": RESOLVED_JSON_PATH,
                    "operator": "is",
                    "value": "true",
                    "kind": "payload",
                }
            ],
        },
    }
    alert_source_fields_attributes: list[dict[str, str]] = []
    title_field_id = alert_field_ids.get("title")
    if title_field_id:
        alert_source_fields_attributes.append(
            {
                "alert_field_id": title_field_id,
                "template_body": TITLE_TEMPLATE,
            }
        )
    description_field_id = alert_field_ids.get("description")
    if description_field_id:
        alert_source_fields_attributes.append(
            {
                "alert_field_id": description_field_id,
                "template_body": DESCRIPTION_TEMPLATE,
            }
        )
    if alert_source_fields_attributes:
        attributes["alert_source_fields_attributes"] = alert_source_fields_attributes
    external_id_field_id = alert_field_ids.get("external_id")
    if external_id_field_id:
        attributes["resolution_rule_attributes"]["identifier_matchable_type"] = "AlertField"
        attributes["resolution_rule_attributes"]["identifier_matchable_id"] = external_id_field_id
    if urgency_rules:
        attributes["alert_source_urgency_rules_attributes"] = urgency_rules
    if cfg.owner_group_ids:
        attributes["owner_group_ids"] = cfg.owner_group_ids
    return {
        "data": {
            "type": "alert_sources",
            "attributes": attributes,
        }
    }


def derive_webhook_urls(webhook_endpoint: str | None, secret: str | None) -> tuple[str | None, str | None]:
    if not webhook_endpoint:
        return None, None
    base_url = webhook_endpoint
    if "/notify/" in webhook_endpoint:
        base_url = webhook_endpoint.split("/notify/", 1)[0]
    query_url = base_url
    if secret:
        separator = "&" if "?" in base_url else "?"
        query_url = f"{base_url}{separator}secret={parse.quote(secret)}"
    return query_url, base_url


def _result_from_item(
    mode: str,
    item: dict[str, Any] | None,
    payload: dict[str, Any],
) -> RootlyBootstrapResult:
    attrs = _attributes(item or {})
    source_name = str(attrs.get("name", "")).strip() or str(
        payload.get("data", {}).get("attributes", {}).get("name", "")
    ).strip()
    webhook_endpoint = str(attrs.get("webhook_endpoint", "")).strip() or None
    secret = str(attrs.get("secret", "")).strip() or None
    webhook_url, webhook_auth_url = derive_webhook_urls(webhook_endpoint, secret)
    return RootlyBootstrapResult(
        mode=mode,
        source_id=_item_id(item or {}),
        source_name=source_name,
        webhook_url=webhook_url,
        webhook_auth_url=webhook_auth_url,
        webhook_bearer_token=secret,
        payload=payload,
    )


def bootstrap_rootly_alert_source(cfg: RootlyBootstrapConfig) -> RootlyBootstrapResult:
    existing = resolve_target_alert_source(cfg)
    urgencies = list_alert_urgencies(cfg)
    alert_fields = (
        list_alert_fields(cfg, kind="title")
        + list_alert_fields(cfg, kind="description")
        + list_alert_fields(cfg, kind="external_id")
    )
    alert_field_ids = {
        "title": _field_id_by_kind(alert_fields, "title"),
        "description": _field_id_by_kind(alert_fields, "description"),
        "external_id": _field_id_by_kind(alert_fields, "external_id"),
    }
    payload = build_alert_source_payload(
        cfg,
        urgency_rules=build_urgency_rules(urgencies),
        alert_field_ids=alert_field_ids,
    )
    if cfg.dry_run:
        mode = "update" if existing else "create"
        return _result_from_item(mode, existing, payload)

    if existing:
        source_id = _item_id(existing)
        response = _rootly_api_request(
            cfg,
            "PUT",
            f"/v1/alert_sources/{parse.quote(str(source_id))}",
            payload=payload,
        )
        mode = "updated"
    else:
        response = _rootly_api_request(cfg, "POST", "/v1/alert_sources", payload=payload)
        mode = "created"
    data = response.get("data")
    return _result_from_item(mode, data if isinstance(data, dict) else None, payload)


def bootstrap_result_as_text(result: RootlyBootstrapResult) -> str:
    lines = [
        f"Rootly alert source {result.mode}: {result.source_name}",
    ]
    if result.source_id:
        lines.append(f"ROOTLY_ALERT_SOURCE_ID={result.source_id}")
    if result.webhook_url:
        lines.append("")
        lines.append("Query-param webhook option:")
        lines.append(f"ROOTLY_WEBHOOK_URL={result.webhook_url}")
    if result.webhook_auth_url and result.webhook_bearer_token:
        lines.append("")
        lines.append("Authorization header option:")
        lines.append(f"ROOTLY_WEBHOOK_URL={result.webhook_auth_url}")
        lines.append("ROOTLY_WEBHOOK_AUTH_HEADER=Authorization")
        lines.append(f"ROOTLY_WEBHOOK_BEARER_TOKEN={result.webhook_bearer_token}")
    lines.append("")
    lines.append("Bootstrap payload:")
    lines.append(json.dumps(result.payload, indent=2, sort_keys=True))
    return "\n".join(lines)
