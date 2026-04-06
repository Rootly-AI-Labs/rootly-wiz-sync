"""CLI runner for the Wiz to Rootly bridge."""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from .config import (
    Config,
    RootlyBootstrapConfig,
    default_wiz_filter_by,
    effective_wiz_filter_by,
    env_json_dict,
    load_env_file,
)
from .rootly import post_to_rootly, to_rootly_payload
from .rootly_admin import bootstrap_result_as_text, bootstrap_rootly_alert_source
from .state import (
    fingerprint,
    load_state_data,
    save_state,
    should_forward,
    should_forward_event,
    update_state_record,
)
from .utils import now_iso
from .wiz import fetch_wiz_items, fetch_wiz_token


@dataclass
class SetupValidationReport:
    ok: bool
    text: str


def run_once(cfg: Config) -> None:
    run_started_at = now_iso()
    metadata, state = load_state_data(cfg.state_file)
    last_successful_run_at = metadata.get("last_successful_run_at", "").strip()
    sync_mode = "incremental" if last_successful_run_at else "initial"
    print(
        f"[{now_iso()}] starting Wiz sync mode={sync_mode} dry_run={str(cfg.dry_run).lower()} "
        f"state_records={len(state)} state_file={cfg.state_file}"
    )
    if last_successful_run_at:
        print(f"[{now_iso()}] using incremental cursor statusChangedAt.after={last_successful_run_at}")
    else:
        print(
            f"[{now_iso()}] no previous successful sync metadata found; "
            "using initial sync filters."
        )
    print(f"[{now_iso()}] requesting Wiz access token...")
    token = fetch_wiz_token(cfg)
    print(f"[{now_iso()}] Wiz access token acquired.")
    wiz_filter_by = effective_wiz_filter_by(
        cfg.state_file,
        cfg.resolved_statuses,
        cfg.severity_filter,
        cfg.wiz_filter_by,
    )
    print(
        f"[{now_iso()}] querying Wiz issues with filter="
        f"{json.dumps(wiz_filter_by, sort_keys=True)}"
    )
    items = fetch_wiz_items(cfg, token, wiz_filter_by=wiz_filter_by)
    print(f"[{now_iso()}] received {len(items)} Wiz items; evaluating matches and updates.")
    forwarded = 0
    matched = 0
    for item in items:
        if not should_forward(cfg, item):
            continue
        matched += 1
        event_id = fingerprint(item)
        previous = state.get(event_id)
        should_send = should_forward_event(cfg, item, previous)
        if should_send:
            payload = to_rootly_payload(cfg, item, event_id=event_id)
            if forwarded == 0:
                action = "printing" if cfg.dry_run else "sending"
                print(f"[{now_iso()}] {action} Rootly alert payloads...")
            if cfg.dry_run:
                print(json.dumps(payload, indent=2))
            else:
                post_to_rootly(cfg, payload)
                state[event_id] = update_state_record(previous, item, was_forwarded=True)
                save_state(cfg.state_file, state, metadata=metadata)
            forwarded += 1
            if forwarded % 25 == 0:
                print(f"[{now_iso()}] processed {forwarded} Rootly alert payloads so far.")
        if should_send and not cfg.dry_run:
            continue
        state[event_id] = update_state_record(previous, item, was_forwarded=should_send)
    if not cfg.dry_run:
        metadata["last_successful_run_at"] = run_started_at
        metadata["last_completed_run_at"] = now_iso()
        save_state(cfg.state_file, state, metadata=metadata)
        state_file_text = str(cfg.state_file)
    else:
        state_file_text = f"{cfg.state_file} (dry-run unchanged)"
    print(
        f"[{now_iso()}] fetched={len(items)} matched={matched} forwarded={forwarded} "
        f"state_file={state_file_text}"
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
        description="Pull Wiz issues and send them to Rootly."
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "sync", "validate", "bootstrap-rootly"),
        default="sync",
        help="Sync once, run continuously, validate local setup, or bootstrap a Rootly Generic Webhook source.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Backward-compatible alias for `sync`.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not call Rootly webhook; print payloads instead.",
    )
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional env file to load before reading config. Defaults to .env.wiz-rootly when present.",
    )
    parser.add_argument(
        "--rootly-api-token",
        default="",
        help="Rootly API token used by the bootstrap-rootly command.",
    )
    parser.add_argument(
        "--rootly-api-url",
        default="",
        help="Rootly API base URL used by the bootstrap-rootly command.",
    )
    parser.add_argument(
        "--rootly-alert-source-name",
        default="",
        help="Alert source name used by the bootstrap-rootly command.",
    )
    parser.add_argument(
        "--rootly-alert-source-id",
        default="",
        help="Existing Rootly alert source ID to update.",
    )
    parser.add_argument(
        "--rootly-owner-group-id",
        action="append",
        default=[],
        help="Owner group ID to assign to the Rootly alert source. Repeat to pass multiple values.",
    )
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="When used with bootstrap-rootly, write the resulting Rootly webhook values into the env file.",
    )
    return parser.parse_args()


