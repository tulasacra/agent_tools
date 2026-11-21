These rules apply to all repositories and directories under this directory.

# AI commit attribution

Every commit created or amended by an AI agent must end its commit message with
a blank line followed by:

```text
Committed-by: [model]
```

Replace `[model]` with the actual model name or identifier supplied by the current
session. If the model is unavailable, use `unknown model`.

# Personal and secret information screening

Before creating or amending any commit, the agent must screen the proposed
commit, including the commit message, for personal information
and secrets (such as credentials, API tokens, passwords, and private keys).
If any are found, the agent must stop before committing and describe the findings
to the user.
Proceed only after the user explicitly confirms inclusion of the identified information.
