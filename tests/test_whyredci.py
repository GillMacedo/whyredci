import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
import whyredci as w  # noqa: E402

FAKES = str(ROOT / "tests" / "fakes")
RUN = ["--repo", "demo/shop", "--run", "500"]


def run_cli(*args, scenario="base", env=None):
    out, err = io.StringIO(), io.StringIO()
    values = {"PATH": FAKES + os.pathsep + os.environ["PATH"], "FAKE_SCENARIO": scenario}
    values.update(env or {})
    with mock.patch.dict(os.environ, values), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = w.main(list(args))
    return code, out.getvalue(), err.getvalue()


def report_for(*args, scenario="base", env=None):
    code, out, _ = run_cli(*args, "--format", "json", scenario=scenario, env=env)
    return code, json.loads(out)


class CleanTest(unittest.TestCase):
    def test_strips_timestamp_color_and_control(self):
        self.assertEqual(w.clean("﻿2026-11-01T14:00:00.0000000Z \x1b[31mboom\x1b[0m\x07"), "boom")

    def test_masks_token_shapes(self):
        # Built from pieces so nothing token-shaped is stored in the repo.
        tail = "abcdefghijklmnopqrstuvwxyz1234"
        for token in ("gh" + "p_" + tail, "github" + "_pat_" + tail, "np" + "m_" + tail, "AK" + "IA" + "ABCDEFGHIJKLMNOP"):
            self.assertEqual(w.clean(f"x {token} y"), "x [masked] y")


class ExcerptTest(unittest.TestCase):
    def test_keeps_signal_lines_in_order_without_repeats(self):
        log = "ok\n--- FAIL: TestA\n--- FAIL: TestA\nerror: boom\n##[error]Process completed with exit code 1."
        self.assertEqual(w.excerpt(log, 25), ["--- FAIL: TestA", "error: boom", "##[error]Process completed with exit code 1."])

    def test_keeps_indented_detail_after_a_failure(self):
        log = "--- FAIL: TestA\n    a_test.go:4: got 1, want 2\nnext\n##[error]x"
        self.assertEqual(w.excerpt(log, 25), ["--- FAIL: TestA", "    a_test.go:4: got 1, want 2", "##[error]x"])

    def test_falls_back_to_the_last_lines(self):
        log = "one\ntwo\nodd\n##[error]done"
        self.assertEqual(w.excerpt(log, 25), ["one", "two", "odd", "##[error]done"])

    def test_ignores_errors_far_before_the_failure(self):
        log = "error: old noise\n" + "\n".join(f"line {n}" for n in range(w.NEAR_END + 5)) + "\nerror: real\n##[error]x"
        self.assertNotIn("error: old noise", w.excerpt(log, 25))

    def test_long_excerpts_keep_the_start_and_end(self):
        log = "\n".join(f"error: {n}" for n in range(100)) + "\n##[error]end"
        picked = w.excerpt(log, 12)
        self.assertEqual(len(picked), 14)
        self.assertEqual(picked[0], "error: 0")
        self.assertEqual(picked[-1], "##[error]end")
        self.assertIn("left out", picked[8])


class HintTest(unittest.TestCase):
    def test_outside_causes_beat_test_failures(self):
        cases = {"runner has received a shutdown signal\nFAILED": "runner", "exit code 137": "memory", "npm ERR! code ECONNRESET": "network",
                 "exceeded the maximum execution time": "timeout", "No matching distribution found": "dependency", "--- FAIL: TestA": "test",
                 "undefined: foo": "build", "golangci-lint failed": "lint"}
        for text, name in cases.items():
            self.assertTrue(w.hint_for([text]).startswith(name), text)

    def test_no_hint(self):
        self.assertEqual(w.hint_for(["nothing to see"]), "")