def load_runtime_env(args: argparse.Namespace) -> Path | None:
    env_file = args.env_file.strip()
    if env_file:
        env_path = Path(env_file).expanduser()
        if not load_env_file(env_path):
            if args.command == "bootstrap-rootly" and args.write_env:
                return env_path
            raise ValueError(f"Env file not found: {env_path}")
        return env_path
    for candidate in (Path(".env.wiz-rootly"), Path(".env")):
        if load_env_file(candidate):
            return candidate
    return None


def apply_cli_env_overrides(args: argparse.Namespace) -> None:
    overrides = {
        "ROOTLY_API_TOKEN": args.rootly_api_token.strip(),
        "ROOTLY_API_URL": args.rootly_api_url.strip(),
        "ROOTLY_ALERT_SOURCE_NAME": args.rootly_alert_source_name.strip(),
        "ROOTLY_ALERT_SOURCE_ID": args.rootly_alert_source_id.strip(),
    }
    for name, value in overrides.items():
        if value:
            os.environ[name] = value
    if args.rootly_owner_group_id:
        os.environ["ROOTLY_OWNER_GROUP_IDS"] = ",".join(
            value.strip() for value in args.rootly_owner_group_id if value.strip()
        )


def _parse_env_key(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].strip()
    if "=" not in stripped:
        return None
    key, _ = stripped.split("=", 1)
    key = key.strip()
    return key or None


def _initial_env_lines(path: Path) -> list[str]:
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    if path.name == ".env.wiz-rootly":
        example_path = path.with_name(".env.wiz-rootly.example")
        if example_path.exists():
            return example_path.read_text(encoding="utf-8").splitlines()
    return []


def write_env_updates(path: Path, values: dict[str, str], *, blank_keys: set[str] | None = None) -> None:
    blank_keys = blank_keys or set()
    lines = _initial_env_lines(path)
    seen_keys: set[str] = set()
    updated_lines: list[str] = []

    for line in lines:
        key = _parse_env_key(line)
        if key is None:
            updated_lines.append(line)
            continue
        if key in values:
            updated_lines.append(f"{key}={values[key]}")
            seen_keys.add(key)
            continue
        if key in blank_keys:
            updated_lines.append(f"{key}=")
            seen_keys.add(key)
            continue
        updated_lines.append(line)

    if updated_lines and updated_lines[-1].strip():
        updated_lines.append("")
    for key, value in values.items():
        if key not in seen_keys:
            updated_lines.append(f"{key}={value}")
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


def resolve_env_target_path(args: argparse.Namespace, loaded_env_path: Path | None) -> Path:
    if args.env_file.strip():
        return Path(args.env_file.strip()).expanduser()
    if loaded_env_path is not None:
        return loaded_env_path
    return Path(".env.wiz-rootly")


