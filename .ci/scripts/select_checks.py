# SPDX-License-Identifier: AGPL-3.0-or-later

"""Select whole CI groups, with full regression unless every path is understood.

paths-filter supplies the whole PR diff, including both sides of renames and
removed files. Non-PR events, failed discovery and potentially truncated API
responses always run all groups. This does not select individual test cases.
"""

import json
import os
import re
from pathlib import PurePosixPath

# Only jobs in an unselected group may be skipped by the existing done-action.
# Keep these names in sync with tests.yml; test_workflows.py checks the wiring.
TEST_JOBS = {
    "rust": ["cargo-test", "cargo-bench"],
    "trial": ["trial", "trial-olddeps"],
    "integration": ["sytest", "portdb", "export-data"],
    "complement": ["complement"],
}
LINT_JOBS = {
    "rust": ["lint-clippy", "lint-clippy-nightly", "lint-rust", "lint-rustfmt"],
    "linting": ["lint", "lint-mypy", "check-sampleconfig", "check-schema-delta"],
    "linting_readme": ["lint-readme"],
    "golangci": ["lint-golangci"],
}
GROUPS = TEST_JOBS.keys() | LINT_JOBS.keys()


def classify(files: list[str]) -> set[str]:
    selected: set[str] = set()
    for filename in files:
        path = PurePosixPath(filename)
        if (
            path.is_absolute()
            or ".." in path.parts
            or any(ord(char) < 32 for char in filename)
        ):
            return set(GROUPS)
        if filename == "README.rst":
            selected.add("linting_readme")
        elif (
            filename.startswith("docs/")
            and path.suffix in (".md", ".rst")
            and not filename.startswith("docs/usage/configuration/")
        ) or filename in ("CHANGES.md", "CONTRIBUTING.md", "INSTALL.md", "UPGRADE.rst"):
            continue
        elif re.fullmatch(
            r"changelog\.d/[0-9]+\.(feature|bugfix|doc|removal|misc)", filename
        ):
            continue
        elif filename.startswith("tests/") and re.fullmatch(r"test_.+\.py", path.name):
            selected.update(("trial", "linting"))
        elif filename.startswith("complement/tests/"):
            selected.update(("complement", "golangci"))
        else:
            # Runtime, shared fixtures, dependency locks, CI and unknown paths.
            return set(GROUPS)
    return selected


def select() -> tuple[set[str], str]:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return set(GROUPS), "Non-PR event: full regression."
    if os.environ.get("FILTER_OUTCOME") != "success":
        return set(GROUPS), "Changed-file discovery unavailable: full regression."
    try:
        files = json.loads(os.environ.get("CHANGED_FILES", ""))
        count = int(os.environ.get("CHANGED_FILE_COUNT", ""))
        # The GitHub list-files API has a 3000-file limit. Renames contribute
        # two paths, so the returned path count may exceed changed_files.
        if (
            not isinstance(files, list)
            or not all(isinstance(path, str) and path for path in files)
            or not 0 < count < 3000
            or len(files) < count
        ):
            raise ValueError("Incomplete changed-file list")
    except (ValueError, TypeError):
        return set(
            GROUPS
        ), "Changed-file list invalid or possibly truncated: full regression."
    selected = classify(files)
    if selected == GROUPS:
        return (
            selected,
            "Runtime, infrastructure or unclassified change: full regression.",
        )
    return (
        selected,
        "Only allowlisted docs or test paths changed: run affected groups in full.",
    )


def main() -> None:
    selected, reason = select()
    outputs = {key: str(key in selected).lower() for key in sorted(GROUPS)}
    for kind, mapping in (("tests", TEST_JOBS), ("lint", LINT_JOBS)):
        outputs[f"skippable_{kind}"] = "\n".join(
            job
            for group, jobs in mapping.items()
            if group not in selected
            for job in jobs
        )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for key, value in outputs.items():
            print(f"{key}<<CI_SELECTION\n{value}\nCI_SELECTION", file=output)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        print("## CI selection\n\n" + reason, file=summary)
        print("\n| Group | Decision |\n| --- | --- |", file=summary)
        for group in sorted(GROUPS):
            print(
                f"| {group} | {'Run' if group in selected else 'Not affected'} |",
                file=summary,
            )
        print(
            "\nLockfile, line endings, newsfragment and CI policy checks retain their own gates. "
            "Packaging and other workflows are unchanged. No test results are cached.",
            file=summary,
        )


if __name__ == "__main__":
    main()
