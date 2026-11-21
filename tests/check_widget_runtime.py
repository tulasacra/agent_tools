"""Exercise the actual widget with fixture commands in an isolated Quickshell.

Requires a running Wayland session and Omarchy. No real provider calls are made.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "omarchy/agent-usage/Widget.qml"
FIXTURE = '''import json, sys, time
from datetime import datetime, timezone
time.sleep(0.3)
mode = sys.argv[1]
if mode == "invalid":
    print("broken JSON")
elif mode == "exit":
    sys.exit(7)
else:
    row = {"provider": "codex", "label": "Codex (Gpt)", "used_percent": 12.5,
           "reset_at": "2030-10-20T00:00:00+00:00", "window_seconds": 18000,
           "manual_resets_available": 3,
           "manual_reset_expirations": ["2030-11-07T00:05:59Z", "2030-10-22T21:13:59.999999Z", "2030-10-29T19:44:01Z"],
           "error": None}
    if mode == "unavailable":
        row.update(used_percent=None, reset_at=None, window_seconds=None,
                   manual_resets_available=None, manual_reset_expirations=None, error="http 401")
    elif mode == "unknown-window":
        row["window_seconds"] = None
    elif mode == "zero-resets":
        row["manual_resets_available"] = 0
        row["manual_reset_expirations"] = []
    elif mode == "legacy":
        del row["manual_resets_available"]
        del row["manual_reset_expirations"]
    elif mode == "invalid-resets":
        row["manual_resets_available"] = "bad count"
    elif mode == "unavailable-expirations":
        row["manual_reset_expirations"] = None
    elif mode == "invalid-expirations":
        row["manual_reset_expirations"] = "bad details"
    elif mode == "partial-expirations":
        row["manual_reset_expirations"] = ["2030-10-22T21:13:23.818918Z", "bad date"]
    print(json.dumps({"generated_at": datetime.now(timezone.utc).isoformat(), "rows": [row]}))
'''

HARNESS = '''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
    id: harness
    property var component: null
    property var widget: null
    property var process: null
    property string currentMode: "success"
    property int starts: 0
    property int generation: 0
    Item { id: host }
    function commandForMode(value) {
        return value === "missing" ? ["/nonexistent/au-runtime-test"]
            : value === "deadline" ? ["/usr/bin/timeout", "--kill-after=0.2", "0.05", PYTHON, FIXTURE_PATH, "success"]
            : [PYTHON, FIXTURE_PATH, value];
    }
    function mountWidget() {
        if (widget) {
            widget.destroy();
            widget = null;
            process = null;
        }
        if (!component) {
            component = Qt.createComponent(PLUGIN_URL);
            if (component.status !== Component.Ready) { console.error(component.errorString()); return; }
        }
        widget = component.createObject(host);
        for (var i = 0; i < widget.data.length; i++) {
            var obj = widget.data[i];
            if (obj.command !== undefined && obj.running !== undefined) process = obj;
            // Keep this test's popup surfaces hidden, even when testing open().
            if (obj.anchorItem !== undefined) obj.visible = false;
        }
        process.started.connect(function() { harness.starts++; });
        process.command = commandForMode(currentMode);
        generation++;
    }
    Component.onCompleted: Qt.callLater(mountWidget)
    IpcHandler {
        target: "au-test"
        function status(): string {
            if (!harness.widget || !harness.process) return '{}';
            var w = harness.widget;
            return JSON.stringify({ready: true, busy: w.busy, rows: w.rows,
                updatedAt: w.updatedAt, error: w.refreshError, opened: w.opened,
                starts: harness.starts, generation: harness.generation});
        }
        function mode(value: string): void {
            harness.currentMode = value;
            if (harness.process) harness.process.command = harness.commandForMode(value);
        }
        function recreate(): void { harness.mountWidget(); }
        function action(value: string): void { harness.widget[value](); }
        function formatExpiry(value: string): string { return harness.widget.formatResetExpiry(value, true); }
        function snapshotAge(milliseconds: string): void {
            harness.widget.updatedAt = new Date(Date.now() - Number(milliseconds)).toISOString();
        }
    }
}
'''


def main():
    with tempfile.TemporaryDirectory(prefix="au-widget-test-") as folder:
        root = Path(folder)
        for name in ("Commons", "Ui"):
            (root / name).symlink_to(Path("/usr/share/omarchy/shell") / name, target_is_directory=True)
        fixture = root / "fixture.py"
        fixture.write_text(FIXTURE)
        qml = HARNESS.replace("PLUGIN_URL", json.dumps(PLUGIN.as_uri()))
        qml = qml.replace("PYTHON", json.dumps(sys.executable)).replace("FIXTURE_PATH", json.dumps(str(fixture)))
        (root / "shell.qml").write_text(qml)

        def ipc(method, *args):
            result = subprocess.run(["quickshell", "ipc", "-p", folder, "call", "au-test", method, *args],
                                    capture_output=True, text=True, timeout=3)
            return result.stdout if result.returncode == 0 else "{}"

        def wait_for(predicate):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = json.loads(ipc("status"))
                if state.get("ready") and predicate(state):
                    return state
                time.sleep(0.02)
            raise AssertionError(f"Widget did not reach expected state: {state}")

        with (root / "quickshell.log").open("w+") as log:
            state_home = root / "state"
            state_file = state_home / "agent-usage" / "snapshot.json"
            process = subprocess.Popen(["quickshell", "-p", folder, "--no-color"], stdout=log, stderr=log,
                                       env={**os.environ, "TZ": "Europe/Prague", "XDG_STATE_HOME": str(state_home)})
            try:
                initial = wait_for(lambda state: not state["busy"])
                assert initial["starts"] == 0 and initial["rows"] == []
                for value, expected in (
                    ("2026-10-22T21:13:23.818Z", "22.10.26 23:13 CEST"),
                    ("2026-10-29T19:44:01.802Z", "29.10.26 20:44 CET"),
                    ("2026-11-07T00:05:59.667Z", "07.11.26 01:05 CET"),
                ):
                    assert ipc("formatExpiry", value).strip() == expected
                print("PASS: actual widget formats expiry dates in local time across Prague's DST change")

                ipc("action", "open")
                ipc("action", "refresh")
                ipc("action", "refresh")
                ipc("action", "close")
                good = wait_for(lambda state: not state["busy"] and state["starts"] == 1)
                assert good["rows"][0]["used_percent"] == 12.5 and not good["error"]
                assert good["rows"][0]["manual_resets_available"] == 3
                assert good["rows"][0]["manual_reset_expirations"] == [
                    "2030-10-22T21:13:59.999Z", "2030-10-29T19:44:01.000Z", "2030-11-07T00:05:59.000Z",
                ], good["rows"][0]["manual_reset_expirations"]
                assert not good["opened"]
                time.sleep(1.1)
                assert json.loads(ipc("status"))["starts"] == 1
                print("PASS: on-open refresh, single in-flight command, result retained after closing, no closed polling")

                for age in (0, 59000):
                    ipc("snapshotAge", str(age))
                    ipc("action", "open")
                    state = wait_for(lambda state: state["opened"])
                    assert not state["busy"] and state["starts"] == 1
                    assert state["rows"] == good["rows"]
                    ipc("action", "close")
                print("PASS: reopening within one minute reuses the snapshot")

                ipc("action", "refresh")
                good = wait_for(lambda state: not state["busy"] and state["starts"] == 2)
                assert good["rows"][0]["used_percent"] == 12.5 and not good["error"]
                print("PASS: manual refresh bypasses the one-minute cache")

                for mode, expected in (("invalid", "invalid JSON"), ("exit", "exit 7"),
                                       ("missing", "Could not start"), ("deadline", "timed out")):
                    ipc("mode", mode)
                    ipc("action", "refresh")
                    state = wait_for(lambda state: not state["busy"] and state["error"])
                    assert expected in state["error"], state
                    assert state["rows"] == good["rows"] and state["updatedAt"] == good["updatedAt"]
                    print(f"PASS: {mode} command keeps the previous snapshot and reports its failure")

                ipc("mode", "unavailable")
                ipc("action", "refresh")
                state = wait_for(lambda state: not state["busy"])
                assert state["rows"][0]["used_percent"] is None
                assert state["rows"][0]["error"] == "http 401" and not state["error"]
                print("PASS: unavailable provider is an error row, not zero usage")

                ipc("mode", "unknown-window")
                ipc("action", "refresh")
                state = wait_for(lambda state: not state["busy"])
                assert state["rows"][0]["used_percent"] == 12.5
                assert state["rows"][0]["window_seconds"] is None
                assert state["rows"][0]["error"] is None and not state["error"]
                print("PASS: unknown window preserves quota data without rejecting the snapshot")

                for mode, expected in (("zero-resets", 0), ("legacy", None), ("invalid-resets", None)):
                    ipc("mode", mode)
                    ipc("action", "refresh")
                    state = wait_for(lambda state: not state["busy"])
                    assert state["rows"][0]["manual_resets_available"] == expected
                    assert state["rows"][0]["used_percent"] == 12.5 and not state["error"]
                print("PASS: zero, legacy, and invalid reset balances preserve quota data")

                for mode, expected in (("unavailable-expirations", None), ("invalid-expirations", None),
                                       ("partial-expirations", ["2030-10-22T21:13:23.818Z", None])):
                    ipc("mode", mode)
                    ipc("action", "refresh")
                    state = wait_for(lambda state: not state["busy"])
                    assert state["rows"][0]["manual_reset_expirations"] == expected
                    assert state["rows"][0]["manual_resets_available"] == 3
                    assert state["rows"][0]["used_percent"] == 12.5 and not state["error"]
                print("PASS: unavailable, malformed, and partial expiry details preserve balances and quotas")

                ipc("mode", "success")
                ipc("snapshotAge", "60000")
                previous_starts = state["starts"]
                ipc("action", "open")
                state = wait_for(lambda state: not state["busy"] and state["starts"] == previous_starts + 1)
                assert state["rows"][0]["used_percent"] == 12.5 and not state["error"]
                assert state["rows"][0]["window_seconds"] == 18000
                assert state["opened"]
                time.sleep(1.1)
                assert json.loads(ipc("status"))["starts"] == state["starts"]
                ipc("action", "close")
                print("PASS: reopening after one minute refreshes, with no periodic fetching while open")

                def wait_for_saved(expected_updated_at):
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        if state_file.exists():
                            try:
                                saved = json.loads(state_file.read_text())
                                if saved.get("generated_at") == expected_updated_at:
                                    return saved
                            except json.JSONDecodeError:
                                pass
                        time.sleep(0.02)
                    raise AssertionError(f"Snapshot file did not record {expected_updated_at}")

                saved = wait_for_saved(state["updatedAt"])
                assert len(saved["rows"]) == 1 and saved["rows"][0]["used_percent"] == 12.5
                print("PASS: snapshot is persisted to XDG_STATE_HOME/agent-usage/snapshot.json")

                previous_generation = state["generation"]
                previous_starts = state["starts"]
                ipc("recreate")
                recreated = wait_for(lambda s: s["generation"] == previous_generation + 1)
                assert recreated["starts"] == previous_starts and not recreated["busy"] and not recreated["error"]
                assert recreated["updatedAt"] == state["updatedAt"]
                assert recreated["rows"] == state["rows"]
                print("PASS: recreated widget loads the persisted snapshot before opening")

                ipc("snapshotAge", "60000")
                ipc("action", "open")
                in_flight = wait_for(lambda s: s["opened"] and s["busy"])
                assert in_flight["rows"] == state["rows"]
                refreshed = wait_for(lambda s: not s["busy"] and s["starts"] == previous_starts + 1)
                assert refreshed["rows"][0]["used_percent"] == 12.5 and not refreshed["error"]
                wait_for_saved(refreshed["updatedAt"])
                ipc("action", "close")
                print("PASS: recreated widget keeps prefilled rows visible while refreshing a stale snapshot")

                state_file.write_text("not valid json")
                previous_generation = refreshed["generation"]
                ipc("recreate")
                corrupt = wait_for(lambda s: s["generation"] == previous_generation + 1)
                assert corrupt["rows"] == [] and corrupt["updatedAt"] == "" and corrupt["error"] == ""
                print("PASS: invalid persisted state falls back cleanly to an empty snapshot")
            except BaseException:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