def env_updates_for_bootstrap_result(result: object) -> tuple[dict[str, str], set[str]]:
    updates: dict[str, str] = {}
    blank_keys = {
        "ROOTLY_WEBHOOK_BEARER_TOKEN",
        "ROOTLY_WEBHOOK_AUTH_HEADER",
        "ROOTLY_WEBHOOK_AUTH_VALUE",
    }
    source_id = getattr(result, "source_id", None)
    webhook_url = getattr(result, "webhook_url", None)
    webhook_auth_url = getattr(result, "webhook_auth_url", None)
    webhook_bearer_token = getattr(result, "webhook_bearer_token", None)

    if source_id:
        updates["ROOTLY_ALERT_SOURCE_ID"] = str(source_id)
    if webhook_url:
        updates["ROOTLY_WEBHOOK_URL"] = str(webhook_url)
        return updates, blank_keys
    if webhook_auth_url and webhook_bearer_token:
        updates["ROOTLY_WEBHOOK_URL"] = str(webhook_auth_url)
        updates["ROOTLY_WEBHOOK_BEARER_TOKEN"] = str(webhook_bearer_token)
        blank_keys.discard("ROOTLY_WEBHOOK_BEARER_TOKEN")
        return updates, blank_keys
    raise RuntimeError("Bootstrap succeeded but did not return a usable Rootly webhook URL.")


