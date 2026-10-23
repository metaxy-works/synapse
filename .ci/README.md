# CI selection and caches

`tests.yml` selects **whole check groups**, not individual test cases. Existing
Python, database, reactor and Complement matrices are unchanged.

## Pull requests

`dorny/paths-filter` supplies the complete PR file list, including deleted paths
and both sides of a rename. `.ci/scripts/select_checks.py` only takes a shorter
route if **every** path is allowlisted:

| Changes | Checks selected in `Tests` |
| --- | --- |
| Ordinary Markdown/reStructuredText under `docs/` (excluding `docs/usage/configuration/`), release notes and numbered changelog fragments | Always-on policy, line-ending, lockfile and applicable newsfragment checks |
| `README.rst` | Above plus README validation |
| `tests/**/test_*.py` | Complete existing Trial matrix, old dependencies and Python/static checks |
| `complement/tests/**` | Go lint, image sanity and the complete in-repo Complement suite in all three modes |
| Any other path, including runtime, shared fixtures, dependencies, configuration or CI | Full regression |

Mixed allowlisted changes combine groups. Invalid/unavailable diffs, empty diffs
and the GitHub 3000-file API limit fall back to full regression. The selection
and its reason are recorded in the job summary. Discovery may fail without
failing selection, **only because the fallback runs everything**.

All non-PR events, including `main`/`develop`/release pushes, merge groups and
manual runs, select full regression. Use **Tests → Run workflow** to request a
full run. Packaging, schema-documentation and other workflows retain their own
existing triggers; in particular, artifact builds are not skipped because docs
and tests also ship in the source distribution.

`linting-done` and `tests-done` accept skips only for unselected groups (plus the
existing conditional newsfragment rule). A selected job that fails, is cancelled
or unexpectedly skips fails the gate. Discovery and policy validation are also
required by these gates. No test failure is ignored.

## Caches

- Complement Rust cache points at the actual `synapse` checkout and its `target`
  directory, rather than the empty workspace root.
- Complement Go cache keys include both the external and in-repo `go.sum` files.
  `go test -count=1` is unchanged: test results are never reused.
- Mypy cache keys include OS, architecture, actual Python version, dependency/
  configuration hashes and commit SHA. Restore fallback stays within that
  compatibility prefix; mypy still runs on every selected typecheck job.
- Within a normal Complement job, the sanity step builds and verifies the current
  image before subsequent suites use `--fast`. Latest-dependency and Twisted-trunk
  runs retain their original rebuild behavior. Images are not reused across runs.

A cache miss still installs/builds normally. This version does not replace the
upstream Poetry setup action or add cross-run Docker caching. Those require
separate validation; no speedup percentage is assumed.

## Local validation and upstream updates

```sh
python -m pip install PyYAML==6.0.3
python -m unittest discover -s .ci/tests -v
ruff check .ci/scripts/select_checks.py .ci/tests
ruff format --check .ci/scripts/select_checks.py .ci/tests
actionlint .github/workflows/tests.yml .github/workflows/complement_tests.yml
```

The policy suite includes whole-PR classification, unknown/malformed input,
rename paths, non-PR full regression, job/gate wiring, cache paths, and execution
of the workflow shell with a substitute test process to verify arguments and
failure propagation. These checks do not replace hosted integration tests or
validate GitHub's cache service locally.

When syncing upstream, preserve the upstream workflows and matrices. Review new
paths conservatively and update the selector's job mappings together with their
contract tests. The reusable Complement workflow's new input defaults to `true`,
so existing callers still run both suites. Publication guards are unchanged.
