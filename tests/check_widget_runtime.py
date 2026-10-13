"""Exercise the actual widget with fixture commands in an isolated Quickshell.

Requires a running Wayland session and Omarchy. No real provider calls are made.
"""

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1] / "omarchy/agent-usage/Widget.qml"
FIXTURE = '''import json, sys, time
time.sleep(0.3)
mode = sys.argv[1]
if mode == "invalid":
    print("broken JSON")
elif mode == "exit":
    sys.exit(7)
else:
    row = {"provider": "codex", "label": "Codex (Gpt)", "used_percent": 12.5,
           "reset_at": "2030-10-20T00:00:00+00:00", "window_seconds": 18000, "error": None}
    if mode == "unavailable":
        row.update(used_percent=None, reset_at=None, window_seconds=None, error="http 401")
    elif mode == "unknown-window":
        row["window_seconds"] = None
    print(json.dumps({"generated_at": "2030-10-13T00:00:00+00:00", "rows": [row]}))
'''

HARNESS = '''import QtQuick
import Quickshell
import Quickshell.Io
ShellRoot {
    id: harness
    property var widget: null
    property var process: null
    property int starts: 0
    Item { id: host }
    Component.onCompleted: Qt.callLater(function() {
        var component = Qt.createComponent(PLUGIN_URL);
        if (component.status !== Component.Ready) { console.error(component.errorString()); return; }
        widget = component.createObject(host);
        for (var i = 0; i < widget.data.length; i++) {
            var obj = widget.data[i];
            if (obj.command !== undefined && obj.running !== undefined) process = obj;
            // Keep this test's popup surfaces hidden, even when testing open().
            if (obj.anchorItem !== undefined) obj.visible = false;
        }
        process.started.connect(function() { harness.starts++; });
        process.command = [PYTHON, FIXTURE_PATH, "success"];
    })
    IpcHandler {
        target: "au-test"
        function status(): string {
            if (!harness.widget || !harness.process) return '{}';
            var w = harness.widget;
            return JSON.stringify({ready: true, busy: w.busy, rows: w.rows,
                updatedAt: w.updatedAt, error: w.refreshError, opened: w.opened, starts: harness.starts});
        }
        function mode(value: string): void {
            harness.process.command = value === "missing" ? ["/nonexistent/au-runtime-test"]
                : value === "deadline" ? ["/usr/bin/timeout", "--kill-after=0.2", "0.05", PYTHON, FIXTURE_PATH, "success"]
                : [PYTHON, FIXTURE_PATH, value];
        }
        function action(value: string): void { harness.widget[value](); }
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
            process = subprocess.Popen(["quickshell", "-p", folder, "--no-color"], stdout=log, stderr=log)
            try:
                initial = wait_for(lambda state: not state["busy"])
                assert initial["starts"] == 0 and initial["rows"] == []

                ipc("action", "open")
                ipc("action", "refresh")
                ipc("action", "refresh")
                ipc("action", "close")
                good = wait_for(lambda state: not state["busy"] and state["starts"] == 1)
                assert good["rows"][0]["used_percent"] == 12.5 and not good["error"]
                assert not good["opened"]
                time.sleep(1.1)
                assert json.loads(ipc("status"))["starts"] == 1
                print("PASS: on-open refresh, single in-flight command, result retained after closing, no closed polling")

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

                ipc("mode", "success")
                ipc("action", "open")
                state = wait_for(lambda state: not state["busy"])
                assert state["rows"][0]["used_percent"] == 12.5 and not state["error"]
                assert state["opened"]
                time.sleep(1.1)
                assert json.loads(ipc("status"))["starts"] == state["starts"]
                ipc("action", "close")
                print("PASS: reopening refreshes and recovers, with no periodic fetching while open")
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
