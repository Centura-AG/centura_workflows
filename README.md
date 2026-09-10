# Centura reusable GitHub Actions workflows

This repository contains the reusable workflows every Centura Frappe app calls from its own `.github/workflows/`:

- `ci.yaml` — bench setup, `bench run-tests` for the app, coverage report
- `linter.yaml` — pre-commit (ruff, prettier) and Semgrep
- `create-version-based-on-tag.yaml` — version bump from a pushed tag

## Workflow: `ci.yaml`

Called with `uses: Centura-AG/centura_workflows/.github/workflows/ci.yaml@develop`. Besides the bench inputs (`frappe_branch`, `install_apps`, `additional_apps`, ...) it has these coverage inputs:

| Name | Type | Default | Meaning |
|---|---|---|---|
| `coverage` | boolean | `true` | Run `bench run-tests --coverage` in the test job and evaluate the result in a separate `Coverage` job: total and per-file Python coverage in the job summary, patch coverage of the PR with [diff-cover](https://github.com/Bachmann1234/diff_cover), `coverage.xml` + `diff-cover.md/json` as artifact `coverage-<app>` (30 days). |
| `coverage_fail_under` | string | `'80'` | Minimum patch coverage in percent for new or changed lines. |
| `enforce_coverage` | boolean | `false` | When `true`, the `Coverage` job fails below `coverage_fail_under`. When `false` (report-only) a warning annotation is written instead. A PR with the label `skip-coverage` is never failed. |
| `coverage_comment` | boolean | `false` | Post the patch coverage report as a sticky PR comment through a separate `coverage-comment` job. The caller job must grant `permissions: pull-requests: write`. |

Jobs and check names: `Sanity Checks` → `Python Unit Tests` → `Coverage` (→ `Coverage comment`, opt-in). The test job only collects the data (`.coverage` → filtered `coverage.xml`, per-file report, total, uploaded as the short-lived artifact `coverage-data-<app>`); the `Coverage` job needs no bench and takes about a minute: checkout with history, `pip install diff-cover`, download the data, evaluate. Test failures and coverage misses therefore show up as two different checks.

Coverage details:

- Test files, `patches/` and `node_modules/` are excluded from both the total and the patch numbers (`*/test_*.py`, `*/tests/*`, `*/patches/*`).
- The total counts every `.py` file in the app, including files no test imports.
- Patch coverage is computed against `origin/<base branch>` of the pull request (the `Coverage` job checks out with `fetch-depth: 0`). On non-PR runs only the total is reported.
- JavaScript: in the `Coverage` job, every directory with a `vitest.config.*` that contains at least one `*.test.*` / `*.spec.*` file (outside `node_modules/` and `e2e/`) is run with `vitest run --coverage` (v8 provider, lcov) and reported the same way. The package must declare `@vitest/coverage-v8` as a devDependency. Directories without test files are skipped, so frontends that only carry the scaffolded config are unaffected.

Turning the report into a merge gate later means setting `enforce_coverage: true` (globally here or per caller in its `with:` block) and adding `Coverage` as a required status check in the repository ruleset.

## Workflow: `create-version-based-on-tag.yaml`

### Purpose
This workflow extracts the version number from a pushed Git tag and updates `__version__` in the specified Python package's `__init__.py` accordingly.

### Location
```
.github/workflows/create-version-based-on-tag.yaml
```

## Inputs
The workflow is designed to be reusable using `workflow_call`. It accepts the following input:

| Name          | Description                                         | Required | Type   | Default          |
|--------------|-----------------------------------------------------|----------|--------|------------------|
| package_path | Path to the package directory containing `__init__.py` | ✅        | string | `my_repo_name`   |

## Permissions
The workflow requires `contents: write` permission to create and push tags.

## Usage Example

### Create Version from TAG

```yaml
name: Version Tag Creator

on:
  push:
    tags:
      - 'v*'

jobs:
  call-version-tag-workflow:
    uses: Centura-AG/centura_workflows/.github/workflows/create-version-based-on-tag.yaml
    permissions:
      contents: write
    with:
      package_path: ${{ github.event.repository.name }}
      pushed_tag: ${{ github.ref_name }}

```

## Requirements
- The target repository must contain a Python package with an `__init__.py` file where `__version__` is defined.
- The workflow should be triggered when a version tag (`v*`) is pushed.
- Ensure `contents: write` permission is granted to allow tag creation.

## License
This project is licensed under the MIT License. You are free to use, modify, and distribute it. However, it comes without any warranty or liability. Use it at your own risk.

