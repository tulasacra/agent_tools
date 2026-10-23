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
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_DETAILS_URL = "https://chatgpt.com/backend-api/wham/rate-limit-reset-credits"


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

    def codex_usage(self, count=3):
        return {
            "rate_limit_reset_credits": {"available_count": count, "applicable_available_count": 0},
            "rate_limit": {"primary_window": {
                "used_percent": 25, "reset_at": 1792454400, "limit_window_seconds": 18000,
            }},
        }

    def fetch_codex(self, details, usage=None):
        auth = json.dumps({"tokens": {"access_token": "PRIVATE", "account_id": "PRIVATE"}})

        def request(url, headers):
            response = (self.codex_usage() if usage is None else usage) if url == CODEX_USAGE_URL else details
            if isinstance(response, Exception):
                raise response
            return response

        with patch.object(Path, "read_text", return_value=auth), \
                patch.object(self.usage, "http_json", side_effect=request):
            return self.usage.fetch_wrapper("Codex", self.usage.codex_rows)

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
                "rate_limit_reset_credits": {"available_count": 3, "applicable_available_count": 0},
                "rate_limit": {
                    "allowed": True,
                    "primary_window": {"used_percent": 5, "reset_at": reset, "limit_window_seconds": 18000},
                    "secondary_window": {"used_percent": 42, "reset_at": reset, "limit_window_seconds": 604800},
                },
            },
            CODEX_DETAILS_URL: {
                "available_count": 3,
                "credits": [{"status": "available", "reset_type": "codex_rate_limits", "expires_at": date}
                            for date in ("2026-10-22T21:13:00Z", "2026-10-29T19:44:00Z", "2026-11-07T00:05:00Z")],
            },
        }
        auth = json.dumps({"accessToken": "PRIVATE", "tokens": {"access_token": "PRIVATE", "account_id": "PRIVATE"}})
        agy = "Gemini\tWeekly Limit Remaining\t75%\t2026-10-20T00:00:00Z\nOther\tFive Hour Limit Remaining\t50%\t2026-10-13T05:00:00Z\n"
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
        self.assertEqual(rows[2]["window_seconds"], 31 * 86400)
        self.assertEqual([r["window_seconds"] for r in rows[3:5]], [18000, 604800])
        self.assertEqual([r["manual_resets_available"] for r in rows], [None, None, None, 3, 3, None, None])
        dates = ["2026-10-22T21:13:00+00:00", "2026-10-29T19:44:00+00:00", "2026-11-07T00:05:00+00:00"]
        self.assertEqual([r["manual_reset_expirations"] for r in rows], [None, None, None, dates, dates, None, None])
        self.assertEqual([r["window_seconds"] for r in rows[5:]], [604800, 18000])
        self.assertEqual(rows[0]["reset_at"], "2026-10-20T00:00:00+00:00")
        self.assertTrue(all(r["error"] is None for r in rows))
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_codex_manual_reset_balance_preserves_zero_and_unknown(self):
        auth = json.dumps({"tokens": {"access_token": "PRIVATE", "account_id": "PRIVATE"}})
        cases = [
            ({}, None),
            ({"rate_limit_reset_credits": {"available_count": 0, "applicable_available_count": 0}}, 0),
            ({"rate_limit_reset_credits": {"available_count": 3, "applicable_available_count": 0}}, 3),
        ]
        cases += [({"rate_limit_reset_credits": credits}, None) for credits in (
            None, {}, [], "invalid", 3, {"applicable_available_count": 2},
            *({"available_count": count} for count in (None, -1, 1.5, "3", True, False, [], {}, float("nan"), float("inf"))),
        )]
        for fields, expected in cases:
            response = {
                "rate_limit": {"primary_window": {
                    "used_percent": 25, "reset_at": 1792454400, "limit_window_seconds": 18000,
                }},
                **fields,
            }
            with self.subTest(fields=fields), patch.object(Path, "read_text", return_value=auth), \
                    patch.object(self.usage, "http_json", side_effect=lambda url, headers: response if url == CODEX_USAGE_URL else {}) as request:
                rows = self.usage.fetch_wrapper("Codex", self.usage.codex_rows)
            self.assertEqual(request.call_count, 2)
            self.assertEqual({call.args[0] for call in request.call_args_list}, {CODEX_USAGE_URL, CODEX_DETAILS_URL})
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["manual_resets_available"], expected)
            self.assertIsNone(rows[0]["manual_reset_expirations"])
            self.assertEqual(rows[0]["used_percent"], 25)
            self.assertIsNone(rows[0]["error"])
            json.dumps(rows, allow_nan=False)

    def test_terminal_manual_resets_appear_once_after_codex_windows(self):
        for count in (3, 0, None):
            with self.subTest(count=count):
                rows = self.usage.fetch_wrapper("Cursor", lambda: self.usage.row("Cursor", 10, "2026-10-20", 86400))
                for label, window in (("Codex 5h", 18000), ("Codex Week", 604800)):
                    rows += self.usage.fetch_wrapper("Codex", lambda: self.usage.row(
                        label, 25, "2026-10-20", window, manual_resets_available=count))
                rows += self.usage.fetch_wrapper("Copilot", lambda: self.usage.fail("Copilot", "http 401"))
                with patch.object(self.usage, "collect_rows", return_value=rows):
                    output = self.invoke([])
                summary = f"  Manual resets available: {count if count is not None else '?'}"
                lines = output.splitlines()
                if count != 0:
                    countdown_column = lines[0].index("Resets in")
                    date_column = lines[0].index("Reset date")
                    summary = f"{summary:<{countdown_column}}?"
                    summary = f"{summary:<{date_column}}?"
                self.assertEqual(output.count("Manual resets available:"), 1)
                index = lines.index(summary)
                self.assertTrue(lines[index - 1].startswith("Codex Week"))
                self.assertTrue(lines[index + 1].startswith("Copilot"))

        for failed in (False, True):
            with self.subTest(failed=failed):
                rows = self.usage.fetch_wrapper("Codex", lambda: self.usage.fail("Codex", "http 401")
                                               if failed else self.usage.row("Codex", 25, "2026-10-20", 18000))
                del rows[0]["manual_resets_available"]  # Older snapshots remain displayable.
                del rows[0]["manual_reset_expirations"]
                with patch.object(self.usage, "collect_rows", return_value=rows):
                    output = self.invoke([])
                header = output.splitlines()[0]
                summary = f"{'  Manual resets available: ?':<{header.index('Resets in')}}?"
                summary = f"{summary:<{header.index('Reset date')}}?"
                self.assertTrue(output.endswith(summary + "\n"))

    def test_codex_details_normalize_filter_and_preserve_unknown_and_duplicate_dates(self):
        available = {"status": "available", "reset_type": "codex_rate_limits"}
        details = {"available_count": 6, "credits": [
            {**available, "expires_at": "2026-10-29T20:44:00+01:00"},
            {**available, "expires_at": "2026-10-22T21:13:23.818918Z"},
            {**available, "expires_at": "2026-10-22T23:13:23.818918+02:00"},
            {**available, "expires_at": "bad date"},
            {**available, "expires_at": None},
            available,
            {**available, "status": "used", "expires_at": "2026-10-01T00:00:00Z"},
            {**available, "status": "expired", "expires_at": "2026-10-02T00:00:00Z"},
            {**available, "reset_type": "another_product", "expires_at": "2026-10-03T00:00:00Z"},
        ]}
        rows = self.fetch_codex(details)
        self.assertEqual(rows[0]["manual_resets_available"], 6)
        self.assertEqual(rows[0]["manual_reset_expirations"], [
            "2026-10-22T21:13:23.818918+00:00", "2026-10-22T21:13:23.818918+00:00",
            "2026-10-29T19:44:00+00:00", None, None, None,
        ])
        self.assertEqual(rows[0]["used_percent"], 25)
        self.assertIsNone(rows[0]["error"])

    def test_codex_detail_counts_override_or_fall_back_without_inference(self):
        credit = {"status": "available", "reset_type": "codex_rate_limits", "expires_at": "2026-10-22T21:13:00Z"}
        for count in (0, 1, 7, None, -1, 1.5, "3", True, [], {}, float("nan")):
            for usage_count in (3, None):
                with self.subTest(details=count, usage=usage_count):
                    rows = self.fetch_codex({"available_count": count, "credits": [credit]}, self.codex_usage(usage_count))
                    expected = count if type(count) is int and count >= 0 else usage_count
                    self.assertEqual(rows[0]["manual_resets_available"], expected)
                    self.assertEqual(rows[0]["manual_reset_expirations"], ["2026-10-22T21:13:00+00:00"])
                    self.assertIsNone(rows[0]["error"])
                    json.dumps(rows, allow_nan=False)
        rows = self.fetch_codex({"credits": []}, self.codex_usage(None))
        self.assertIsNone(rows[0]["manual_resets_available"])
        self.assertEqual(rows[0]["manual_reset_expirations"], [])

    def test_codex_malformed_optional_details_preserve_usage_and_balance(self):
        cases = (None, [], "invalid", 3, {}, {"credits": None}, {"credits": {}},
                 {"credits": "invalid"}, {"credits": [None]}, {"credits": ["invalid"]})
        for details in cases:
            with self.subTest(details=details):
                row = self.fetch_codex(details)[0]
                self.assertEqual(row["manual_resets_available"], 3)
                self.assertIsNone(row["manual_reset_expirations"])
                self.assertEqual(row["used_percent"], 25)
                self.assertIsNone(row["error"])
        row = self.fetch_codex({"available_count": 7, "credits": "invalid"})[0]
        self.assertEqual(row["manual_resets_available"], 7)
        self.assertIsNone(row["manual_reset_expirations"])
        for date in (None, "", "invalid", True, 1792454400, [], {}, float("nan"), "99999-10-22T00:00:00Z",
                     "3", "1792454400", "2026-10-22", "2026-10-22T21:13:23.818918", "2026-10-22T21:13Z",
                     "2026-10-22T00:00:00+01:99", "2026-02-30T00:00:00Z"):
            with self.subTest(date=date):
                row = self.fetch_codex({"credits": [{"status": "available", "reset_type": "codex_rate_limits", "expires_at": date}]})[0]
                self.assertEqual(row["manual_reset_expirations"], [None])
                self.assertIsNone(row["error"])

    def test_codex_optional_request_failures_preserve_usage_and_close_http_errors(self):
        body = io.BytesIO(b"private error details")
        errors = (urllib.error.HTTPError(CODEX_DETAILS_URL, 403, "forbidden", {}, body),
                  urllib.error.URLError("unavailable"), TimeoutError("timed out"),
                  json.JSONDecodeError("invalid JSON", "", 0))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                row = self.fetch_codex(error)[0]
                self.assertEqual(row["manual_resets_available"], 3)
                self.assertIsNone(row["manual_reset_expirations"])
                self.assertEqual(row["used_percent"], 25)
                self.assertIsNone(row["error"])
        self.assertTrue(body.closed)
        usage_body = io.BytesIO(b"private error details")
        row = self.fetch_codex({"available_count": 7, "credits": []},
                               urllib.error.HTTPError(CODEX_USAGE_URL, 401, "unauthorized", {}, usage_body))[0]
        self.assertEqual(row["error"], "http 401")
        self.assertIsNone(row["manual_resets_available"])
        self.assertIsNone(row["manual_reset_expirations"])
        self.assertTrue(usage_body.closed)

    def test_codex_requests_overlap_with_same_auth_and_bounded_workers(self):
        barrier = threading.Barrier(2, timeout=2)
        auth = json.dumps({"tokens": {"access_token": "PRIVATE", "account_id": "ACCOUNT"}})
        request_threads = set()
        requests = []
        lock = threading.Lock()

        def request(url, headers):
            with lock:
                request_threads.add(threading.get_ident())
                requests.append((url, headers))
            barrier.wait()
            return self.codex_usage() if url == CODEX_USAGE_URL else {"available_count": 0, "credits": []}

        with patch.object(Path, "read_text", return_value=auth), \
                patch.object(self.usage, "http_json", side_effect=request):
            rows = self.usage.fetch_wrapper("Codex", self.usage.codex_rows)
        self.assertEqual(len(request_threads), 2)
        self.assertNotIn(threading.get_ident(), request_threads)
        self.assertEqual({url for url, headers in requests}, {CODEX_USAGE_URL, CODEX_DETAILS_URL})
        self.assertTrue(all(headers == {"Authorization": "Bearer PRIVATE", "ChatGPT-Account-ID": "ACCOUNT", "Accept": "application/json"}
                            for url, headers in requests))
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[0]["manual_resets_available"], 0)
        self.assertEqual(rows[0]["manual_reset_expirations"], [])

    def test_http_json_uses_get_and_existing_timeout(self):
        for url in (CODEX_USAGE_URL, CODEX_DETAILS_URL):
            with self.subTest(url=url), patch.object(self.usage.urllib.request, "urlopen", return_value=io.StringIO("{}")) as request:
                self.assertEqual(self.usage.http_json(url, {"Authorization": "Bearer PRIVATE"}), {})
                self.assertEqual(request.call_args.args[0].get_method(), "GET")
                self.assertEqual(request.call_args.kwargs["timeout"], 30)

    def test_terminal_expiry_cells_sorting_unknowns_and_passed_dates(self):
        first, second = "2026-10-22T21:13:00Z", "2026-10-29T19:44:00Z"
        cases = (
            (2, [second, first], "9.9 days", "22.10.26 21:13"),
            (2, [first, first], "9.9 days", "22.10.26 21:13"),
            (3, [second, first], "9.9 days", "22.10.26 21:13 (earliest known)"),
            (1, [second, first], "9.9 days", "22.10.26 21:13 (earliest known)"),
            (2, [None, first], "9.9 days", "22.10.26 21:13 (earliest known)"),
            (None, [first], "9.9 days", "22.10.26 21:13 (earliest known)"),
            (3, [], "?", "?"),
            (3, None, "?", "?"),
            (1, [None], "?", "?"),
            (0, [first], "", ""),
            (2, [second, "2026-10-12T12:00:00Z"], "now", "12.10.26 12:00 (passed; refresh)"),
        )
        for count, dates, countdown, expiry in cases:
            with self.subTest(count=count, dates=dates), patch.object(self.usage, "datetime", FixedDateTime):
                record = {"manual_resets_available": count, "manual_reset_expirations": dates}
                self.assertEqual(self.usage.manual_reset_cells(record),
                                 (f"Manual resets available: {count if count is not None else '?'}", countdown, expiry))

    def test_terminal_expirations_use_prague_daylight_saving_offsets(self):
        with patch.dict(os.environ, TZ="Europe/Prague"), patch.object(self.usage, "datetime", FixedDateTime):
            time.tzset()
            for date, expected in (("2026-10-22T21:13:00Z", "22.10.26 23:13"),
                                   ("2026-10-29T19:44:00Z", "29.10.26 20:44")):
                with self.subTest(date=date):
                    record = {"manual_resets_available": 1, "manual_reset_expirations": [date]}
                    self.assertEqual(self.usage.manual_reset_cells(record)[2], expected)

    def test_terminal_summary_only_follows_final_codex_row_even_if_interleaved(self):
        rows = []
        for provider in ("Codex", "Cursor", "Codex", "Copilot"):
            rows += self.usage.fetch_wrapper(provider, lambda: self.usage.row(provider, 25, "2026-10-20", 18000,
                                                manual_resets_available=1, manual_reset_expirations=["2026-10-22T21:13:00Z"]))
        with patch.object(self.usage, "collect_rows", return_value=rows), patch.object(self.usage, "datetime", FixedDateTime):
            output = self.invoke([])
        self.assertEqual(output.count("Manual resets available:"), 1)
        lines = output.splitlines()
        index = next(index for index, line in enumerate(lines) if "Manual resets available:" in line)
        self.assertTrue(lines[index - 1].startswith("Codex"))
        self.assertTrue(lines[index + 1].startswith("Copilot"))

    def test_copilot_calendar_months_include_leap_years_and_use_utc(self):
        cases = (
            ("2026-11-01", 31),
            ("2026-05-01", 30),
            ("2026-03-01", 28),
            ("2028-03-01", 29),
            ("2027-01-01", 31),
            ("2026-04-01T02:00:00+02:00", 31),
            ("2026-11-01T01:00:00+01:00", 31),
        )
        for zone in ("UTC", "Europe/Prague"):
            with patch.dict(os.environ, TZ=zone):
                time.tzset()
                for reset, days in cases:
                    response = {
                        "quota_snapshots": {"premium_interactions": {"percent_remaining": 80}},
                        "quota_reset_date": reset,
                    }
                    with self.subTest(zone=zone, reset=reset), \
                            patch.object(self.usage, "copilot_token", return_value="PRIVATE"), \
                            patch.object(self.usage, "http_json", return_value=response):
                        rows = self.usage.fetch_wrapper("Copilot", self.usage.copilot_rows)
                        self.assertIsNone(rows[0]["error"])
                        self.assertEqual(rows[0]["used_percent"], 20)
                        self.assertEqual(rows[0]["window_seconds"], days * 86400)

    def test_copilot_unexpected_reset_keeps_usage_with_unknown_window(self):
        for reset in ("2026-10-20", "2026-11-01T00:00:01Z", "2026-11-01T00:00:00+01:00"):
            response = {
                "quota_snapshots": {"premium_interactions": {"percent_remaining": 80}},
                "quota_reset_date": reset,
            }
            with self.subTest(reset=reset), \
                    patch.object(self.usage, "copilot_token", return_value="PRIVATE"), \
                    patch.object(self.usage, "http_json", return_value=response):
                rows = self.usage.fetch_wrapper("Copilot", self.usage.copilot_rows)
                self.assertIsNone(rows[0]["error"])
                self.assertIsNone(rows[0]["window_seconds"])
                self.assertEqual(rows[0]["used_percent"], 20)
                self.assertEqual(datetime.fromisoformat(rows[0]["reset_at"]), self.usage.local_dt(reset))

    def test_antigravity_only_uses_explicit_known_periods(self):
        output = "\n".join([
            *(
                f"Gemini Models\t{kind}\t75%\t2026-10-20T00:00:00Z"
                for kind in ("Weekly Limit Remaining", "Five Hour Limit Remaining", "Limit Remaining", "Monthly Limit Remaining")
            ),
            "Gemini 4 Argon\tGemini 4 Argon Weekly Limit\t75%\t2026-10-20T00:00:00Z",
        ])
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=output)):
            rows = self.usage.fetch_wrapper("Antigravity", self.usage.antigravity_rows)
        self.assertEqual([r["window_seconds"] for r in rows], [604800, 18000, None, None, 604800])
        self.assertEqual([r["label"] for r in rows], [
            "Antigravity (Gemini Week)", "Antigravity (Gemini 5h)",
            "Antigravity (Gemini unknown)", "Antigravity (Gemini Monthly)",
            "Antigravity (Argon Week)",
        ])
        self.assertTrue(all(r["used_percent"] == 25 and r["error"] is None for r in rows))

    def test_unknown_window_retains_usage_and_countdown_in_both_formats(self):
        rows = self.usage.fetch_wrapper("Test", lambda: self.usage.row("Unknown", 25, "2026-10-20", None))
        with patch.object(self.usage, "collect_rows", return_value=rows), patch.object(self.usage, "datetime", FixedDateTime):
            data = json.loads(self.invoke(["--json"]))
            self.assertIsNone(data["rows"][0]["window_seconds"])
            self.assertIsNone(data["rows"][0]["error"])
            actual = self.invoke([])
            self.assertIn("25.0%", actual)
            self.assertIn("7.0/? days", actual)
            self.assertIn("20.10.26", actual)
            self.assertEqual(self.usage.remaining(self.usage.local_dt("2026-10-12"), None), ("now", "", ""))

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
            "-------------  ------  ------------  --------------\n"
            "Cursor (Grok)   12.3%  7.0/ 30 days  20.10.26 00:00\n"
            "Codex (Gpt)    100.0%  0.2/0.2 days  13.10.26 05:00\n"
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
            for key in ("used_percent", "reset_at", "window_seconds", "manual_resets_available", "manual_reset_expirations"):
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
