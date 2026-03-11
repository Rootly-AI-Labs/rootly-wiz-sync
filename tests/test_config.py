import tempfile
import unittest
from pathlib import Path

from wiz_rootly_bridge.config import default_wiz_filter_by, effective_wiz_filter_by


class ConfigTests(unittest.TestCase):
    def test_default_wiz_filter_by_uses_active_statuses_on_first_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"

            filter_by = default_wiz_filter_by(state_path, {"resolved", "closed", "rejected"})

            self.assertEqual({"status": ["OPEN", "IN_PROGRESS"]}, filter_by)

    def test_default_wiz_filter_by_adds_resolution_statuses_for_legacy_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text('{"version": 2, "items": {}}', encoding="utf-8")

            filter_by = default_wiz_filter_by(state_path, {"resolved", "closed", "rejected"})

            self.assertEqual(
                {"status": ["OPEN", "IN_PROGRESS", "CLOSED", "REJECTED", "RESOLVED"]},
                filter_by,
            )

    def test_default_wiz_filter_by_uses_status_changed_at_after_last_successful_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                '{"version": 3, "metadata": {"last_successful_run_at": "2024-01-03T00:00:00+00:00"}, "items": {}}',
                encoding="utf-8",
            )

            filter_by = default_wiz_filter_by(state_path, {"resolved", "closed", "rejected"})

            self.assertEqual(
                {"statusChangedAt": {"after": "2024-01-03T00:00:00+00:00"}},
                filter_by,
            )

    def test_effective_wiz_filter_by_merges_delta_and_severity_into_custom_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            state_path.write_text(
                '{"version": 3, "metadata": {"last_successful_run_at": "2024-01-03T00:00:00+00:00"}, "items": {}}',
                encoding="utf-8",
            )

            filter_by = effective_wiz_filter_by(
                state_path,
                {"resolved", "closed", "rejected"},
                {"high", "critical"},
                {"type": ["THREAT_DETECTION"]},
            )

            self.assertEqual(
                {
                    "type": ["THREAT_DETECTION"],
                    "statusChangedAt": {"after": "2024-01-03T00:00:00+00:00"},
                    "severity": ["CRITICAL", "HIGH"],
                },
                filter_by,
            )


if __name__ == "__main__":
    unittest.main()