def _has_secret_query_param(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return any(key == "secret" and value for key, value in parse_qsl(parsed.query, keep_blank_values=True))


def _is_placeholder_env_value(name: str, value: str) -> bool:
    normalized = value.strip()
    if not normalized:
        return True
    lowered = normalized.lower()
    if lowered in {"replace-me", "changeme"}:
        return True
    if name == "ROOTLY_WEBHOOK_URL" and "example.rootly.webhook" in lowered:
        return True
    return False


def build_setup_validation_report(env_path: Path | None) -> SetupValidationReport:
    lines: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    lines.append("Setup validation")
    if env_path is not None:
        lines.append(f"Env file: {env_path}")
    else:
        lines.append("Env file: not auto-loaded (using current shell environment only)")

    wiz_client_id = os.getenv("WIZ_CLIENT_ID", "").strip()
    wiz_client_secret = os.getenv("WIZ_CLIENT_SECRET", "").strip()
    rootly_webhook_url = os.getenv("ROOTLY_WEBHOOK_URL", "").strip()
    rootly_auth_header = os.getenv("ROOTLY_WEBHOOK_AUTH_HEADER", "").strip()
    rootly_auth_value = os.getenv("ROOTLY_WEBHOOK_AUTH_VALUE", "").strip()
    rootly_bearer_token = os.getenv("ROOTLY_WEBHOOK_BEARER_TOKEN", "").strip()
    state_file = os.getenv("WIZ_STATE_FILE", ".wiz_rootly_seen_ids.json").strip() or ".wiz_rootly_seen_ids.json"
    order_by_value = os.getenv("WIZ_ORDER_BY_JSON", "").strip()
    filter_by_value = os.getenv("WIZ_FILTER_BY_JSON", "").strip()
    severity_value = os.getenv("WIZ_ONLY_SEVERITIES", "").strip()
    keyword_value = os.getenv("WIZ_MATCH_KEYWORDS", "").strip()
    resolved_statuses_value = os.getenv("WIZ_RESOLVED_STATUSES", "").strip()

    has_wiz_client_id = not _is_placeholder_env_value("WIZ_CLIENT_ID", wiz_client_id)
    has_wiz_client_secret = not _is_placeholder_env_value("WIZ_CLIENT_SECRET", wiz_client_secret)
    has_rootly_webhook_url = not _is_placeholder_env_value("ROOTLY_WEBHOOK_URL", rootly_webhook_url)

    if has_wiz_client_id:
        lines.append("[OK] WIZ_CLIENT_ID is set")
    else:
        errors.append("Missing WIZ_CLIENT_ID")

    if has_wiz_client_secret:
        lines.append("[OK] WIZ_CLIENT_SECRET is set")
    else:
        errors.append("Missing WIZ_CLIENT_SECRET")

    if has_rootly_webhook_url:
        lines.append("[OK] ROOTLY_WEBHOOK_URL is set")
    else:
        errors.append("Missing ROOTLY_WEBHOOK_URL")

    if has_rootly_webhook_url:
        if _has_secret_query_param(rootly_webhook_url):
            lines.append("[OK] Rootly webhook auth is embedded in the URL query string")
        elif rootly_bearer_token:
            lines.append("[OK] Rootly webhook auth uses ROOTLY_WEBHOOK_BEARER_TOKEN")
        elif rootly_auth_header and rootly_auth_value:
            lines.append(
                f"[OK] Rootly webhook auth uses {rootly_auth_header}"
            )
        else:
            warnings.append(
                "No explicit Rootly webhook auth detected. This can be fine if the webhook URL already includes its secret."
            )

    for name in ("WIZ_FILTER_BY_JSON", "WIZ_ORDER_BY_JSON"):
        try:
            _ = env_json_dict(name)
        except ValueError as exc:
            errors.append(str(exc))

    lines.append(f"[OK] State file path: {state_file}")
    if order_by_value:
        lines.append(f"[OK] Custom order: {order_by_value}")
    else:
        lines.append("[OK] Default order: not set")
    if filter_by_value:
        lines.append(f"[OK] Custom Wiz filter: {filter_by_value}")
    else:
        resolved_statuses = {"resolved", "closed", "rejected"}
        if resolved_statuses_value:
            resolved_statuses = {
                value.strip().lower() for value in resolved_statuses_value.split(",") if value.strip()
            } or resolved_statuses
        severity_filter = None
        if severity_value:
            severity_filter = {value.strip().lower() for value in severity_value.split(",") if value.strip()}
        default_filter = default_wiz_filter_by(Path(state_file), resolved_statuses, severity_filter)
        lines.append(f"[OK] Default Wiz filter: {json.dumps(default_filter, separators=(',', ':'))}")
    if severity_value:
        lines.append(f"[OK] Severity filter: {severity_value}")
    else:
        lines.append("[OK] Severity filter: not set")
    if keyword_value:
        lines.append(f"[OK] Keyword filter: {keyword_value}")
    else:
        lines.append("[OK] Keyword filter: disabled (recommended default)")

    if errors:
        lines.append("")
        lines.append("Missing or invalid configuration:")
        lines.extend(f"- {error_text}" for error_text in errors)
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {warning_text}" for warning_text in warnings)

    lines.append("")
    if errors:
        missing_wiz_credentials = not has_wiz_client_id or not has_wiz_client_secret
        missing_rootly_webhook = not has_rootly_webhook_url
        if missing_rootly_webhook and not missing_wiz_credentials:
            lines.append(
                "Next step: run `python3 wiz_to_rootly.py bootstrap-rootly --rootly-api-token <rootly-api-token> --write-env`, then rerun `python3 wiz_to_rootly.py validate`."
            )
        elif missing_rootly_webhook:
            lines.append(
                "Next step: fill in `WIZ_CLIENT_ID` and `WIZ_CLIENT_SECRET`, then run `python3 wiz_to_rootly.py bootstrap-rootly --rootly-api-token <rootly-api-token> --write-env`, and rerun `python3 wiz_to_rootly.py validate`."
            )
        else:
            lines.append(
                "Next step: fill in the missing values in .env.wiz-rootly and rerun `python3 wiz_to_rootly.py validate`."
            )
    else:
        lines.append("Next step: run `python3 wiz_to_rootly.py sync --dry-run`.")

    return SetupValidationReport(ok=not errors, text="\n".join(lines))


def main() -> None:
    args = parse_args()
    loaded_env_path = load_runtime_env(args)
    apply_cli_env_overrides(args)
    if args.command == "validate":
        report = build_setup_validation_report(loaded_env_path)
        print(report.text)
        raise SystemExit(0 if report.ok else 1)
    if args.command == "bootstrap-rootly":
        if args.write_env and args.dry_run:
            raise ValueError("--write-env cannot be used with --dry-run.")
        cfg = RootlyBootstrapConfig.from_env(
            dry_run=args.dry_run,
            source_name=args.rootly_alert_source_name.strip() or None,
            source_id=args.rootly_alert_source_id.strip() or None,
        )
        result = bootstrap_rootly_alert_source(cfg)
        print(bootstrap_result_as_text(result))
        if args.write_env:
            env_target_path = resolve_env_target_path(args, loaded_env_path)
            values, blank_keys = env_updates_for_bootstrap_result(result)
            write_env_updates(env_target_path, values, blank_keys=blank_keys)
            print("")
            print(f"Updated env file: {env_target_path}")
        return
    cfg = Config.from_env(dry_run=args.dry_run)
    if args.command == "sync" or args.once:
        try:
            run_once(cfg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[{now_iso()}] sync failed: {exc}")
            raise SystemExit(1) from None
        return
    run_loop(cfg)
