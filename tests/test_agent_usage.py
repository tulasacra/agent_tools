import contextlib
import io
import json
import os
import subprocess
import threading
import time
import types
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "agent_usage"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 13, tzinfo=timezone.utc).astimezone(tz)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.usage = types.ModuleType("agent_usage")
        exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), self.usage.__dict__)
        environment = patch.dict(os.environ, TZ="UTC")
        environment.start()
        time.tzset()
        self.addCleanup(time.tzset)
        self.addCleanup(environment.stop)

    def invoke(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.usage.main(args)
        return out.getvalue()

    def test_collectors_json_multiple_windows_and_credentials_stay_private(self):
        reset = 1792454400  # 2026-10-20 UTC
        responses = {
            "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage": {
                "planUsage": {"autoPercentUsed": 12.34, "apiPercentUsed": 56.78},
                "billingCycleStart": (reset - 30 * 86400) * 1000,
                "billingCycleEnd": reset * 1000,
            },
            "https://api.github.com/copilot_internal/user": {
                "quota_snapshots": {"premium_interactions": {"percent_remaining": 80}},
                "quota_reset_date": "2026-11-01",
            },
            "https://chatgpt.com/backend-api/wham/usage": {
                "rate_limit": {
                    "allowed": True,
                    "primary_window": {"used_percent": 5, "reset_at": reset, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 42, "reset_at": reset, "limit_window_seconds": 604800},
                },
            },
        }
        auth = json.dumps({"accessToken": "PRIVATE", "tokens": {"access_token": "PRIVATE", "account_id": "PRIVATE"}})
        agy = "Gemini\tWeekly Limit Remaining\t75%\t2026-10-20T00:00:00Z\nOther\tLimit Remaining\t50%\t2026-10-13T05:00:00Z\n"
        with patch.object(Path, "read_text", return_value=auth), \
                patch.object(self.usage, "http_json", side_effect=lambda url, *a, **kw: responses[url]), \
                patch.object(self.usage, "copilot_token", return_value="PRIVATE"), \
                patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=agy)) as run:
            raw = self.invoke(["--json"])
        self.assertNotIn("PRIVATE", raw)
        data = json.loads(raw)
        self.assertIsNotNone(datetime.fromisoformat(data["generated_at"]).tzinfo)
        rows = data["rows"]
        self.assertEqual([r["provider"] for r in rows], ["cursor", "cursor", "copilot", "codex", "codex", "antigravity", "antigravity"])
        self.assertEqual([r["used_percent"] for r in rows], [12.34, 56.78, 20, 5, 42, 25, 50])
        self.assertEqual([r["window_seconds"] for r in rows[3:5]], [18000, 604800])
        self.assertEqual(rows[0]["reset_at"], "2026-10-20T00:00:00+00:00")
        self.assertTrue(all(r["error"] is None for r in rows))
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_terminal_table_keeps_existing_format(self):
        def fixture():
            self.usage.row("Cursor (Grok)", 12.34, "2026-10-20T00:00:00Z", 30 * 86400)
            self.usage.row("Codex (Gpt)", 99.96, "2026-10-13T05:00:00Z", 5 * 3600)
            self.usage.fail("Copilot", "http 401")

        rows = self.usage.fetch_wrapper("Test", fixture)
        with patch.object(self.usage, "collect_rows", return_value=rows), patch.object(self.usage, "datetime", FixedDateTime):
            actual = self.invoke([])
        self.assertEqual(actual, (
            "Agent            Used  Resets in     Reset date\n"
            "-------------  ------  ------------  ----------\n"
            "Cursor (Grok)   12.3%  7.0/ 30 days  20.10.26\n"
            "Codex (Gpt)    100.0%  0.2/0.2 days  13.10.26\n"
            "Copilot        http 401\n"
        ))

    def test_reset_timestamp_formats_and_timezone(self):
        for reset in (1792454400, 1792454400000, "1792454400000", "2026-10-20", "2026-10-20T02:00:00+02:00"):
            with self.subTest(reset=reset):
                rows = self.usage.fetch_wrapper("Codex", lambda: self.usage.row("Codex", 1, reset, 18000))
                self.assertEqual(rows[0]["reset_at"], "2026-10-20T00:00:00+00:00")

    def test_partial_failures_and_stable_concurrent_order(self):
        finished = threading.Event()

        def cursor():
            self.assertTrue(finished.wait(2))
            self.usage.row("Cursor (Grok)", 10, "2026-10-20", 86400)

        def copilot():
            raise urllib.error.HTTPError("https://example.test", 401, "unauthorized", {}, None)

        def codex():
            self.usage.row("Codex (Gpt)", 20, "2026-10-20", 18000)
            finished.set()

        def agy():
            raise subprocess.TimeoutExpired(["agy"], 30)

        with patch.multiple(self.usage, cursor_rows=cursor, copilot_rows=copilot, codex_rows=codex, antigravity_rows=agy):
            rows = json.loads(self.invoke(["--json"]))["rows"]
        self.assertEqual([r["provider"] for r in rows], ["cursor", "copilot", "codex", "antigravity"])
        self.assertEqual(rows[1]["error"], "http 401")
        self.assertEqual(rows[3]["error"], "request timed out after 30s")
        for row in (rows[1], rows[3]):
            for key in ("used_percent", "reset_at", "window_seconds"):
                self.assertIsNone(row[key])

    def test_subprocess_timeouts_produce_error_rows(self):
        for label, collect in (("Copilot", self.usage.copilot_rows), ("Antigravity", self.usage.antigravity_rows)):
            with self.subTest(provider=label), patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(["tool"], 30)) as run:
                rows = self.usage.fetch_wrapper(label, collect)
                self.assertEqual(run.call_args.kwargs["timeout"], 30)
                self.assertEqual(rows[0]["error"], "request timed out after 30s")
                self.assertIsNone(rows[0]["used_percent"])

    def test_missing_credentials_and_all_provider_failures_are_valid_json(self):
        def missing():
            raise FileNotFoundError("login not found")

        with patch.multiple(self.usage, cursor_rows=missing, copilot_rows=missing, codex_rows=missing, antigravity_rows=missing):
            data = json.loads(self.invoke(["--json"]))
        self.assertEqual(len(data["rows"]), 4)
        self.assertTrue(all(r["used_percent"] is None and r["error"] for r in data["rows"]))

    def test_invalid_response_cannot_become_zero_or_nonstandard_json(self):
        for percent, window in ((float("nan"), 18000), (float("inf"), 18000), (-1, 18000), (1, 0), (1, float("inf"))):
            with self.subTest(percent=percent, window=window):
                rows = self.usage.fetch_wrapper("Codex", lambda: self.usage.row("Codex", percent, "2026-10-20", window))
                self.assertIsNotNone(rows[0]["error"])
                self.assertIsNone(rows[0]["used_percent"])
                json.dumps(rows, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
