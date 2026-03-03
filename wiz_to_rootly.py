#!/usr/bin/env python3
"""Poll Wiz GraphQL and forward vulnerability/threat items to a Rootly webhook."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, parse, request


DEFAULT_AUTH_URL = "https://auth.app.wiz.io/oauth/token"
DEFAULT_API_URL = "https://api.us17.app.wiz.io/graphql"
DEFAULT_WIZ_MAX_RPS = 3
DEFAULT_POLL_INTERVAL_SECS = 86400
DEFAULT_QUERY_ISSUES_V2 = """
query PullIssuesV2($first: Int!, $after: String, $filterBy: IssueFilters, $orderBy: IssueOrder) {
  issuesV2(first: $first, after: $after, filterBy: $filterBy, orderBy: $orderBy) {
    nodes {
      id
      type
      severity
      createdAt
      updatedAt
      status
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()

DEFAULT_QUERY_ISSUES = """
query IssuesTable($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
  issues(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
    nodes {
      id
      control {
        id
        name
      }
      createdAt
      updatedAt
      dueAt
      project {
        id
        name
        slug
        businessUnit
        riskProfile {
          businessImpact
        }
      }
      status
      severity
      entitySnapshot {
        id
        type
        name
        status
        cloudPlatform
        region
      }
      note
      serviceTickets {
        externalId
        name
        url
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
""".strip()


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_LAST_WIZ_CALL_MONO = 0.0


def throttle_wiz_requests(max_rps: int) -> None:
    global _LAST_WIZ_CALL_MONO
    if max_rps <= 0:
        return
    min_interval = 1.0 / float(max_rps)
    now = time.monotonic()
    wait = min_interval - (now - _LAST_WIZ_CALL_MONO)
    if wait > 0:
        time.sleep(wait)
    _LAST_WIZ_CALL_MONO = time.monotonic()


def retry_after_seconds(header_value: str | None) -> float | None:
    if not header_value:
        return None
    try:
        return float(header_value.strip())
    except ValueError:
        return None


class HttpRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def http_json(
    url: str,
    method: str,
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    timeout_secs: int,
    *,
    max_retries: int = 0,
    retry_base_secs: float = 1.0,
    retry_max_secs: float = 30.0,
    retry_on_statuses: set[int] | None = None,
    throttle_per_sec: int = 0,
    request_label: str = "request",
) -> dict[str, Any]:
    retry_on_statuses = retry_on_statuses or set()
    attempt = 0
    while True:
        if throttle_per_sec > 0:
            throttle_wiz_requests(throttle_per_sec)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
        req = request.Request(url=url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with request.urlopen(req, timeout=timeout_secs) as response:
                raw = response.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Non-JSON response from {url}: {raw[:500]}") from exc
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            should_retry = exc.code in retry_on_statuses and attempt < max_retries
            if should_retry:
                explicit_retry_after = retry_after_seconds(exc.headers.get("Retry-After"))
                computed_backoff = min(retry_max_secs, retry_base_secs * (2 ** attempt))
                delay = explicit_retry_after if explicit_retry_after is not None else computed_backoff
                attempt += 1
                print(
                    f"[{now_iso()}] {request_label} throttled/error {exc.code}, "
                    f"retrying in {delay:.1f}s (attempt {attempt}/{max_retries})"
                )
                time.sleep(delay)
                continue
            raise HttpRequestError(
                f"HTTP {exc.code} on {url}: {raw}",
                status_code=exc.code,
                body=raw,
            ) from exc
        except error.URLError as exc:
            if attempt < max_retries:
                delay = min(retry_max_secs, retry_base_secs * (2 ** attempt))
                attempt += 1
                print(
                    f"[{now_iso()}] {request_label} network error, retrying in {delay:.1f}s "
                    f"(attempt {attempt}/{max_retries}): {exc}"
                )
                time.sleep(delay)
                continue
            raise HttpRequestError(f"Request failed for {url}: {exc}") from exc


def fetch_wiz_token(cfg: Config) -> str:
    form_data = parse.urlencode(
        {
            "grant_type": "client_credentials",
            "audience": "wiz-api",
            "client_id": cfg.wiz_client_id,
            "client_secret": cfg.wiz_client_secret,
        }
    ).encode("utf-8")
    attempt = 0
    while True:
        throttle_wiz_requests(cfg.wiz_max_rps)
        req = request.Request(
            cfg.wiz_auth_url,
            data=form_data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with request.urlopen(req, timeout=cfg.request_timeout_secs) as response:
                raw = response.read().decode("utf-8")
            break
        except error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            should_retry = exc.code in {429, 500, 502, 503, 504} and attempt < cfg.wiz_max_retries
            if should_retry:
                explicit_retry_after = retry_after_seconds(exc.headers.get("Retry-After"))
                computed_backoff = min(
                    cfg.wiz_retry_max_secs,
                    cfg.wiz_retry_base_secs * (2 ** attempt),
                )
                delay = explicit_retry_after if explicit_retry_after is not None else computed_backoff
                attempt += 1
                print(
                    f"[{now_iso()}] wiz token request retry in {delay:.1f}s "
                    f"(attempt {attempt}/{cfg.wiz_max_retries}) after HTTP {exc.code}"
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"Token request failed (HTTP {exc.code}): {raw}") from exc
        except error.URLError as exc:
            if attempt < cfg.wiz_max_retries:
                delay = min(cfg.wiz_retry_max_secs, cfg.wiz_retry_base_secs * (2 ** attempt))
                attempt += 1
                print(
                    f"[{now_iso()}] wiz token network retry in {delay:.1f}s "
                    f"(attempt {attempt}/{cfg.wiz_max_retries}): {exc}"
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"Token request failed: {exc}") from exc
    token_payload = json.loads(raw)
    token = token_payload.get("access_token")
    if not token:
        raise RuntimeError(f"Token response missing access_token: {raw}")
    return str(token)


def extract_connection(data: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(value.get("nodes"), list):
            return key, value
    keys = ", ".join(sorted(data.keys()))
    raise RuntimeError(f"No GraphQL connection with nodes found. Data keys: {keys}")


def run_wiz_query(
    cfg: Config,
    token: str,
    query_text: str,
    variables: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    payload = {"query": query_text, "variables": variables}
    try:
        response = http_json(
            url=cfg.wiz_api_url,
            method="POST",
            payload=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout_secs=cfg.request_timeout_secs,
            max_retries=cfg.wiz_max_retries,
            retry_base_secs=cfg.wiz_retry_base_secs,
            retry_max_secs=cfg.wiz_retry_max_secs,
            retry_on_statuses={429, 500, 502, 503, 504},
            throttle_per_sec=cfg.wiz_max_rps,
            request_label="wiz graphql request",
        )
    except HttpRequestError as exc:
        if exc.status_code == 401:
            return (
                "",
                {
                    "errors": [
                        {
                            "message": str(exc),
                            "extensions": {"code": "UNAUTHENTICATED"},
                        }
                    ]
                },
            )
        raise
    data = response.get("data")
    # Wiz can return HTTP 200 with GraphQL errors and partial data.
    if response.get("errors"):
        if isinstance(data, dict):
            try:
                name, connection = extract_connection(data)
                print(
                    f"[{now_iso()}] graphql returned partial data with errors; "
                    f"continuing with available nodes for '{name}'."
                )
                return name, connection
            except RuntimeError:
                pass
        return "", response
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected GraphQL response: {response}")
    return extract_connection(data)


def graphql_errors(payload: dict[str, Any]) -> list[dict[str, Any]]:
    errors = payload.get("errors")
    if not isinstance(errors, list):
        return []
    return [e for e in errors if isinstance(e, dict)]


def graphql_error_codes(payload: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for err in graphql_errors(payload):
        code = str((err.get("extensions") or {}).get("code", "")).upper()
        if code:
            codes.add(code)
    return codes


def graphql_error_summary(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for err in graphql_errors(payload):
        code = str((err.get("extensions") or {}).get("code", "")).upper() or "UNKNOWN"
        message = str(err.get("message", "")).strip()
        if message:
            parts.append(f"{code}: {message}")
        else:
            parts.append(code)
    return " | ".join(parts) if parts else json.dumps(payload, ensure_ascii=True)


def is_scope_unauthorized_error(payload: dict[str, Any]) -> bool:
    for err in graphql_errors(payload):
        code = str((err.get("extensions") or {}).get("code", "")).upper()
        message = str(err.get("message", "")).lower()
        if code == "UNAUTHORIZED":
            return True
        if "required scopes" in message or "access denied" in message:
            return True
    return False


def is_token_expired_error(payload: dict[str, Any]) -> bool:
    for err in graphql_errors(payload):
        code = str((err.get("extensions") or {}).get("code", "")).upper()
        message = str(err.get("message", "")).lower()
        if code in {"UNAUTHENTICATED", "TOKEN_EXPIRED"}:
            return True
        if "token" in message and ("expired" in message or "invalid" in message):
            return True
    return False


def fetch_wiz_items(cfg: Config, token: str) -> list[dict[str, Any]]:
    last_error = "Unknown query failure."
    current_token = token
    token_refresh_attempts = 0
    for query_text in cfg.query_candidates:
        items: list[dict[str, Any]] = []
        after: str | None = None
        connection_name: str | None = None
        for _ in range(cfg.wiz_max_pages):
            page_retry_attempt = 0
            while True:
                variables = {"first": cfg.wiz_page_size, "after": after}
                if cfg.wiz_filter_by is not None:
                    variables["filterBy"] = cfg.wiz_filter_by
                if cfg.wiz_order_by is not None:
                    variables["orderBy"] = cfg.wiz_order_by
                result_name, payload = run_wiz_query(cfg, current_token, query_text, variables)
                if result_name:
                    break
                if is_token_expired_error(payload) and token_refresh_attempts < cfg.wiz_token_refresh_retries:
                    token_refresh_attempts += 1
                    print(
                        f"[{now_iso()}] wiz token expired/invalid, refreshing token "
                        f"(attempt {token_refresh_attempts}/{cfg.wiz_token_refresh_retries})"
                    )
                    current_token = fetch_wiz_token(cfg)
                    continue
                if is_scope_unauthorized_error(payload):
                    error_text = graphql_error_summary(payload)
                    raise RuntimeError(
                        "Wiz GraphQL authorization error. Check service-account scopes. "
                        f"Details: {error_text}"
                    )
                if "INTERNAL" in graphql_error_codes(payload) and page_retry_attempt < cfg.wiz_max_retries:
                    delay = min(cfg.wiz_retry_max_secs, cfg.wiz_retry_base_secs * (2 ** page_retry_attempt))
                    page_retry_attempt += 1
                    print(
                        f"[{now_iso()}] graphql INTERNAL error, retrying in {delay:.1f}s "
                        f"(attempt {page_retry_attempt}/{cfg.wiz_max_retries})"
                    )
                    time.sleep(delay)
                    continue
                last_error = graphql_error_summary(payload)
                items = []
                break
            if not result_name:
                break
            connection_name = result_name
            connection = payload
            nodes = connection.get("nodes", [])
            if not isinstance(nodes, list):
                raise RuntimeError(f"{connection_name}.nodes is not a list.")
            for node in nodes:
                if isinstance(node, dict):
                    items.append(node)
            page_info = connection.get("pageInfo") or {}
            has_next = bool(page_info.get("hasNextPage"))
            after = page_info.get("endCursor")
            if not has_next or not after:
                break
        # Return if query shape is valid, even when there are zero matches.
        if connection_name is not None:
            return items
    raise RuntimeError(f"All GraphQL query candidates failed. Last error: {last_error}")


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

    title = (
        item.get("title")
        or (control.get("name") if isinstance(control, dict) else None)
        or item.get("name")
        or "Wiz security alert"
    )
    item_type = item.get("type") or "ISSUE"
    severity = item.get("severity") or "unknown"
    created_at = item.get("createdAt") or now_iso()
    status = item.get("status") or "OPEN"
    rule_name = source_rule.get("name") if isinstance(source_rule, dict) else None
    if not rule_name and isinstance(control, dict):
        rule_name = control.get("name")

    return {
        "source": "wiz",
        "event_type": "wiz.security.alert",
        "event_id": event_id,
        "timestamp": now_iso(),
        "alert": {
            "title": str(title),
            "summary": f"Wiz reported a {item_type} item with {severity} severity.",
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


def run_once(cfg: Config) -> None:
    seen_ids = load_seen_ids(cfg.state_file)
    token = fetch_wiz_token(cfg)
    items = fetch_wiz_items(cfg, token)
    forwarded = 0
    matched = 0
    for item in items:
        if not should_forward(cfg, item):
            continue
        matched += 1
        event_id = fingerprint(item)
        if event_id in seen_ids:
            continue
        payload = to_rootly_payload(item, event_id=event_id)
        if cfg.dry_run:
            print(json.dumps(payload, indent=2))
        else:
            post_to_rootly(cfg, payload)
        seen_ids.add(event_id)
        forwarded += 1
    save_seen_ids(cfg.state_file, seen_ids)
    print(
        f"[{now_iso()}] fetched={len(items)} matched={matched} forwarded={forwarded} "
        f"state_file={cfg.state_file}"
    )


def run_loop(cfg: Config) -> None:
    while True:
        try:
            run_once(cfg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[{now_iso()}] error: {exc}")
        time.sleep(cfg.poll_interval_secs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Poll Wiz GraphQL and send vulnerability/threat items to Rootly webhook."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single polling cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call Rootly webhook; print payloads instead.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = Config.from_env(dry_run=args.dry_run)
    if args.once:
        run_once(cfg)
        return
    run_loop(cfg)


if __name__ == "__main__":
    main()
