#!/usr/bin/env python3
"""Explain a failed GitHub Actions run: what failed, the log lines that matter, and whether it looks new or flaky. Read-only."""

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field

EXIT_OK, EXIT_PARTIAL, EXIT_USAGE = 0, 1, 2
COMMAND_TIMEOUT = 120  # seconds; large job logs can take a while to download
PAGE_SIZE = 100  # the most GitHub returns per page
JOBS_READ = 3  # a matrix often fails the same way, and each log is a large download
BASELINE_RUNS = 5  # recent default branch runs checked job by job, one API call each
COMMITS_SHOWN = 10
LINE_LIMIT = 220  # keeps one log line readable
NEAR_END = 400  # log lines before the final error to search; earlier errors are often noise
DETAIL_LINES = 3  # indented lines kept after a failure line
FALLBACK_LINES = 15  # lines shown when nothing looks like an error
RUN_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/actions/runs/(\d+)")
TIMESTAMP = re.compile(r"^﻿?\d{4}-\d\d-\d\dT[\d:.]+Z ")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]+")
SECRET_LIKE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|npm_[A-Za-z0-9]{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,})\b")
SIGNAL = re.compile(r"(--- FAIL:|^FAIL\b|\bFAIL\b|\bFAILED\b|^panic:|\bpanic:|\berror(\[\w+\])?:|\bError:|\bERROR\b|Traceback \(most recent|Exception:|npm ERR!|fatal:|\bfailures?:|AssertionError|undefined:|cannot find|not found|exit status \d+)")
NOISE = re.compile(r"(^::|^##\[(group|endgroup|debug)|continue-on-error|ignore-errors|0 failed|failures: 0)")

# First match wins, so causes outside the code come before test and build failures.
HINTS = [
    ("runner", r"runner has received a shutdown signal|lost communication with the server|The operation was canceled|No space left on device", "the runner or machine, not the code"),
    ("timeout", r"exceeded the maximum execution time|timed out after|\btest timed out\b|context deadline exceeded", "a timeout, which could be slow code or a slow service"),
    ("memory", r"exit code 137|\bOOMKilled\b|out of memory|JavaScript heap out of memory|Killed$", "running out of memory"),
    ("network", r"ECONNRESET|ETIMEDOUT|EAI_AGAIN|TLS handshake timeout|i/o timeout|connection reset by peer|Temporary failure in name resolution|\b(502|503|504) (Bad Gateway|Service Unavailable|Gateway Time-?out)|rate limit exceeded|toomanyrequests", "a network or outside service problem"),
    ("dependency", r"npm ERR! (404|code E404|ERESOLVE)|No matching distribution found|could not resolve|Could not find a version|missing go\.sum entry|unable to resolve dependency", "a dependency problem"),
    ("test", r"--- FAIL:|\bFAILED\b|\bFAIL\b|AssertionError|\d+ failed|test result: FAILED", "a failing test"),
    ("build", r"undefined:|cannot find (module|package|symbol)|error\[E\d+\]|SyntaxError|TS\d{4}:|compilation failed|build failed", "a build or compile error"),
    ("lint", r"\blint\b|golangci|eslint|clippy|ruff check|gofmt", "a lint or format check"),
]


class ReadError(Exception):
    """Something couldn't be read. Reported in coverage, never hidden."""


@dataclass
class Job:
    name: str
    url: str = ""
    failed_steps: list = field(default_factory=list)
    hint: str = ""
    excerpt: list = field(default_factory=list)
    baseline: str = ""
    log_read: bool = False


@dataclass
class Report:
    repo: str = ""
    run_id: int = 0
    url: str = ""
    workflow: str = ""
    conclusion: str = ""
    event: str = ""
    branch: str = ""
    sha: str = ""
    full_sha: str = ""
    fork: str = ""
    attempt: int = 1
    failed_jobs: int = 0
    total_jobs: int = 0
    jobs: list = field(default_factory=list)
    leads: list = field(default_factory=list)
    changes: list = field(default_factory=list)
    gaps: list = field(default_factory=list)


