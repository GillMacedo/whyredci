# Architecture

    choose_run --> run + jobs --> excerpt and hint_for each failed job
    add_history_leads --> same commit, default branch baseline, commits since the last pass
    render_* --> text, Markdown or JSON

- **`excerpt`** searches near the final error, keeps indented detail after failure lines, and falls back to the last lines when nothing matches.
- **Leads** come from history, so they don't depend on log patterns.
- **Jobs** are paginated for the selected run and baseline runs, with a 10-page (1,000-job) limit. A failed page or limit breach reports an incomplete read; baseline failures become coverage gaps.
- **`gaps`** lists what couldn't be read, and sets exit code 1.

Key choice: a skill with one standard-library script, not an MCP server. See `decisions/`.