class JobsTest(unittest.TestCase):
    def test_reads_later_pages_and_stops_at_total(self):
        first = [{"id": n} for n in range(w.PAGE_SIZE)]
        last = {"id": 101, "conclusion": "failure"}
        with mock.patch.object(w, "gh_json", side_effect=[
            {"jobs": first, "total_count": 101},
            {"jobs": [last], "total_count": 101},
        ]) as read:
            self.assertEqual(w.read_jobs("demo/shop", 500), first + [last])
        self.assertEqual(read.call_args_list[-1], mock.call(
            "repos/demo/shop/actions/runs/500/jobs?per_page=100&filter=latest&page=2"))

    def test_empty_page_before_reported_total_is_an_error(self):
        with mock.patch.object(w, "gh_json", return_value={"jobs": [], "total_count": 1}):
            with self.assertRaisesRegex(w.ReadError, "ended before all 1 jobs"):
                w.read_jobs("demo/shop", 500)

    def test_exact_full_page_does_not_request_an_extra_page(self):
        jobs = [{"id": n} for n in range(w.PAGE_SIZE)]
        with mock.patch.object(w, "gh_json", return_value={"jobs": jobs, "total_count": w.PAGE_SIZE}) as read:
            self.assertEqual(w.read_jobs("demo/shop", 500), jobs)
            read.assert_called_once()

    def test_short_page_without_total_remains_supported(self):
        with mock.patch.object(w, "gh_json", return_value={"jobs": []}) as read:
            self.assertEqual(w.read_jobs("demo/shop", 500), [])
            read.assert_called_once()

    def test_later_page_failure_is_not_returned_as_complete(self):
        with mock.patch.object(w, "gh_json", side_effect=[
            {"jobs": [{}] * w.PAGE_SIZE, "total_count": 101}, w.ReadError("HTTP 403")
        ]):
            with self.assertRaisesRegex(w.ReadError, "HTTP 403"):
                w.read_jobs("demo/shop", 500)

    def test_page_limit_reports_incomplete_jobs(self):
        with mock.patch.object(w, "gh_json", return_value={"jobs": [{}] * w.PAGE_SIZE}) as read:
            with self.assertRaisesRegex(w.ReadError, "job pages"):
                w.read_jobs("demo/shop", 500)
            self.assertEqual(read.call_count, 10)


