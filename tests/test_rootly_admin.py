import unittest
from unittest.mock import patch

from wiz_rootly_bridge.config import RootlyBootstrapConfig
from wiz_rootly_bridge.http_client import HttpRequestError
from wiz_rootly_bridge.rootly_admin import (
    _rootly_api_request,
    build_alert_source_payload,
    build_urgency_rules,
    bootstrap_result_as_text,
    derive_webhook_urls,
)


def build_config() -> RootlyBootstrapConfig:
    return RootlyBootstrapConfig(
        rootly_api_token="token",
        rootly_api_url="https://api.rootly.com",
        source_name="Wiz Security Alerts",
        source_id=None,
        owner_group_ids=["grp_123"],
        dry_run=True,
    )


class RootlyAdminTests(unittest.TestCase):
    def test_derive_webhook_urls_uses_secret_query_and_bearer_base(self) -> None:
        query_url, auth_url = derive_webhook_urls(
            "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks/notify/TYPE/ID",
            "super-secret",
        )

        self.assertEqual(
            "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=super-secret",
            query_url,
        )
        self.assertEqual(
            "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks",
            auth_url,
        )

    def test_build_urgency_rules_matches_known_names(self) -> None:
        urgencies = [
            {"id": "urg_critical", "attributes": {"name": "Critical"}},
            {"id": "urg_high", "attributes": {"name": "High"}},
            {"id": "urg_medium", "attributes": {"name": "Medium"}},
            {"id": "urg_low", "attributes": {"name": "Low"}},
        ]

        rules = build_urgency_rules(urgencies)

        self.assertEqual(4, len(rules))
        self.assertEqual("$.urgency", rules[0]["json_path"])
        self.assertEqual("urg_high", rules[1]["alert_urgency_id"])

    def test_build_alert_source_payload_sets_dedupe_resolution_and_template(self) -> None:
        payload = build_alert_source_payload(
            build_config(),
            urgency_rules=[{"json_path": "$.urgency", "operator": "is", "value": "High", "alert_urgency_id": "urg_high"}],
            alert_field_ids={
                "title": "fld_title",
                "description": "fld_description",
                "external_id": "fld_external_id",
            },
        )

        attributes = payload["data"]["attributes"]
        self.assertTrue(attributes["deduplicate_alerts_by_key"])
        self.assertEqual("$.dedupe_key", attributes["deduplication_key_path"])
        self.assertEqual("{{ alert.data.title }}", attributes["alert_template_attributes"]["title"])
        self.assertTrue(attributes["sourceable_attributes"]["auto_resolve"])
        self.assertEqual("fld_title", attributes["alert_source_fields_attributes"][0]["alert_field_id"])
        self.assertEqual(
            "fld_external_id",
            attributes["resolution_rule_attributes"]["identifier_matchable_id"],
        )
        self.assertEqual(
            "$.resolved",
            attributes["resolution_rule_attributes"]["conditions_attributes"][0]["json_path"],
        )
        self.assertEqual(["grp_123"], attributes["owner_group_ids"])

    def test_bootstrap_result_as_text_includes_env_lines(self) -> None:
        text = bootstrap_result_as_text(
            type(
                "BootstrapResult",
                (),
                {
                    "mode": "created",
                    "source_id": "src_123",
                    "source_name": "Wiz Security Alerts",
                    "webhook_url": "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks?secret=abc",
                    "webhook_auth_url": "https://webhooks.rootly.com/webhooks/incoming/generic_webhooks",
                    "webhook_bearer_token": "abc",
                    "payload": {"data": {"attributes": {"name": "Wiz Security Alerts"}}},
                },
            )()
        )

        self.assertIn("ROOTLY_ALERT_SOURCE_ID=src_123", text)
        self.assertIn("ROOTLY_WEBHOOK_BEARER_TOKEN=abc", text)

    def test_rootly_api_request_surfaces_cloudflare_1010_helpfully(self) -> None:
        with patch(
            "wiz_rootly_bridge.rootly_admin.http_json",
            side_effect=HttpRequestError(
                "HTTP 403",
                status_code=403,
                body="error code: 1010",
            ),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                _rootly_api_request(build_config(), "GET", "/v1/alert_sources")

        self.assertIn("Cloudflare edge blocked", str(exc_info.exception))


if __name__ == "__main__":
    unittest.main()
