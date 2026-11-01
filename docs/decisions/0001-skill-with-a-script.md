# 0001: A skill with one standard-library script

**Decision:** ship a Claude Code skill and one Python script that uses the user's `gh`.

**Why:** installing it needs no server or package, and `gh` already handles sign-in. A script can also gather history in one step, which an agent reading a single log wouldn't look for.

**Alternatives:** an MCP server would be easier to list in registries, but it adds an install and a process to run.
