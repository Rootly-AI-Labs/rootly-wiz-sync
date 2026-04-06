import json
import tempfile
import unittest
from pathlib import Path

from wiz_rootly_bridge.config import Config
from wiz_rootly_bridge.state import (
    load_state,
    load_state_metadata,
    save_state,
    should_forward,
    should_forward_event,
)


def build_config(state_file: Path) -> Config:
    return Config(
        wiz_client_id="client-id",
        wiz_client_secret="client-secret",
        wiz_auth_url="https://auth.app.wiz.io/oauth/token",
        wiz_api_url="https://api.us17.app.wiz.io/graphql",
        wiz_user_agent="Rootly-Wiz-Sync-1.0",
        wiz_page_size=50,
        wiz_max_pages=5,
        wiz_max_rps=3,
        wiz_max_retries=5,
        wiz_retry_base_secs=1.0,
        wiz_retry_max_secs=30.0,
        wiz_token_refresh_retries=5,
        request_timeout_secs=20,
        poll_interval_secs=86400,
        state_file=state_file,
        rootly_webhook_url="https://example.com/webhook",
        rootly_auth_header=None,
        rootly_auth_value=None,
        rootly_max_rps=1,
        rootly_max_retries=5,
        rootly_retry_base_secs=1.0,
        rootly_retry_max_secs=30.0,
        match_keywords=None,
        severity_filter=None,
        resolved_statuses={"resolved", "closed"},
        dry_run=True,
        wiz_filter_by=None,
        wiz_order_by=None,
        query_candidates=["query"],
    )


class StateTests(unittest.TestCase):
    def test_load_state_migrates_legacy_seen_id_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(json.dumps(["issue-1", "issue-2"]), encoding="utf-8")

            state = load_state(state_path)

            self.assertEqual({"issue-1", "issue-2"}, set(state))
            self.assertEqual("OPEN", state["issue-1"]["status"])
            self.assertEqual("", state["issue-1"]["updated_at"])

    def test_save_state_trims_by_last_seen_recency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state = {
                "zzz-issue": {
                    "status": "OPEN",
                    "last_seen_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                },
                "aaa-issue": {
                    "status": "OPEN",
                    "last_seen_at": "2024-01-02T00:00:00+00:00",
                    "updated_at": "2024-01-02T00:00:00+00:00",
                },
            }

            save_state(state_path, state, max_items=1)
            saved = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(["aaa-issue"], list(saved["items"].keys()))

    def test_save_state_orders_by_actual_timestamp_not_raw_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state = {
                "lexicographically-larger-but-older": {
                    "status": "OPEN",
                    "last_seen_at": "2024-03-23T13:59:30+02:00",
                    "updated_at": "2024-03-23T13:59:30+02:00",
                },
                "lexicographically-smaller-but-newer": {
                    "status": "OPEN",
                    "last_seen_at": "2024-03-23T12:59:30+00:00",
                    "updated_at": "2024-03-23T12:59:30+00:00",
                },
            }

            save_state(state_path, state, max_items=1)
            saved = json.loads(state_path.read_text(encoding="utf-8"))

            self.assertEqual(["lexicographically-smaller-but-newer"], list(saved["items"].keys()))

    def test_load_state_backfills_last_seen_from_existing_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": {
                            "issue-1": {
                                "status": "OPEN",
                                "updated_at": "2024-01-03T00:00:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            state = load_state(state_path)

            self.assertEqual("2024-01-03T00:00:00+00:00", state["issue-1"]["last_seen_at"])

    def test_save_state_persists_run_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            save_state(
                state_path,
                {
                    "issue-1": {
                        "status": "OPEN",
                        "last_seen_at": "2024-01-02T00:00:00+00:00",
                    }
                },
                metadata={"last_successful_run_at": "2024-01-03T00:00:00+00:00"},
            )

            metadata = load_state_metadata(state_path)

            self.assertEqual("2024-01-03T00:00:00+00:00", metadata["last_successful_run_at"])

    def test_should_forward_defaults_to_all_matching_severities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = build_config(Path(tmpdir) / "state.json")
            cfg.severity_filter = {"high"}

            self.assertTrue(should_forward(cfg, {"severity": "HIGH"}))
            self.assertFalse(should_forward(cfg, {"severity": "LOW"}))

    def test_should_forward_event_skips_initial_resolved_and_sends_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = build_config(Path(tmpdir) / "state.json")

            self.assertFalse(
                should_forward_event(
                    cfg,
                    {"id": "issue-1", "status": "RESOLVED", "updatedAt": "2024-01-02T00:00:00+00:00"},
                    None,
                )
            )
            self.assertTrue(
                should_forward_event(
                    cfg,
                    {"id": "issue-1", "status": "RESOLVED", "updatedAt": "2024-01-02T00:00:00+00:00"},
                    {"status": "OPEN", "updated_at": "2024-01-01T00:00:00+00:00"},
                )
            )


if __name__ == "__main__":
    unittest.main()
