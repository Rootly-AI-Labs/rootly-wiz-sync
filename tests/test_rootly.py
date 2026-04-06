import unittest
from pathlib import Path
from unittest.mock import patch

from wiz_rootly_bridge.config import Config
from wiz_rootly_bridge.rootly import post_to_rootly


def build_config() -> Config:
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
        state_file=Path(".wiz_rootly_seen_ids.json"),
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


class RootlyTests(unittest.TestCase):
    def test_post_to_rootly_uses_retry_and_throttle_controls(self) -> None:
        cfg = build_config()

        with patch("wiz_rootly_bridge.rootly.http_json") as http_json_mock:
            post_to_rootly(cfg, {"example": "payload"})

        kwargs = http_json_mock.call_args.kwargs
        self.assertEqual(5, kwargs["max_retries"])
        self.assertEqual({429, 500, 502, 503, 504}, kwargs["retry_on_statuses"])
        self.assertEqual(1, kwargs["throttle_per_sec"])
        self.assertEqual("rootly", kwargs["throttle_key"])


if __name__ == "__main__":
    unittest.main()
