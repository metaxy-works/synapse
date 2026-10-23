# SPDX-License-Identifier: AGPL-3.0-or-later

"""Check the selection/gate contract and cache paths without starting services."""

import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.load(
        (ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tests = workflow("tests.yml")
        self.complement = workflow("complement_tests.yml")

    def test_discovery_is_whole_pr_and_failure_falls_back(self):
        changes = self.tests["jobs"]["changes"]
        discovery = next(
            step for step in changes["steps"] if step.get("id") == "filter"
        )
        self.assertEqual(discovery.get("continue-on-error"), "true")
        self.assertEqual(discovery["with"].get("list-files"), "json")
        self.assertNotIn("base", discovery["with"])
        self.assertEqual(discovery["with"]["filters"].strip(), "all: ['**']")
        selector = next(step for step in changes["steps"] if step.get("id") == "select")
        self.assertIn("steps.filter.outcome", selector["env"]["FILTER_OUTCOME"])
        self.assertIn(
            "github.event.pull_request.changed_files",
            selector["env"]["CHANGED_FILE_COUNT"],
        )

    def test_gate_lists_match_job_conditions(self):
        spec = importlib.util.spec_from_file_location(
            "selector", ROOT / ".ci/scripts/select_checks.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for kind, mapping, gate_name in [
            ("tests", module.TEST_JOBS, "tests-done"),
            ("lint", module.LINT_JOBS, "linting-done"),
        ]:
            gate = self.tests["jobs"][gate_name]
            self.assertIn("changes", gate["needs"])
            action = next(
                step for step in gate["steps"] if "done-action@" in step.get("uses", "")
            )
            self.assertIn(
                f"needs.changes.outputs.skippable_{kind}", action["with"]["skippable"]
            )
            for group, jobs in mapping.items():
                self.assertIn(group, self.tests["jobs"]["changes"]["outputs"])
                for job in jobs:
                    self.assertIn(job, gate["needs"])
                    self.assertIn(
                        f"needs.changes.outputs.{group} == 'true'",
                        self.tests["jobs"][job]["if"],
                    )
        self.assertIn("calculate-test-jobs", self.tests["jobs"]["tests-done"]["needs"])

    def test_complement_in_repo_selection_does_not_disable_in_repo_tests(self):
        self.assertEqual(
            self.complement["on"]["workflow_call"]["inputs"]["run_external_tests"][
                "default"
            ],
            "true",
        )
        caller = self.tests["jobs"]["complement"]
        self.assertIn("outputs.integration", caller["with"]["run_external_tests"])
        steps = {
            step.get("id"): step
            for step in self.complement["jobs"]["complement"]["steps"]
        }
        self.assertIn("inputs.run_external_tests", steps["run_complement_tests"]["if"])
        condition = steps["run_in_repo_complement_tests"]["if"]
        self.assertIn("!inputs.run_external_tests", condition)
        self.assertIn(
            "steps.run_sanity_check_complement_image_test.outcome == 'success'",
            condition,
        )
        self.assertIn("!cancelled()", condition)
        self.assertEqual(
            len(self.complement["jobs"]["complement"]["strategy"]["matrix"]["include"]),
            3,
        )

    def test_complement_caches_use_both_real_workspaces(self):
        steps = self.complement["jobs"]["complement"]["steps"]
        rust = next(
            step for step in steps if "Swatinem/rust-cache@" in step.get("uses", "")
        )
        self.assertEqual(rust.get("with", {}).get("workspaces"), "synapse -> target")
        go = next(step for step in steps if "actions/setup-go@" in step.get("uses", ""))
        self.assertIn(
            "complement/go.sum", go["with"]["cache-dependency-path"].splitlines()
        )
        self.assertIn(
            "synapse/complement/go.sum",
            go["with"]["cache-dependency-path"].splitlines(),
        )

    def test_mypy_compatibility_prefix_and_real_sha(self):
        steps = self.tests["jobs"]["lint-mypy"]["steps"]
        cache = next(step for step in steps if "actions/cache@" in step.get("uses", ""))
        key = cache["with"]["key"]
        restore = cache["with"]["restore-keys"]
        self.assertNotIn("github.context.sha", key)
        for part in [
            "runner.os",
            "runner.arch",
            "steps.mypy-cache-key.outputs.python",
            "poetry.lock",
            "mypy.ini",
        ]:
            self.assertIn(part, key)
            self.assertIn(part, restore)
        self.assertEqual(key, restore + "${{ github.sha }}")
        self.assertTrue(any(step.get("run") == "poetry run mypy" for step in steps))

    def test_current_modes_never_reuse_a_different_dependency_image(self):
        for step in self.complement["jobs"]["complement"]["steps"]:
            if step.get("id") not in (
                "run_complement_tests",
                "run_in_repo_complement_tests",
            ):
                continue
            env = step["env"]["REUSE_IMAGE"]
            self.assertIn("!inputs.use_latest_deps", env)
            self.assertIn("!inputs.use_twisted_trunk", env)
            self.assertIn('"${extra_args[@]}"', step["run"])
            self.assertIn("--fast", step["run"])
            self.assertIn("set -o pipefail", step["run"])
        self.assertIn("-count=1", (ROOT / "scripts-dev/complement.sh").read_text())

    def test_sanity_always_builds_before_reuse(self):
        step = next(
            step
            for step in self.complement["jobs"]["complement"]["steps"]
            if step.get("id") == "run_sanity_check_complement_image_test"
        )
        self.assertNotIn("REUSE_IMAGE", step["run"])
        self.assertNotIn("--fast", step["run"])

    def test_complement_shell_arguments_and_failure_propagation(self):
        # Execute the real workflow shell with only the heavy test process
        # substituted. No Docker, network or test services are started.
        for step in self.complement["jobs"]["complement"]["steps"]:
            if step.get("id") not in (
                "run_complement_tests",
                "run_in_repo_complement_tests",
            ):
                continue
            for reuse in ("true", "false"):
                for status in (0, 17):
                    with (
                        self.subTest(step=step["id"], reuse=reuse, status=status),
                        tempfile.TemporaryDirectory() as directory,
                    ):
                        root = Path(directory)
                        script = root / "synapse/scripts-dev/complement.sh"
                        script.parent.mkdir(parents=True)
                        script.write_text(
                            '#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$CAPTURE"\nexit "$TEST_STATUS"\n'
                        )
                        script.chmod(0o755)
                        capture = root / "arguments"
                        # Keep the workflow logs inside the disposable test directory.
                        command = step["run"].replace(
                            "/tmp/gotest-", directory + "/gotest-"
                        )
                        result = subprocess.run(
                            [
                                "bash",
                                "--noprofile",
                                "--norc",
                                "-eo",
                                "pipefail",
                                "-c",
                                command,
                            ],
                            cwd=root,
                            env={
                                **os.environ,
                                "REUSE_IMAGE": reuse,
                                "CAPTURE": str(capture),
                                "TEST_STATUS": str(status),
                            },
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, status, result.stderr)
                        args = capture.read_text().splitlines()
                        self.assertEqual("--fast" in args, reuse == "true")
                        self.assertEqual(
                            "--in-repo" in args,
                            step["id"] == "run_in_repo_complement_tests",
                        )
                        self.assertEqual(args[-3:], ["-p", "1", "-json"])

    def test_ci_policy_checks_always_run(self):
        checks = self.tests["jobs"]["ci-policy"]
        self.assertNotIn("if", checks)
        self.assertIn("ci-policy", self.tests["jobs"]["linting-done"]["needs"])
        self.assertTrue(
            any(
                "unittest discover -s .ci/tests" in step.get("run", "")
                for step in checks["steps"]
            )
        )

    def test_release_guards_are_preserved(self):
        for job in workflow("docker.yml")["jobs"].values():
            self.assertIn("github.repository == 'element-hq/synapse'", job["if"])
        self.assertEqual(
            set(self.tests["on"]),
            {"pull_request", "push", "merge_group", "workflow_dispatch"},
        )
        self.assertEqual(
            self.tests["on"]["push"]["branches"], ["main", "develop", "release-*"]
        )


if __name__ == "__main__":
    unittest.main()
