# SPDX-License-Identifier: AGPL-3.0-or-later

"""Regression tests for conservative CI selection (stdlib only)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {
    "rust",
    "trial",
    "integration",
    "complement",
    "linting",
    "linting_readme",
    "golangci",
}


class SelectionTests(unittest.TestCase):
    def select(self, files, event="pull_request", outcome="success", count=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            summary = Path(directory) / "summary"
            result = subprocess.run(
                [sys.executable, str(ROOT / ".ci/scripts/select_checks.py")],
                env={
                    **os.environ,
                    "GITHUB_EVENT_NAME": event,
                    "FILTER_OUTCOME": outcome,
                    "CHANGED_FILES": json.dumps(files),
                    "CHANGED_FILE_COUNT": str(len(files) if count is None else count),
                    "GITHUB_OUTPUT": str(output),
                    "GITHUB_STEP_SUMMARY": str(summary),
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            values = {}
            lines = iter(output.read_text().splitlines())
            for line in lines:
                if "<<" in line:
                    key, delimiter = line.split("<<", 1)
                    parts = []
                    for part in lines:
                        if part == delimiter:
                            break
                        parts.append(part)
                    values[key] = "\n".join(parts)
                else:
                    key, value = line.split("=", 1)
                    values[key] = value
            self.assertIn("CI selection", summary.read_text())
            return values

    def enabled(self, files, **kwargs):
        return {
            key
            for key, value in self.select(files, **kwargs).items()
            if key in GROUPS and value == "true"
        }

    def test_docs_and_news(self):
        self.assertEqual(
            self.enabled(["docs/development/git.md", "changelog.d/2.misc"]), set()
        )
        self.assertEqual(self.enabled(["README.rst"]), {"linting_readme"})

    def test_python_tests_keep_entire_trial_group(self):
        self.assertEqual(
            self.enabled(["tests/handlers/test_sync.py"]), {"trial", "linting"}
        )

    def test_in_repo_tests_keep_complement_and_go_checks(self):
        self.assertEqual(
            self.enabled(["complement/tests/federation_test.go"]),
            {"complement", "golangci"},
        )

    def test_mixed_safe_paths_union_groups(self):
        self.assertEqual(
            self.enabled(
                [
                    "docs/architecture.md",
                    "tests/test_types.py",
                    "complement/tests/testdata/new.json",
                ]
            ),
            {"trial", "linting", "complement", "golangci"},
        )

    def test_runtime_infrastructure_and_unknown_paths_run_full(self):
        for path in [
            "synapse/handlers/sync.py",
            "rust/src/lib.rs",
            "Cargo.lock",
            "poetry.lock",
            "pyproject.toml",
            "docker/Dockerfile",
            ".github/workflows/tests.yml",
            ".ci/scripts/select_checks.py",
            "tests/utils.py",
            "tests/unittest.py",
            "tests/handlers/__init__.py",
            "complement/go.mod",
            "complement/shared/helper.go",
            "new-component/file.py",
            "docs/usage/configuration/config_documentation.md",
            "docs/sample_config.yaml",
            "docs/.sample_config_header.yaml",
            "sytest-blacklist",
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.enabled(["docs/architecture.md", path]), GROUPS)

    def test_renames_include_old_and_new_paths_from_paths_filter(self):
        # dorny/paths-filter reports a rename as a deletion plus an addition.
        self.assertEqual(self.enabled(["synapse/old.py", "docs/old.md"]), GROUPS)
        self.assertEqual(self.enabled(["docs/old.md", "synapse/new.py"]), GROUPS)

    def test_non_pr_events_always_run_full(self):
        for event in [
            "push",
            "merge_group",
            "workflow_dispatch",
            "schedule",
            "pull_request_target",
        ]:
            with self.subTest(event=event):
                self.assertEqual(
                    self.enabled(["docs/architecture.md"], event=event), GROUPS
                )

    def test_unavailable_or_incomplete_diff_runs_full(self):
        for outcome in ["failure", "skipped", "cancelled", ""]:
            self.assertEqual(
                self.enabled(["docs/architecture.md"], outcome=outcome), GROUPS
            )
        for files, count in [
            ([], 0),
            (["docs/a.md"], 2),
            (["docs/a.md"], 3000),
            (["docs/a.md"], "invalid"),
            ([None], 1),
            (["../docs/a.md"], 1),
            (["docs/a.md\ntrial=false"], 1),
        ]:
            with self.subTest(files=files, count=count):
                self.assertEqual(self.enabled(files, count=count), GROUPS)

    def test_only_unselected_jobs_are_skippable(self):
        full = self.select(["synapse/app/homeserver.py"])
        self.assertEqual(full["skippable_tests"], "")
        self.assertEqual(full["skippable_lint"], "")
        selected = self.select(["complement/tests/room_test.go"])
        self.assertNotIn("complement", selected["skippable_tests"].split())
        self.assertNotIn("lint-golangci", selected["skippable_lint"].split())
        self.assertIn("sytest", selected["skippable_tests"].split())
        python = self.select(["tests/test_types.py"])
        self.assertNotIn("trial", python["skippable_tests"].split())
        self.assertNotIn("trial-olddeps", python["skippable_tests"].split())
        self.assertNotIn("lint-mypy", python["skippable_lint"].split())


if __name__ == "__main__":
    unittest.main()
