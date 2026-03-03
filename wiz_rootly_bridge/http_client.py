"""HTTP client with throttling and retry behavior."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib import error, request

from .utils import now_iso

_LAST_WIZ_CALL_MONO = 0.0


class HttpRequestError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, body: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


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

