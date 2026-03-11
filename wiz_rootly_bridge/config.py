"""Configuration loading from environment variables."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import (
    DEFAULT_ACTIVE_STATUSES,
    DEFAULT_API_URL,
    DEFAULT_AUTH_URL,
    DEFAULT_POLL_INTERVAL_SECS,
    DEFAULT_QUERY_ISSUES,
    DEFAULT_QUERY_ISSUES_COMPAT,
    DEFAULT_QUERY_ISSUES_V2,
    DEFAULT_QUERY_ISSUES_V2_COMPAT,
    DEFAULT_RESOLVED_STATUSES,
    DEFAULT_WIZ_MAX_RPS,
    DEFAULT_WIZ_ORDER_BY,
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


def env_csv(name: str) -> list[str]:
    value = os.getenv(name, "").strip()
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def load_env_file(path: Path, *, override: bool = False) -> bool:
    """Load a simple KEY=VALUE env file without requiring python-dotenv."""

    if not path.exists():
        return False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or name not in os.environ:
            os.environ[name] = value
    return True


def load_last_successful_run_at(state_file: Path) -> str | None:
    if not state_file.exists():
        return None
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("last_successful_run_at")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def ordered_severity_values(severity_filter: set[str]) -> list[str]:
    normalized = {value.strip().upper() for value in severity_filter if value.strip()}
    if not normalized:
        return []
    order = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFORMATIONAL")
    values = [value for value in order if value in normalized]
    for value in sorted(normalized):
        if value not in values:
            values.append(value)
    return values


def default_wiz_filter_by(
    state_file: Path,
    resolved_statuses: set[str],
    severity_filter: set[str] | None = None,
) -> dict[str, Any]:
    last_successful_run_at = load_last_successful_run_at(state_file)
    if last_successful_run_at:
        filter_by: dict[str, Any] = {
            "statusChangedAt": {
                "after": last_successful_run_at,
            }
        }
    else:
        filter_by = {}
        statuses = list(DEFAULT_ACTIVE_STATUSES)
        if state_file.exists():
            for status in sorted(resolved_statuses):
                normalized = status.strip().upper()
                if normalized and normalized not in statuses:
                    statuses.append(normalized)
        filter_by["status"] = statuses
    if severity_filter:
        ordered_values = ordered_severity_values(severity_filter)
        if ordered_values:
            filter_by["severity"] = ordered_values
    return filter_by


def effective_wiz_filter_by(
    state_file: Path,
    resolved_statuses: set[str],
    severity_filter: set[str] | None,
    custom_filter_by: dict[str, Any] | None,
) -> dict[str, Any]:
    if custom_filter_by is None:
        return default_wiz_filter_by(state_file, resolved_statuses, severity_filter)

    effective_filter = dict(custom_filter_by)
    last_successful_run_at = load_last_successful_run_at(state_file)
    if last_successful_run_at and "statusChangedAt" not in effective_filter:
        effective_filter["statusChangedAt"] = {"after": last_successful_run_at}
    if severity_filter and "severity" not in effective_filter:
        ordered_values = ordered_severity_values(severity_filter)
        if ordered_values:
            effective_filter["severity"] = ordered_values
    return effective_filter


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
    rootly_max_rps: int
    rootly_max_retries: int
    rootly_retry_base_secs: float
    rootly_retry_max_secs: float
    match_keywords: list[str] | None
    severity_filter: set[str] | None
    resolved_statuses: set[str]
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
            # Prefer the WIN-documented issuesV2 shapes first, then fall back to legacy issues queries.
            query_candidates = [
                DEFAULT_QUERY_ISSUES_V2,
                DEFAULT_QUERY_ISSUES_V2_COMPAT,
                DEFAULT_QUERY_ISSUES,
                DEFAULT_QUERY_ISSUES_COMPAT,
            ]

        keywords_raw = os.getenv("WIZ_MATCH_KEYWORDS", "").strip()
        match_keywords = None
        if keywords_raw:
            match_keywords = [k.strip().lower() for k in keywords_raw.split(",") if k.strip()]
            if not match_keywords:
                raise ValueError("WIZ_MATCH_KEYWORDS must contain at least one keyword.")

        severity_raw = os.getenv("WIZ_ONLY_SEVERITIES", "").strip()
        severity_filter = None
        if severity_raw:
            severity_filter = {s.strip().lower() for s in severity_raw.split(",") if s.strip()}

        state_file = Path(os.getenv("WIZ_STATE_FILE", ".wiz_rootly_seen_ids.json"))
        wiz_filter_by = env_json_dict("WIZ_FILTER_BY_JSON")
        resolved_statuses_raw = os.getenv("WIZ_RESOLVED_STATUSES", "").strip()
        resolved_statuses = DEFAULT_RESOLVED_STATUSES
        if resolved_statuses_raw:
            resolved_statuses = {
                status.strip().lower() for status in resolved_statuses_raw.split(",") if status.strip()
            }
            if not resolved_statuses:
                raise ValueError("WIZ_RESOLVED_STATUSES must contain at least one status.")

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
            state_file=state_file,
            rootly_webhook_url=rootly_webhook_url,
            rootly_auth_header=auth_header.strip() if auth_header else None,
            rootly_auth_value=auth_value.strip() if auth_value else None,
            rootly_max_rps=env_int("ROOTLY_MAX_RPS", 1),
            rootly_max_retries=env_int("ROOTLY_MAX_RETRIES", 5),
            rootly_retry_base_secs=float(os.getenv("ROOTLY_RETRY_BASE_SECS", "1.0")),
            rootly_retry_max_secs=float(os.getenv("ROOTLY_RETRY_MAX_SECS", "30.0")),
            match_keywords=match_keywords,
            severity_filter=severity_filter,
            resolved_statuses=resolved_statuses,
            dry_run=dry_run,
            wiz_filter_by=wiz_filter_by,
            wiz_order_by=env_json_dict("WIZ_ORDER_BY_JSON") or DEFAULT_WIZ_ORDER_BY,
            query_candidates=query_candidates,
        )


@dataclass
class RootlyBootstrapConfig:
    rootly_api_token: str
    rootly_api_url: str
    source_name: str
    source_id: str | None
    owner_group_ids: list[str]
    dry_run: bool

    @staticmethod
    def from_env(*, dry_run: bool, source_name: str | None = None, source_id: str | None = None) -> "RootlyBootstrapConfig":
        rootly_api_token = os.getenv("ROOTLY_API_TOKEN", "").strip()
        if not rootly_api_token:
            raise ValueError("Set ROOTLY_API_TOKEN to bootstrap the Rootly alert source.")
        resolved_source_name = (source_name or os.getenv("ROOTLY_ALERT_SOURCE_NAME", "")).strip()
        if not resolved_source_name:
            resolved_source_name = "Wiz Security Alerts"
        resolved_source_id = (source_id or os.getenv("ROOTLY_ALERT_SOURCE_ID", "")).strip() or None
        return RootlyBootstrapConfig(
            rootly_api_token=rootly_api_token,
            rootly_api_url=os.getenv("ROOTLY_API_URL", "https://api.rootly.com").strip().rstrip("/"),
            source_name=resolved_source_name,
            source_id=resolved_source_id,
            owner_group_ids=env_csv("ROOTLY_OWNER_GROUP_IDS"),
            dry_run=dry_run,
        )