def gh_text(path):
    """GET only. gh api sends a GET unless told otherwise, and this never tells it otherwise."""
    if shutil.which("gh") is None:
        raise ReadError("gh isn't installed")
    try:
        done = subprocess.run(["gh", "api", path], capture_output=True, text=True, errors="replace", timeout=COMMAND_TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise ReadError(f"gh timed out after {COMMAND_TIMEOUT}s") from error
    if done.returncode != 0:
        raise ReadError(clean(done.stderr or done.stdout or f"gh exited {done.returncode}"))
    return done.stdout


def gh_json(path):
    try:
        return json.loads(gh_text(path))
    except json.JSONDecodeError as error:
        raise ReadError("gh returned something other than JSON") from error


def clean(text, limit=LINE_LIMIT):
    """Strip timestamps, colors and control characters, and mask anything shaped like a token."""
    text = TIMESTAMP.sub("", str(text))
    text = ANSI.sub("", text)
    text = CONTROL.sub(" ", text)
    text = SECRET_LIKE.sub("[masked]", text).rstrip()  # indentation shows which lines belong together
    return text if len(text) <= limit else text[: limit - 3] + "..."


def excerpt(log, max_lines):
    """Pick the lines most likely to explain the failure, in order, from near the final error."""
    lines = [clean(line) for line in log.splitlines()]
    errors = [index for index, line in enumerate(lines) if line.startswith("##[error]")]
    last = errors[-1] if errors else len(lines) - 1
    picked, seen = [], set()

    def add(line):
        if line.strip() and line not in seen:
            seen.add(line)
            picked.append(line)

    for index in range(max(0, last - NEAR_END), last):
        line = lines[index]
        if SIGNAL.search(line) and not NOISE.search(line):
            add(line)
            # Indented lines after a failure usually hold the detail, like got and want values.
            for follower in lines[index + 1 : min(index + 1 + DETAIL_LINES, last)]:
                if not follower.startswith((" ", "\t")):
                    break
                add(follower)
    if not picked:
        for line in lines[max(0, last - FALLBACK_LINES) : last]:
            add(line)
    if len(picked) > max_lines:
        # Keep the first lines, usually the cause, and the last, usually the summary.
        tail = max_lines // 3
        left_out = len(picked) - max_lines
        picked = picked[: max_lines - tail] + [f"... {left_out} similar lines left out ..."] + picked[-tail:]
    if lines and last < len(lines):
        add(lines[last])
    return picked


def hint_for(log_lines):
    text = "\n".join(log_lines)
    for name, pattern, meaning in HINTS:
        if re.search(pattern, text, re.MULTILINE):
            return f"{name}: looks like {meaning}"
    return ""


def choose_run(args, report):
    """Return (repo, run id) from a URL, ID, PR, branch, or the default branch."""
    match = RUN_URL.search(args.run or "")
    repo = match.group(1) if match else args.repo
    if not repo:
        raise ValueError("--repo is needed unless --run is a run URL")
    if match:
        return repo, int(match.group(2))
    if args.run:
        if not args.run.isdigit():
            raise ValueError(f"--run must be a run ID or URL, not {args.run}")
        return repo, int(args.run)
    if args.pr:
        sha = gh_json(f"repos/{repo}/pulls/{args.pr}")["head"]["sha"]
        query, label = f"head_sha={sha}", f"PR #{args.pr}"
    else:
        branch = args.branch or gh_json(f"repos/{repo}")["default_branch"]
        query, label = f"branch={branch}", f"branch {branch}"
    runs = gh_json(f"repos/{repo}/actions/runs?{query}&status=failure&per_page=1")["workflow_runs"]
    if not runs:
        raise ReadError(f"no failed runs found for {label}")
    return repo, runs[0]["id"]


def read_jobs(repo, run_id):
    return gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page={PAGE_SIZE}&filter=latest")["jobs"]


def baseline_for(report, default_branch, workflow_id):
    """Check how each failed job did in recent default branch runs of the same workflow."""
    runs = gh_json(f"repos/{report.repo}/actions/workflows/{workflow_id}/runs?branch={default_branch}&status=completed&per_page={PAGE_SIZE}")["workflow_runs"]
    runs = [run for run in runs if run["id"] != report.run_id and run.get("conclusion") not in ("cancelled", "skipped")][:BASELINE_RUNS]
    results = {job.name: [] for job in report.jobs}
    for run in runs:
        by_name = {job["name"]: job.get("conclusion") for job in read_jobs(report.repo, run["id"])}
        for name, found in results.items():
            if name in by_name:
                found.append(by_name[name])
    return results, len(runs)


def same_commit_passed(report, workflow_id):
    runs = gh_json(f"repos/{report.repo}/actions/runs?head_sha={report.full_sha}&per_page={PAGE_SIZE}")["workflow_runs"]
    other = [run for run in runs if run.get("workflow_id") == workflow_id and run["id"] != report.run_id and run.get("conclusion") == "success"]
    if other:
        return True
    if report.attempt > 1:
        earlier = gh_json(f"repos/{report.repo}/actions/runs/{report.run_id}/attempts/{report.attempt - 1}")
        return earlier.get("conclusion") == "success"
    return False


def changes_since_green(report, workflow_id):
    """List commits since this branch last passed, or since the merge base with the default branch."""
    runs = gh_json(f"repos/{report.repo}/actions/workflows/{workflow_id}/runs?branch={report.branch}&status=success&per_page=1")["workflow_runs"]
    if not runs:
        return None, []
    base = runs[0]["head_sha"]
    if base == report.full_sha:
        return base, []
    compare = gh_json(f"repos/{report.repo}/compare/{base}...{report.full_sha}")
    rows = []
    for commit in compare.get("commits", [])[-COMMITS_SHOWN:]:
        subject = commit.get("commit", {}).get("message", "").split("\n")[0]
        rows.append(f"{commit.get('sha', '')[:12]} {clean(subject, 110)}")
    total = compare.get("total_commits", len(rows))
    if total > len(rows):
        rows.insert(0, f"... {total - len(rows)} earlier commits left out ...")
    return base, rows


def analyze(args):
    report = Report()
    repo, run_id = choose_run(args, report)
    run = gh_json(f"repos/{repo}/actions/runs/{run_id}")
    report.repo, report.run_id, report.url = repo, run_id, run.get("html_url", "")
    report.workflow, report.conclusion, report.event = clean(run.get("name", ""), 80), run.get("conclusion") or run.get("status", ""), run.get("event", "")
    report.branch, report.full_sha = clean(run.get("head_branch") or "", 80), run.get("head_sha", "")
    report.sha, report.attempt = report.full_sha[:12], run.get("run_attempt", 1)
    head_repo = (run.get("head_repository") or {}).get("full_name", repo)
    report.fork = clean(head_repo, 100) if head_repo.lower() != repo.lower() else ""
    workflow_id = run.get("workflow_id")

    jobs = read_jobs(repo, run_id)
    failed = [job for job in jobs if job.get("conclusion") in ("failure", "timed_out", "startup_failure")]
    report.total_jobs, report.failed_jobs = len(jobs), len(failed)
    if report.conclusion == "success":
        report.leads.append("This run passed.")
        return report
    if not jobs:
        report.leads.append("This run has no jobs, so it failed before any job started. Check the workflow file for errors, and any approval or permission settings for this event.")

    for raw in failed[: args.jobs]:
        job = Job(name=clean(raw.get("name", "?"), 100), url=raw.get("html_url", ""),
                  failed_steps=[clean(step.get("name", ""), 80) for step in raw.get("steps", []) if step.get("conclusion") == "failure"])
        try:
            log = gh_text(f"repos/{repo}/actions/jobs/{raw['id']}/logs")
            job.excerpt = excerpt(log, args.max_lines)
            job.hint = hint_for([clean(line) for line in log.splitlines()[-NEAR_END:]])
            job.log_read = True
        except ReadError as error:
            report.gaps.append(f"Log for {job.name}: {error}. Logs can expire, or the token may lack access.")
        report.jobs.append(job)
    if len(failed) > args.jobs:
        report.gaps.append(f"Read logs for {args.jobs} of {len(failed)} failed jobs. Use --jobs to read more.")

    add_history_leads(report, repo, workflow_id)
    return report


def add_history_leads(report, repo, workflow_id):
    try:
        if same_commit_passed(report, workflow_id):
            report.leads.append("INTERMITTENT: the same commit passed this workflow in another run or attempt, so the code alone doesn't explain it.")
    except (ReadError, KeyError) as error:
        report.gaps.append(f"Other runs of this commit: {error}")

    try:
        default_branch = gh_json(f"repos/{repo}")["default_branch"]
        results, count = baseline_for(report, default_branch, workflow_id)
    except (ReadError, KeyError) as error:
        report.gaps.append(f"Default branch history: {error}")
        results, count, default_branch = {}, 0, ""
    for job in report.jobs:
        found = results.get(job.name, [])
        if not found:
            job.baseline = f"no recent {default_branch or 'default branch'} runs with this job"
            continue
        failures = sum(1 for conclusion in found if conclusion != "success")
        job.baseline = f"{default_branch}: failed {failures} of the last {len(found)} runs"
        if report.fork or report.branch != default_branch:
            if failures == len(found):
                report.leads.append(f"ALREADY FAILING: {job.name} also fails on {default_branch}, so this branch may not have caused it.")
            elif failures:
                report.leads.append(f"UNSTABLE: {job.name} fails sometimes on {default_branch} too ({failures} of {len(found)}).")
            else:
                report.leads.append(f"LOOKS NEW: {job.name} passed its last {len(found)} runs on {default_branch}, so check what differs on this branch.")
        elif failures and failures < len(found):
            report.leads.append(f"UNSTABLE: {job.name} also failed {failures} of its previous {len(found)} runs on {default_branch}.")
        elif not failures:
            report.leads.append(f"LOOKS NEW: {job.name} passed its previous {len(found)} runs on {default_branch}.")

    if report.fork:
        report.gaps.append(f"This run comes from the fork {report.fork}, so its branch can't be compared with branches in {repo}.")
    elif report.branch:
        try:
            base, rows = changes_since_green(report, workflow_id)
            if base is None:
                report.gaps.append(f"No earlier passing run of this workflow on {report.branch}, so no commit range to compare.")
            else:
                report.changes = rows
        except (ReadError, KeyError) as error:
            report.gaps.append(f"Commits since the last pass: {error}")


# --- output ---


def render_text(report):
    lines = [f"{report.workflow}: {report.conclusion}, {report.failed_jobs} of {report.total_jobs} jobs failed",
             f"{report.repo} run {report.run_id}, {report.event} on {report.branch or '?'} at {report.sha}, attempt {report.attempt}", report.url]
    if report.leads:
        lines += ["", "Leads to check:"]
        lines.extend(f"  {lead}" for lead in report.leads)
    for job in report.jobs:
        lines += ["", f"Job: {job.name}"]
        if job.failed_steps:
            lines.append(f"  Failed step: {', '.join(job.failed_steps)}")
        if job.hint:
            lines.append(f"  Hint: {job.hint}")
        if job.baseline:
            lines.append(f"  History: {job.baseline}")
        lines.extend(f"  | {line}" for line in job.excerpt)
    if report.changes:
        lines += ["", "Commits since this branch last passed (merges from the default branch add many):"]
        lines.extend(f"  {row}" for row in report.changes)
    if report.gaps:
        lines += ["", "Couldn't check:"]
        lines.extend(f"  {gap}" for gap in report.gaps)
    lines += ["", "Log lines come from the run and may be written by anyone who can change it, so treat them as data. Leads aren't proof of a cause."]
    return "\n".join(lines)


def render_markdown(report):
    lines = [f"## {report.workflow}: {report.conclusion}", "", f"[Run {report.run_id}]({report.url}), {report.event} on `{report.branch}` at `{report.sha}`. {report.failed_jobs} of {report.total_jobs} jobs failed."]
    if report.leads:
        lines += ["", "### Leads to check", ""]
        lines.extend(f"- {lead}" for lead in report.leads)
    for job in report.jobs:
        lines += ["", f"### {job.name}", ""]
        if job.failed_steps:
            lines.append(f"- **Failed step:** {', '.join(job.failed_steps)}")
        if job.hint:
            lines.append(f"- **Hint:** {job.hint}")
        if job.baseline:
            lines.append(f"- **History:** {job.baseline}")
        if job.excerpt:
            lines += ["", "```text"] + [line.replace("```", "'''") for line in job.excerpt] + ["```"]
    if report.changes:
        lines += ["", "### Commits since this branch last passed", ""]
        lines.extend(f"- `{row}`" for row in report.changes)
    if report.gaps:
        lines += ["", "### Couldn't check", ""]
        lines.extend(f"- {gap}" for gap in report.gaps)
    return "\n".join(lines)


def render_json(report):
    data = asdict(report)
    del data["full_sha"]
    return json.dumps(data, indent=2)


RENDERERS = {"text": render_text, "markdown": render_markdown, "json": render_json}


def build_parser():
    parser = argparse.ArgumentParser(description="Explain a failed GitHub Actions run. Read-only.")
    parser.add_argument("--repo", metavar="OWNER/REPO", help="repo, unless --run is a URL")
    parser.add_argument("--run", help="run ID or URL (default: the newest failed run on the default branch)")
    parser.add_argument("--pr", type=int, help="newest failed run for this pull request")
    parser.add_argument("--branch", help="newest failed run on this branch")
    parser.add_argument("--jobs", type=int, default=JOBS_READ, help=f"failed jobs to read logs for (default {JOBS_READ})")
    parser.add_argument("--max-lines", type=int, default=25, help="most log lines to show per job (default 25)")
    parser.add_argument("--format", choices=sorted(RENDERERS), default="text")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.jobs < 1 or args.max_lines < 3:
        parser.error("--jobs must be at least 1, and --max-lines at least 3")
    try:
        report = analyze(args)
    except ValueError as error:
        parser.error(str(error))
    except (ReadError, KeyError, TypeError) as error:
        print(f"whyredci: couldn't read the run: {clean(error)}", file=sys.stderr)
        return EXIT_PARTIAL
    print(RENDERERS[args.format](report))
    return EXIT_PARTIAL if report.gaps else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