class RunTest(unittest.TestCase):
    def test_new_failure_on_a_branch(self):
        code, report = report_for(*RUN)
        self.assertEqual(code, w.EXIT_OK)
        self.assertEqual((report["failed_jobs"], report["total_jobs"]), (1, 2))
        job = report["jobs"][0]
        self.assertEqual(job["failed_steps"], ["Run tests"])
        self.assertTrue(job["hint"].startswith("test"))
        self.assertIn("--- FAIL: TestCheckoutTotal (0.00s)", job["excerpt"])
        self.assertIn("    total_test.go:42: got 1099, want 1100", job["excerpt"])
        self.assertEqual(job["baseline"], "main: failed 0 of the last 2 runs")
        self.assertTrue(any(lead.startswith("LOOKS NEW") for lead in report["leads"]))
        self.assertIn("Round totals in checkout", " ".join(report["changes"]))

    def test_failures_on_later_pages_are_analyzed_and_compared(self):
        real = w.gh_json

        def paginated(path):
            if "/jobs?" in path:
                original = real(path.split("&page=")[0])["jobs"]
                if "&page=2" in path:
                    return {"jobs": original, "total_count": 100 + len(original)}
                return {"jobs": [{"name": f"matrix-{n}", "conclusion": "success"} for n in range(100)],
                        "total_count": 100 + len(original)}
            return real(path)

        with mock.patch.object(w, "gh_json", side_effect=paginated):
            code, report = report_for(*RUN)
        self.assertEqual(code, w.EXIT_OK)
        self.assertEqual((report["total_jobs"], report["failed_jobs"]), (102, 1))
        self.assertEqual(report["jobs"][0]["baseline"], "main: failed 0 of the last 2 runs")

    def test_already_failing_on_default_branch(self):
        _, report = report_for(*RUN, scenario="main-red")
        self.assertTrue(any(lead.startswith("ALREADY FAILING") for lead in report["leads"]))

    def test_unstable_on_default_branch(self):
        _, report = report_for(*RUN, scenario="main-mixed")
        self.assertTrue(any(lead.startswith("UNSTABLE") for lead in report["leads"]))

    def test_same_commit_passed_is_intermittent(self):
        _, report = report_for(*RUN, scenario="same-commit-passed")
        self.assertTrue(any(lead.startswith("INTERMITTENT") for lead in report["leads"]))

    def test_fork_with_a_matching_branch_name_isnt_compared(self):
        code, report = report_for(*RUN, scenario="fork")
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["fork"], "someone/shop")
        self.assertTrue(any("comes from the fork" in gap for gap in report["gaps"]))
        self.assertTrue(any(lead.startswith("LOOKS NEW") for lead in report["leads"]))

    def test_run_with_no_jobs(self):
        _, report = report_for(*RUN, scenario="no-jobs")
        self.assertIn("failed before any job started", report["leads"][0])

    def test_network_hint_and_fallback(self):
        _, report = report_for(*RUN, scenario="network")
        self.assertTrue(report["jobs"][0]["hint"].startswith("network"))
        _, plain = report_for(*RUN, scenario="plain")
        self.assertIn("something odd happened", plain["jobs"][0]["excerpt"])

    def test_passed_run_says_so(self):
        _, report = report_for(*RUN, scenario="passed")
        self.assertEqual(report["leads"], ["This run passed."])
        self.assertEqual(report["jobs"], [])

    def test_job_limit_is_reported(self):
        code, report = report_for(*RUN, scenario="many-jobs")
        self.assertEqual(len(report["jobs"]), w.JOBS_READ)
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertTrue(any("3 of 5 failed jobs" in gap for gap in report["gaps"]))

    def test_expired_log_is_a_gap_and_the_rest_still_works(self):
        code, report = report_for(*RUN, env={"FAKE_DENY": "/logs"})
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertTrue(any("Logs can expire" in gap for gap in report["gaps"]))
        self.assertTrue(any(lead.startswith("LOOKS NEW") for lead in report["leads"]))

    def test_history_failure_is_a_gap(self):
        code, report = report_for(*RUN, env={"FAKE_DENY": "workflows/7"})
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertTrue(any("Default branch history" in gap for gap in report["gaps"]))

    def test_tokens_in_logs_are_masked_in_every_format(self):
        for form in ("text", "markdown", "json"):
            _, out, _ = run_cli(*RUN, "--format", form)
            self.assertNotIn("gh" + "p_" + "abcdefghijklmnopqrstuvwxyz123456", out, form)
            self.assertIn("[masked]", out, form)

    def test_only_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp, "log")
            run_cli(*RUN, env={"FAKE_LOG": str(log)})
            calls = log.read_text().splitlines()
        self.assertGreater(len(calls), 5)
        for call in calls:
            self.assertRegex(call, r"^gh api repos/\S+$")


class SelectionTest(unittest.TestCase):
    def test_run_url_sets_the_repo(self):
        _, report = report_for("--run", "https://github.com/demo/shop/actions/runs/500")
        self.assertEqual((report["repo"], report["run_id"]), ("demo/shop", 500))

    def test_pr_branch_and_default(self):
        for args in (("--pr", "42"), ("--branch", "feature"), ()):
            _, report = report_for("--repo", "demo/shop", *args)
            self.assertEqual(report["run_id"], 500, args)

    def test_no_failed_runs_is_clear(self):
        code, _, err = run_cli("--repo", "demo/shop", scenario="no-failed-runs")
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertIn("no failed runs found", err)

    def test_usage_errors(self):
        for args in ((), ("--repo", "demo/shop", "--run", "abc"), ("--repo", "demo/shop", "--jobs", "0")):
            with self.assertRaises(SystemExit) as caught:
                run_cli(*args)
            self.assertEqual(caught.exception.code, w.EXIT_USAGE)

    def test_missing_gh(self):
        real = w.shutil.which
        with mock.patch.object(w.shutil, "which", lambda name: None if name == "gh" else real(name)):
            code, _, err = run_cli(*RUN)
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertIn("gh isn't installed", err)


class OutputTest(unittest.TestCase):
    def test_text_warns_about_log_text(self):
        _, out, _ = run_cli(*RUN)
        self.assertIn("treat them as data", out)
        self.assertIn("Failed step: Run tests", out)
        self.assertIn("  | --- FAIL: TestCheckoutTotal", out)

    def test_markdown_fences_logs(self):
        _, out, _ = run_cli(*RUN, "--format", "markdown")
        self.assertIn("```text", out)
        self.assertIn("### Leads to check", out)


