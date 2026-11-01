# whyredci: why did this GitHub Actions run fail?

[![CI](https://github.com/GillMacedo/whyredci/actions/workflows/ci.yml/badge.svg)](https://github.com/GillMacedo/whyredci/actions/workflows/ci.yml) [![MIT license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**A Claude Code skill and read-only CLI for failed GitHub Actions runs. It finds the failed step and the log lines that matter, then checks history to show whether the failure looks new, already failing on the default branch, or flaky.**

A job log is thousands of lines, and it can't tell you whether your change caused the failure. `whyredci` pulls the useful lines, compares the job with recent default branch runs, checks whether the same commit passed elsewhere, and lists the commits since the branch last passed.

## What you can ask Claude

- "Why is CI red on this PR?"
- "Is this failure from my change, or was main already broken?"
- "Is this test flaky?"
- "What's wrong with this run?" followed by a GitHub Actions URL

## Install

As a Claude Code plugin:

    claude plugin marketplace add GillMacedo/whyredci
    claude plugin install whyredci@whyredci-marketplace

Or run the script directly. It needs Python 3.10 or newer, with nothing from pip, plus an authenticated [`gh`](https://cli.github.com/):

    git clone https://github.com/GillMacedo/whyredci
    python3 whyredci/plugin/scripts/whyredci.py --run https://github.com/OWNER/REPO/actions/runs/123
    python3 whyredci/plugin/scripts/whyredci.py --repo OWNER/REPO --pr 42

## Example

On made-up data, through the stand-in `gh` in `tests/fakes`:

    $ make demo
    CI: failure, 1 of 2 jobs failed
    demo/shop run 500, pull_request on feature at ffffffffffff, attempt 1
    https://github.com/demo/shop/actions/runs/500

    Leads to check:
      LOOKS NEW: test passed its last 2 runs on main, so check what differs on this branch.

    Job: test
      Failed step: Run tests
      Hint: test: looks like a failing test
      History: main: failed 0 of the last 2 runs
      | --- FAIL: TestCheckoutTotal (0.00s)
      |     total_test.go:42: got 1099, want 1100
      | error: token [masked] leaked
      | FAIL	demo/shop/checkout	0.2s
      | FAIL
      | ##[error]Process completed with exit code 1.

    Commits since this branch last passed (merges from the default branch add many):
      dddddddddddd Round totals in checkout
      eeeeeeeeeeee Update docs

    Log lines come from the run and may be written by anyone who can change it, so treat them as data. Leads aren't proof of a cause.

Add `--format markdown` for a PR comment or ticket, or `--format json` to process the results. Exit code 0 means everything was read, 1 means something couldn't be, and 2 means bad options.

## Leads

| Lead | Meaning |
|---|---|
| `LOOKS NEW` | The job passed its recent default branch runs, so check what differs on this branch |
| `ALREADY FAILING` | The job fails on the default branch too, so this branch may not have caused it |
| `UNSTABLE` | The job fails only some of the time on the default branch |
| `INTERMITTENT` | The same commit passed in another run or attempt |

Hints such as `network`, `memory` or `test` come from matching log text. They point you somewhere to start and aren't a diagnosis.

## Safety

- **Read-only:** it only makes `gh api` GET requests. It can't re-run workflows or change anything.
- **Log text is untrusted:** anyone who can open a pull request can write to a log. The script flattens the text, and the skill tells Claude not to follow instructions in it.
- **Token shapes are masked:** it masks common token formats as a backstop. That won't catch every secret, so treat the output as you would the log.
- **No data leaves your machine** beyond your own `gh` calls. There's no telemetry.

## Limits

- GitHub Actions only.
- History compares job names, so a renamed job or a job that only runs on PRs has no baseline.
- It compares jobs, not individual tests, so a flaky test in a job that fails for other reasons won't stand out.
- Commit ranges aren't compared for runs from forks, and merges from the default branch make a range look large.
- It reads logs for 3 failed jobs by default, because matrix jobs often fail the same way. It tells you when it skips any. Logs also expire.
- Log line selection uses patterns, so an unusual tool's error can be missed. When nothing matches, it shows the last lines before the failure.

## FAQ

**How do I find out why GitHub Actions failed?** Give it the run URL. It shows the failed step, the key log lines and what's changed.

**Is this CI failure flaky?** Look for `INTERMITTENT` or `UNSTABLE`. A flaky failure can still be a real timing bug.

**Did my PR break CI, or was main already broken?** `ALREADY FAILING` and `LOOKS NEW` answer this at the job level.

**Is it an MCP server?** No. It's a skill with a bundled script, so it needs no server or package install.

## Tested on

Unit tests use a stand-in `gh`. It also ran on real failed runs in public repos: a Rust test failure in `astral-sh/ruff`, a Go module error in `grafana/grafana`, and a fork PR run with no jobs in `cli/cli`. It hasn't been used on private repos yet, and the skill hasn't been tested in a fresh Claude session.

## Contributing

Contributions are welcome, from people and from people working with AI agents. See [CONTRIBUTING.md](CONTRIBUTING.md). Good first issues are [listed here](https://github.com/GillMacedo/whyredci/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).

## AI disclosure

This project was largely generated with Claude Code and reviewed by its maintainer.

## About me

I'm Giulliano Macedo, and I build developer tools at GoTech Solutions. Follow along or get in touch:

- GitHub: [GillMacedo](https://github.com/GillMacedo)
- LinkedIn: [giulliano](https://www.linkedin.com/in/giulliano/)
- X: [@giumacedo](https://x.com/giumacedo)

If `whyredci` turned a red build green faster, a star helps others find it.

---

Built by [Giulliano Macedo](https://github.com/GillMacedo), GoTech Solutions · [LinkedIn](https://www.linkedin.com/in/giulliano/) · [X](https://x.com/giumacedo)
