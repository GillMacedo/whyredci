# whyredci

Claude Code skill and CLI that explains failed GitHub Actions runs.

## Architecture
`plugin/scripts/whyredci.py` holds all the logic: choose a run, read its jobs and logs, compare history, then render. `plugin/skills/triaging-ci-failures/SKILL.md` tells Claude when and how to run it. See `docs/architecture.md`.

## Commands
- `make test`: unit tests and project checks
- `make lint`: manifest validation
- `make demo`: run on made-up data through `tests/fakes`

## Conventions
- Python 3.10+, standard library only.
- GET requests only. A test checks this.
- Pass all outside text through `clean`.
- Something that can't be read goes in `gaps`, and is never treated as a pass.

## Do and don't
- Do add a test with a real log line when adding a pattern, with anything private removed.
- Don't add network calls of its own or telemetry.
- Don't state a cause. Leads and hints are things to check.

## Definition of done
`make test` and `make lint` pass, and `ROADMAP.md` and docs are updated when relevant.