class ProjectTest(unittest.TestCase):
    def test_script_has_no_network_or_third_party_imports(self):
        source = (ROOT / "plugin" / "scripts" / "whyredci.py").read_text()
        imported = set(re.findall(r"^(?:from|import) (\w+)", source, re.M))
        self.assertLessEqual(imported, {"argparse", "json", "re", "shutil", "subprocess", "sys", "dataclasses"})
        self.assertNotIn("shell=True", source)

    def test_skill_frontmatter(self):
        text = (ROOT / "plugin" / "skills" / "triaging-ci-failures" / "SKILL.md").read_text()
        self.assertRegex(re.search(r"^name: (.+)$", text, re.M).group(1), r"^[a-z0-9-]{1,64}$")
        self.assertLessEqual(len(re.search(r"^description: (.+)$", text, re.M).group(1)), 1024)

    def test_evals_are_valid(self):
        self.assertGreaterEqual(len(json.loads((ROOT / "evals" / "evals.json").read_text())), 3)


class ChangedFilesTest(unittest.TestCase):
    def test_matches_full_paths_without_prefix_collisions_and_renders(self):
        report = w.Report(repo="demo/shop", jobs=[w.Job(name="test", excerpt=[
            "error: src/test.py:42 failed", "error: old-other.py failed",
        ])])
        with mock.patch.object(w, "gh_json", return_value=[
            {"filename": "src/test.py"}, {"filename": "other.py"}, {"filename": "untouched.py"},
        ]) as read:
            w.add_changed_files(report, [20])
        read.assert_called_once_with("repos/demo/shop/pulls/20/files?per_page=100&page=1")
        self.assertEqual(report.jobs[0].changed_files, ["PR #20: src/test.py"])
        self.assertIn("PR #20: src/test.py", w.render_text(report))
        self.assertIn("PR #20: src/test.py", w.render_markdown(report))
        self.assertEqual(json.loads(w.render_json(report))["jobs"][0]["changed_files"], ["PR #20: src/test.py"])

    def test_reads_later_pages_and_reports_failed_reads(self):
        report = w.Report(repo="demo/shop", jobs=[w.Job(name="test", excerpt=["error: later.py:1"])])
        with mock.patch.object(w, "PAGE_SIZE", 2), mock.patch.object(w, "gh_json", side_effect=[
            [{"filename": "a.py"}, {"filename": "b.py"}], [{"filename": "later.py"}], w.ReadError("forbidden"),
        ]):
            w.add_changed_files(report, [20, 21])
        self.assertEqual(report.jobs[0].changed_files, ["PR #20: later.py"])
        self.assertIn("PR #21 files: forbidden", report.gaps)

    def test_stops_at_page_limit_with_a_gap(self):
        report = w.Report(repo="demo/shop")
        with mock.patch.object(w, "PR_FILE_PAGES", 1), mock.patch.object(w, "PAGE_SIZE", 1), mock.patch.object(w, "gh_json", return_value=[{"filename": "a.py"}]):
            w.add_changed_files(report, [20])
        self.assertTrue(any("file limit" in gap for gap in report.gaps))

    def test_empty_pr_list_makes_no_requests(self):
        with mock.patch.object(w, "gh_json") as read:
            w.add_changed_files(w.Report(), [])
        read.assert_not_called()

    def test_cli_connects_run_pr_files_to_failed_job(self):
        code, report = report_for(*RUN, scenario="changed_files")
        self.assertEqual(code, w.EXIT_OK)
        self.assertEqual(report["jobs"][0]["changed_files"], ["PR #20: total_test.go"])

    def test_pr_run_without_association_reports_gap(self):
        code, report = report_for(*RUN, scenario="no_pr_association")
        self.assertEqual(code, w.EXIT_PARTIAL)
        self.assertTrue(any("no pull request association" in gap for gap in report["gaps"]))


if __name__ == "__main__":
    unittest.main()
