# switch

Two console commands for Cursor, GitHub Copilot, and Codex.

`a` stays the Omarchy default (`omarchy-agent --inline`). These replace the `aa` and `aaa` aliases.

| Command | What it does |
| --- | --- |
| `aa` | Pick 1 cursor, 2 copilot, or 3 codex, then launch it |
| `au` | Usage rows: `34% \| 5 days \| 1.11.26` |

`aa cursor` (or `copilot`, `codex`) skips the menu.

Install:

```bash
ln -sfn "$PWD/aa" ~/.local/bin/aa
ln -sfn "$PWD/au" ~/.local/bin/au
```
