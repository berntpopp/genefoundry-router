# Data Artifact Contract Review

Date: 2026-07-13
PR: https://github.com/berntpopp/genefoundry-router/pull/60
Reviewer command: `claude -p --model claude-opus-4-8 --effort xhigh`

## Initial Opus Findings

Opus reviewed the PR #60 diff against `origin/main` and reported four release blockers:

- CLI schema probe was tautological. `materialize-data` returned the operator-supplied `--schema-version` instead of probing the extracted artifact.
- Rollback selected retained data without re-verifying tree identity or schema compatibility.
- `previous_known_good_digest` was accepted as evidence but not enforced before selecting a new version.
- Data manifests were local unsigned JSON inputs with no offline trust anchor.

Opus also recommended fixing writable extracted modes, data-root ownership/mode checks, mutable data release labels, and zstd process handling before wiring the contract into CI.

## Dispositions

- Schema probe: fixed. `materialize-data` now requires `--schema-file`, reads that JSON file from the extracted tree via `probe_schema_file`, and cross-checks it against the manifest schema version.
- Rollback verification: fixed. Materialization writes a sidecar identity file containing artifact digest, expanded tree digest, expanded size, member count, data schema, release tag, and previous-known-good digest. `rollback-data` recomputes tree identity and checks schema compatibility before selecting.
- Previous-known-good retention: fixed. Materialization requires the previous-known-good digest to be retained and verified before selecting a new artifact. The bootstrap case is explicit: previous-known-good may equal the artifact being materialized.
- Manifest trust anchor: fixed for this offline contract stage. `validate-data-manifest` and `materialize-data` now require `--manifest-sha256` and fail if the supplied manifest bytes do not match the trusted digest.
- Extracted modes: fixed. Regular members are clamped to read-only modes before identity hashing and materialization.
- Data-root safety: fixed. Existing data roots must be private directories owned by the effective user and not group/world writable.
- Mutable data labels: fixed. `latest`, `main`, `master`, `head`, `stable`, and `current` are rejected as data release tags.
- zstd handling: fixed. The decompressor is resolved from `PATH`; timeout and exception paths kill and reap the subprocess.

## Verification Evidence

- `uv run pytest -q tests/release/test_data_release.py --tb=short`: 46 passed.
- `uv run ruff check genefoundry_router/release/data.py genefoundry_router/release/data_materialization.py genefoundry_router/release/cli.py genefoundry_router/release/models.py tests/release/test_data_release.py --output-format=github`: passed.
- `uv run mypy genefoundry_router/release/data.py genefoundry_router/release/data_materialization.py genefoundry_router/release/cli.py genefoundry_router/release/models.py`: passed.
- `uv run python scripts/check_file_size.py`: passed.
- `uv run pytest tests/release -q`: 946 passed.
- `/tmp/actionlint -color=false .github/workflows/_container-ci.yml .github/workflows/_container-release.yml`: passed.
- `git diff --check`: passed.
- `make ci-local`: passed.

## Final Verdict

Pending rerun of Opus after the fixes.
