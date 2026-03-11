import unittest
from pathlib import Path
from unittest.mock import patch

from wiz_rootly_bridge.config import Config
from wiz_rootly_bridge.constants import DEFAULT_QUERY_ISSUES_COMPAT
from wiz_rootly_bridge.http_client import HttpRequestError
from wiz_rootly_bridge.wiz import fetch_wiz_items, query_text_with_disabled_optionals


def build_config() -> Config:
    return Config(
        wiz_client_id="client-id",
        wiz_client_secret="client-secret",
        wiz_auth_url="https://auth.app.wiz.io/oauth/token",
        wiz_api_url="https://api.us17.app.wiz.io/graphql",
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
        dry_run=True,
        wiz_filter_by=None,
        wiz_order_by={"field": "UPDATED_AT", "direction": "DESC"},
        query_candidates=["rich-query", "compat-query"],
    )


class WizTests(unittest.TestCase):
    def test_query_text_with_disabled_optionals_removes_optional_args(self) -> None:
        query_text = """
query IssuesTable($filterBy: IssueFilters, $first: Int, $after: String, $orderBy: IssueOrder) {
  issues(filterBy: $filterBy, first: $first, after: $after, orderBy: $orderBy) {
    nodes { id }
  }
}
""".strip()

        updated = query_text_with_disabled_optionals(query_text, {"filterBy", "orderBy"})

        self.assertNotIn("$filterBy", updated)
        self.assertNotIn("$orderBy", updated)
        self.assertIn("issues(first: $first, after: $after)", updated)

    def test_fetch_wiz_items_falls_back_after_graphql_validation_http_400(self) -> None:
        cfg = build_config()
        responses = [
            HttpRequestError(
                "HTTP 400",
                status_code=400,
                body='{"errors":[{"message":"Cannot query field \\"title\\" on type \\"Issue\\".","extensions":{"code":"GRAPHQL_VALIDATION_FAILED"}}]}',
            ),
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-1",
                                "severity": "HIGH",
                                "status": "OPEN",
                            }
                        ],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            },
        ]

        def fake_http_json(**_: object) -> dict:
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with patch("wiz_rootly_bridge.wiz.http_json", side_effect=fake_http_json):
            items = fetch_wiz_items(cfg, token="token")

        self.assertEqual(1, len(items))
        self.assertEqual("issue-1", items[0]["id"])

    def test_fetch_wiz_items_retries_without_rejected_order_by_variable(self) -> None:
        cfg = build_config()
        cfg.query_candidates = [DEFAULT_QUERY_ISSUES_COMPAT]
        seen_queries: list[str] = []
        responses = [
            HttpRequestError(
                "HTTP 400",
                status_code=400,
                body='{"errors":[{"message":"invalid type for variable: \\"orderBy\\"","extensions":{"code":"VALIDATION_INVALID_TYPE_VARIABLE"}}]}',
            ),
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-2",
                                "severity": "MEDIUM",
                                "status": "OPEN",
                            }
                        ],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            },
        ]

        def fake_http_json(**kwargs: object) -> dict:
            seen_queries.append(str(kwargs["payload"]["query"]))
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        with patch("wiz_rootly_bridge.wiz.http_json", side_effect=fake_http_json):
            items = fetch_wiz_items(cfg, token="token")

        self.assertEqual(1, len(items))
        self.assertEqual("issue-2", items[0]["id"])
        self.assertIn("orderBy", seen_queries[0])
        self.assertNotIn("orderBy", seen_queries[1])

    def test_fetch_wiz_items_uses_override_filter_by(self) -> None:
        cfg = build_config()
        cfg.query_candidates = [DEFAULT_QUERY_ISSUES_COMPAT]
        seen_filters: list[object] = []

        def fake_http_json(**kwargs: object) -> dict:
            seen_filters.append(kwargs["payload"]["variables"].get("filterBy"))
            return {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "issue-3",
                                "severity": "HIGH",
                                "status": "OPEN",
                            }
                        ],
                        "pageInfo": {
                            "hasNextPage": False,
                            "endCursor": None,
                        },
                    }
                }
            }

        with patch("wiz_rootly_bridge.wiz.http_json", side_effect=fake_http_json):
            items = fetch_wiz_items(
                cfg,
                token="token",
                wiz_filter_by={"statusChangedAt": {"after": "2024-01-03T00:00:00+00:00"}},
            )

        self.assertEqual(1, len(items))
        self.assertEqual(
            {"statusChangedAt": {"after": "2024-01-03T00:00:00+00:00"}},
            seen_filters[0],
        )


if __name__ == "__main__":
    unittest.main()
