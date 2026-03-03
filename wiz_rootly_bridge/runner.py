"""CLI runner for the Wiz to Rootly bridge."""

from __future__ import annotations

import argparse
import json
import time

from .config import Config
from .rootly import post_to_rootly, to_rootly_payload
from .state import fingerprint, load_seen_ids, save_seen_ids, should_forward
from .utils import now_iso
from .wiz import fetch_wiz_items, fetch_wiz_token


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

