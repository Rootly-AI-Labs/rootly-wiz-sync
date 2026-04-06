import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from wiz_rootly_bridge.config import Config
from wiz_rootly_bridge.runner import (
    build_setup_validation_report,
    env_updates_for_bootstrap_result,
    parse_args,
    main,
    resolve_env_target_path,
    run_once,
    write_env_updates,
)
from wiz_rootly_bridge.state import load_state, load_state_metadata


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
        dry_run=False,
        wiz_filter_by=None,
        wiz_order_by=None,
        query_candidates=["query"],
    )


class RunnerTests(unittest.TestCase):
    def test_parse_args_defaults_to_sync(self) -> None:
        with patch("sys.argv", ["wiz_to_rootly.py"]):
            args = parse_args()

        self.assertEqual(args.command, "sync")
        self.assertFalse(args.once)

    def test_main_prints_clean_sync_error_and_exits(self) -> None:
        args = SimpleNamespace(
            command="sync",
            once=False,
            dry_run=False,
            env_file="",
            rootly_api_token="",
            rootly_api_url="",
            rootly_alert_source_name="",
            rootly_alert_source_id="",
            rootly_owner_group_id=[],
            write_env=False,
        )
        cfg = build_config(Path("state.json"))

        with patch("wiz_rootly_bridge.runner.parse_args", return_value=args), patch(
            "wiz_rootly_bridge.runner.load_runtime_env", return_value=None
        ), patch("wiz_rootly_bridge.runner.apply_cli_env_overrides"), patch(
            "wiz_rootly_bridge.runner.Config.from_env", return_value=cfg
        ), patch("wiz_rootly_bridge.runner.run_once", side_effect=RuntimeError("bad credentials")), patch(
            "builtins.print"
        ) as mock_print:
            with self.assertRaises(SystemExit) as ctx:
                main()

        self.assertEqual(1, ctx.exception.code)
        mock_print.assert_any_call(unittest.mock.ANY)
        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list if call.args)
        self.assertIn("sync failed: bad credentials", printed)

    def test_run_once_forwards_open_and_resolution_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "items": {
                            "issue-1": {
                                "status": "OPEN",
                                "updated_at": "2024-01-01T00:00:00+00:00",
                                "last_seen_at": "2024-01-01T00:00:00+00:00",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            cfg = build_config(state_path)
            items = [
                {
                    "id": "issue-1",
                    "status": "RESOLVED",
                    "severity": "HIGH",
                    "type": "THREAT",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                },
                {
                    "id": "issue-2",
                    "status": "OPEN",
                    "severity": "MEDIUM",
                    "type": "VULNERABILITY",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                    "title": "New issue",
                },
                {
                    "id": "issue-3",
                    "status": "RESOLVED",
                    "severity": "LOW",
                    "type": "VULNERABILITY",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                },
            ]

            with patch("wiz_rootly_bridge.runner.fetch_wiz_token", return_value="token"), patch(
                "wiz_rootly_bridge.runner.fetch_wiz_items", return_value=items
            ), patch("wiz_rootly_bridge.runner.post_to_rootly") as post_to_rootly:
                run_once(cfg)

            self.assertEqual(2, post_to_rootly.call_count)
            first_payload = post_to_rootly.call_args_list[0].args[1]
            second_payload = post_to_rootly.call_args_list[1].args[1]

            self.assertEqual("resolved", first_payload["event_action"])
            self.assertTrue(first_payload["resolved"])
            self.assertEqual("opened", second_payload["event_action"])
            self.assertFalse(second_payload["resolved"])

            state = load_state(state_path)
            self.assertEqual("RESOLVED", state["issue-1"]["status"])
            self.assertEqual("OPEN", state["issue-2"]["status"])
            self.assertEqual("RESOLVED", state["issue-3"]["status"])
            metadata = load_state_metadata(state_path)
            self.assertIn("last_successful_run_at", metadata)

    def test_run_once_dry_run_does_not_persist_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            cfg = build_config(state_path)
            cfg.dry_run = True
            items = [
                {
                    "id": "issue-1",
                    "status": "OPEN",
                    "severity": "HIGH",
                    "type": "THREAT",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                }
            ]

            with patch("wiz_rootly_bridge.runner.fetch_wiz_token", return_value="token"), patch(
                "wiz_rootly_bridge.runner.fetch_wiz_items", return_value=items
            ):
                run_once(cfg)

            self.assertFalse(state_path.exists())

    def test_run_once_persists_partial_success_before_delivery_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            cfg = build_config(state_path)
            items = [
                {
                    "id": "issue-1",
                    "status": "OPEN",
                    "severity": "HIGH",
                    "type": "THREAT",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                },
                {
                    "id": "issue-2",
                    "status": "OPEN",
                    "severity": "HIGH",
                    "type": "THREAT",
                    "updatedAt": "2024-01-02T00:00:00+00:00",
                },
            ]

            with patch("wiz_rootly_bridge.runner.fetch_wiz_token", return_value="token"), patch(
                "wiz_rootly_bridge.runner.fetch_wiz_items", return_value=items
            ), patch(
                "wiz_rootly_bridge.runner.post_to_rootly",
                side_effect=[None, RuntimeError("HTTP 429")],
            ):
                with self.assertRaises(RuntimeError):
                    run_once(cfg)

            state = load_state(state_path)
            self.assertIn("issue-1", state)
            self.assertNotIn("issue-2", state)
            metadata = load_state_metadata(state_path)
            self.assertNotIn("last_successful_run_at", metadata)

    def test_build_setup_validation_report_is_ready_for_minimal_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env.wiz-rootly"
            with patch.dict(
                os.environ,
                {
                    "WIZ_CLIENT_ID": "client-id",
                    "WIZ_CLIENT_SECRET": "client-secret",
                    "ROOTLY_WEBHOOK_URL": "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=abc",
                    "WIZ_STATE_FILE": str(Path(tmpdir) / "state.json"),
                },
                clear=True,
            ):
                report = build_setup_validation_report(env_path)

            self.assertTrue(report.ok)
            self.assertIn("Next step: run `python3 wiz_to_rootly.py sync --dry-run`.", report.text)
            self.assertIn("Keyword filter: disabled", report.text)
            self.assertIn('Default order: not set', report.text)
            self.assertIn('Default Wiz filter: {"status":["OPEN","IN_PROGRESS"]}', report.text)

    def test_build_setup_validation_report_flags_missing_values(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            report = build_setup_validation_report(None)

        self.assertFalse(report.ok)
        self.assertIn("Missing WIZ_CLIENT_ID", report.text)
        self.assertIn("Missing ROOTLY_WEBHOOK_URL", report.text)
        self.assertIn("bootstrap-rootly", report.text)

    def test_env_updates_for_bootstrap_result_prefers_query_param_url(self) -> None:
        values, blank_keys = env_updates_for_bootstrap_result(
            SimpleNamespace(
                source_id="src_123",
                webhook_url="https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=abc",
                webhook_auth_url="https://webhooks.rootly.com/webhooks/incoming/generic_webhooks",
                webhook_bearer_token="abc",
            )
        )

        self.assertEqual("src_123", values["ROOTLY_ALERT_SOURCE_ID"])
        self.assertTrue(values["ROOTLY_WEBHOOK_URL"].endswith("?secret=abc"))
        self.assertIn("ROOTLY_WEBHOOK_BEARER_TOKEN", blank_keys)

    def test_write_env_updates_uses_example_when_env_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env.wiz-rootly"
            example_path = Path(tmpdir) / ".env.wiz-rootly.example"
            example_path.write_text(
                "WIZ_CLIENT_ID=replace-me\nROOTLY_WEBHOOK_URL=https://old.example\nROOTLY_WEBHOOK_BEARER_TOKEN=stale\n",
                encoding="utf-8",
            )

            write_env_updates(
                env_path,
                {
                    "ROOTLY_ALERT_SOURCE_ID": "src_123",
                    "ROOTLY_WEBHOOK_URL": "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=abc",
                },
                blank_keys={"ROOTLY_WEBHOOK_BEARER_TOKEN"},
            )

            text = env_path.read_text(encoding="utf-8")
            self.assertIn("WIZ_CLIENT_ID=replace-me", text)
            self.assertIn("ROOTLY_ALERT_SOURCE_ID=src_123", text)
            self.assertIn("ROOTLY_WEBHOOK_BEARER_TOKEN=", text)

    def test_resolve_env_target_path_defaults_to_dot_env_wiz_rootly(self) -> None:
        args = SimpleNamespace(env_file="")
        path = resolve_env_target_path(args, None)

        self.assertEqual(Path(".env.wiz-rootly"), path)


if __name__ == "__main__":
    unittest.main()
