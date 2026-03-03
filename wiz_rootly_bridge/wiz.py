"""Wiz auth and GraphQL querying logic."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, parse, request

from .config import Config
from .http_client import HttpRequestError, http_json, retry_after_seconds, throttle_wiz_requests
from .utils import now_iso


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
            result_name = ""
            payload: dict[str, Any] = {}
            page_retry_attempt = 0
            while True:
                variables: dict[str, Any] = {"first": cfg.wiz_page_size, "after": after}
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

