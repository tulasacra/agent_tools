# agent_tools

Console commands and an Omarchy widget for Cursor, GitHub Copilot, Codex, and Antigravity.

| File | Command | What it does |
| --- | --- | --- |
| `agent_launch` | `aa` | Pick an agent and launch it |
| `agent_usage` | `au` | Show how much of each agent's quota is used |

Install:

```bash
ln -sfn "$PWD/agent_launch" ~/.local/bin/aa
ln -sfn "$PWD/agent_usage" ~/.local/bin/au
```

`au` prints the quota table. `au --json` prints a single snapshot with
`generated_at` (UTC ISO 8601) and `rows`. Each row contains `provider`, `label`,
`used_percent` (0–100 scale, possibly higher for overage), `reset_at` (UTC ISO
8601), `window_seconds` (null when unknown), and `error`. Successful rows have `error: null`;
failed rows have an error message and null quota fields. Provider failures
are reported in the data, with exit status 0, so partial results remain usable.
HTTP requests and subprocess calls have 30-second timeouts.

## Omarchy popup

The **AU** bar button opens all quotas together. Opening the popup or clicking
**Refresh** fetches fresh data; **R** and **Enter** also refresh, and **Esc**
closes it. There is no periodic fetching. The latest snapshot stays in memory
between openings, reset countdowns update while visible, and dates use the
desktop's local timezone. An in-progress fetch can finish after closing.
Only one fetch runs per widget instance, with a 65-second overall timeout
(and a five-second kill grace period).

Usage colors estimate when the quota will run out, using average consumption
since the current window began. **Red** means projected exhaustion by the
first quarter of the full window, **orange** by halfway, and **yellow** by
three quarters; later projections use normal text. Already exhausted quotas
are red. Hover over a percentage for the projection.

The estimate is `elapsed fraction of window / used fraction of quota`, using
the last snapshot's timestamp. For example, 50% used after 10% of the window
projects exhaustion at 20% and shows red. Cached readings keep their pace
until refreshed; a passed reset clears the old warning. Unknown pace and zero
usage stay neutral. Window lengths use the data each provider supplies:

- Cursor: the difference between the reported billing-cycle start and end.
- Codex: the reported `limit_window_seconds` for each quota.
- Copilot: the calendar month ending at the reported reset (28–31 days), following
  [GitHub's premium-quota reset rule](https://docs.github.com/en/copilot/reference/copilot-billing/request-based-billing-legacy/monitor-premium-requests).
  Resets outside the first of a month at midnight UTC leave the length unknown.
- Antigravity: the explicit “Weekly” or “Five Hour” label in `agy /usage`.
  Unrecognized labels leave the length unknown instead of defaulting to five hours.

Unknown window lengths appear as **—** in the popup and **?** in the terminal.
Usage and reset countdowns still work, but pace projections are unavailable;
an exhausted quota remains red until its reset passes.

Requires Omarchy's Quickshell plugin system (tested with 4.0.4), Python 3,
coreutils `timeout`, and the same provider logins/tools as `au`. Install `au`
at `~/.local/bin/au` first using the commands above.

From this repository root, install or update the widget:

```bash
omarchy plugin validate omarchy/agent-usage
cp -a ~/.config/omarchy/shell.json ~/.config/omarchy/shell.json.bak.au.$(date +%Y%m%d-%H%M%S)
mkdir -p ~/.config/omarchy/plugins/local.agent-usage
cp omarchy/agent-usage/{manifest.json,Widget.qml,Model.js} ~/.config/omarchy/plugins/local.agent-usage/
omarchy-shell shell rescanPlugins
omarchy plugin enable local.agent-usage --section right --index 0
```

This places AU first in the right-hand section. Install ordinary copies: Omarchy's plugin
validator rejects symlinks inside plugin folders. Source changes need to be
copied again; the installed copies hot-reload. Omarchy's packaged files do
not need changes. If an update still shows the previous behavior after a
rescan, run `omarchy restart shell` to clear cached QML components.

Disable with `omarchy plugin disable local.agent-usage`. Remove it with
`omarchy plugin remove local.agent-usage --yes`. This keeps the `au` command
installed. Opening by IPC is also supported:

```bash
omarchy-shell shell summon local.agent-usage '{}'
```

## Checks

The fixture tests do not contact providers or read real credentials. Node.js
is only needed to run the popup's JavaScript model tests.

```bash
python3 -m unittest discover -s tests -v
node --test tests/test_usage_model.cjs
omarchy plugin validate omarchy/agent-usage
```

With an active Omarchy Wayland session, `python3 tests/check_widget_runtime.py`
also checks the widget's actual process lifecycle, failed commands, recovery,
and refresh behavior using fixture commands in an isolated, hidden shell.
