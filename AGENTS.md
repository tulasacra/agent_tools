# AI commit attribution

This rule applies to all repositories and directories under this directory.

Every commit created or amended by an AI agent must end its commit message with
a blank line followed by:

```text
Committed-by: [model]
```

Replace `[model]` with the actual model name or identifier supplied by the current
session. If the model is unavailable, use `unknown model`.
