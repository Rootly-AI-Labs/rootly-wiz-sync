"""Configuration loading from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_API_URL,
    DEFAULT_AUTH_URL,
    DEFAULT_POLL_INTERVAL_SECS,
    DEFAULT_QUERY_ISSUES,
    DEFAULT_QUERY_ISSUES_V2,
    DEFAULT_WIZ_MAX_RPS,
)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


def env_json_dict(name: str) -> dict[str, Any] | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object.")
    return parsed


@dataclass
class Config:
    wiz_client_id: str
    wiz_client_secret: str
    wiz_auth_url: str
    wiz_api_url: str
    wiz_page_size: int
    wiz_max_pages: int
    wiz_max_rps: int
    wiz_max_retries: int
    wiz_retry_base_secs: float
    wiz_retry_max_secs: float
    wiz_token_refresh_retries: int
    request_timeout_secs: int
    poll_interval_secs: int
    state_file: Path
    rootly_webhook_url: str
    rootly_auth_header: str | None
    rootly_auth_value: str | None
    match_keywords: list[str]
    severity_filter: set[str] | None
    dry_run: bool
    wiz_filter_by: dict[str, Any] | None
    wiz_order_by: dict[str, Any] | None
    query_candidates: list[str]

    @staticmethod
    def from_env(dry_run: bool) -> "Config":
        wiz_client_id = os.getenv("WIZ_CLIENT_ID", "").strip()
        wiz_client_secret = os.getenv("WIZ_CLIENT_SECRET", "").strip()
        if not wiz_client_id or not wiz_client_secret:
            raise ValueError("Set WIZ_CLIENT_ID and WIZ_CLIENT_SECRET.")

        rootly_webhook_url = os.getenv("ROOTLY_WEBHOOK_URL", "").strip()
        if not rootly_webhook_url and not dry_run:
            raise ValueError("Set ROOTLY_WEBHOOK_URL or run with --dry-run.")

        auth_header = os.getenv("ROOTLY_WEBHOOK_AUTH_HEADER")
        auth_value = os.getenv("ROOTLY_WEBHOOK_AUTH_VALUE")
        bearer = os.getenv("ROOTLY_WEBHOOK_BEARER_TOKEN")
        if bearer:
            auth_header = auth_header or "Authorization"
            auth_value = f"Bearer {bearer}"

        custom_query = os.getenv("WIZ_GRAPHQL_QUERY", "").strip()
        query_file = os.getenv("WIZ_GRAPHQL_QUERY_FILE", "").strip()
        query_candidates: list[str] = []
        if custom_query:
            query_candidates.append(custom_query)
        if query_file:
            query_path = Path(query_file).expanduser()
            query_candidates.append(query_path.read_text(encoding="utf-8"))
        if not query_candidates:
            # Use WIN quickstart shape first, then fallback to issuesV2.
            query_candidates = [DEFAULT_QUERY_ISSUES, DEFAULT_QUERY_ISSUES_V2]

        keywords_raw = os.getenv("WIZ_MATCH_KEYWORDS", "vulnerability,threat,cve,detection")
        match_keywords = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()]
        if not match_keywords:
            raise ValueError("WIZ_MATCH_KEYWORDS must contain at least one keyword.")

        severity_raw = os.getenv("WIZ_ONLY_SEVERITIES", "").strip()
        severity_filter = None
        if severity_raw:
            severity_filter = {s.strip().lower() for s in severity_raw.split(",") if s.strip()}

        wiz_filter_by = env_json_dict("WIZ_FILTER_BY_JSON")
        if wiz_filter_by is None:
            # Default to OPEN issues so first run does not backfill resolved history.
            wiz_filter_by = {"status": ["OPEN"]}

        return Config(
            wiz_client_id=wiz_client_id,
            wiz_client_secret=wiz_client_secret,
            wiz_auth_url=os.getenv("WIZ_AUTH_URL", DEFAULT_AUTH_URL).strip(),
            wiz_api_url=os.getenv("WIZ_API_URL", DEFAULT_API_URL).strip(),
            wiz_page_size=env_int("WIZ_PAGE_SIZE", 50),
            wiz_max_pages=env_int("WIZ_MAX_PAGES", 5),
            wiz_max_rps=env_int("WIZ_MAX_RPS", DEFAULT_WIZ_MAX_RPS),
            wiz_max_retries=env_int("WIZ_MAX_RETRIES", 5),
            wiz_retry_base_secs=float(os.getenv("WIZ_RETRY_BASE_SECS", "1.0")),
            wiz_retry_max_secs=float(os.getenv("WIZ_RETRY_MAX_SECS", "30.0")),
            wiz_token_refresh_retries=env_int("WIZ_TOKEN_REFRESH_RETRIES", 5),
            request_timeout_secs=env_int("REQUEST_TIMEOUT_SECS", 20),
            poll_interval_secs=env_int("POLL_INTERVAL_SECS", DEFAULT_POLL_INTERVAL_SECS),
            state_file=Path(os.getenv("WIZ_STATE_FILE", ".wiz_rootly_seen_ids.json")),
            rootly_webhook_url=rootly_webhook_url,
            rootly_auth_header=auth_header.strip() if auth_header else None,
            rootly_auth_value=auth_value.strip() if auth_value else None,
            match_keywords=match_keywords,
            severity_filter=severity_filter,
            dry_run=dry_run,
            wiz_filter_by=wiz_filter_by,
            wiz_order_by=env_json_dict("WIZ_ORDER_BY_JSON"),
            query_candidates=query_candidates,
        )

