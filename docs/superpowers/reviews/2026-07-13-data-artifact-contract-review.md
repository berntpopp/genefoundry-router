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

## First-Round Dispositions

- Schema probe: fixed. `materialize-data` now requires `--schema-file`, reads that JSON file from the extracted tree via `probe_schema_file`, and cross-checks it against the manifest schema version.
- Rollback verification: fixed. Materialization writes a sidecar identity file containing artifact digest, expanded tree digest, expanded size, member count, data schema, release tag, and previous-known-good digest. `rollback-data` recomputes tree identity and checks schema compatibility before selecting.
- Previous-known-good retention: partially fixed. The first fix required the previous-known-good digest to be retained and verified, but the follow-up Opus review found the self-referential bootstrap path was not scoped tightly enough.
- Manifest trust anchor: fixed for this offline contract stage. `validate-data-manifest` and `materialize-data` now require `--manifest-sha256` and fail if the supplied manifest bytes do not match the trusted digest.
- Extracted modes: partially fixed. Regular members were clamped to read-only modes, but the follow-up Opus review found directory members were made read-only too early.
- Data-root safety: partially fixed. Materialization enforces private data-root permissions, but the follow-up Opus review found rollback did not.
- Mutable data labels: partially fixed. Runtime Pydantic validation rejects `latest`, `main`, `master`, `head`, `stable`, and `current`, but the follow-up Opus review found the generated JSON Schemas had lost the `release_tag` pattern.
- zstd handling: partially fixed. The subprocess is killed and reaped, but generic decompression errors can still mask the original archive-validation error; this remains outside the release blockers after the follow-up fixes.

## Follow-Up Opus Findings

Opus reran against the updated PR #60 diff and reported five release blockers:

- Directory archive members made extraction fail on conventionally produced tarballs because directories were chmodded read-only before later members were written.
- `rollback_data` did not enforce the same private data-root precondition as materialization, even though rollback trusts sidecar files in that root.
- Self-referential `previous_known_good_digest` was accepted on every rollout, not just the first bootstrap materialization.
- `DataReleaseTag` lost its JSON Schema `pattern` due to `Annotated` ordering, weakening the published schema contract.
- `data.py` and `data_materialization.py` formed an import cycle if `data_materialization` was imported first.

## Follow-Up Dispositions

- Directory members: fixed. Extraction keeps directories owner-writable during staging, records the final clamped modes, applies them bottom-up after all members are written, and cleans staging trees through a chmod-and-retry removal path.
- Rollback data-root trust: fixed. `rollback_data` now calls `_ensure_private_data_root` before taking the materialization lock, and identity sidecars must be private regular files owned by the effective user.
- Previous-known-good bootstrap: fixed. Self-referential previous-known-good is now accepted only when the data root has no other retained immutable versions; later rollouts must point to a distinct retained target.
- Published tag schema: fixed. `DataReleaseTag` now emits a JSON Schema pattern and mutable-label denylist through `WithJsonSchema`; checked-in schemas were regenerated.
- Import cycle: fixed. `data.py` owns only data models; runtime materialization functions are imported from `data_materialization.py` by callers.

## Verification Evidence

- `uv run pytest -q tests/release/test_data_release.py --tb=short`: 52 passed.
- `uv run ruff check genefoundry_router/release/data.py genefoundry_router/release/data_materialization.py genefoundry_router/release/cli.py genefoundry_router/release/models.py tests/release/test_data_release.py --output-format=github`: passed.
- `uv run mypy genefoundry_router/release/data.py genefoundry_router/release/data_materialization.py genefoundry_router/release/cli.py genefoundry_router/release/models.py`: passed.
- `uv run python scripts/check_file_size.py`: passed.
- `uv run pytest -q tests/release/test_models.py tests/release/test_model_schema.py tests/release/test_model_hardening.py --tb=short`: 206 passed.
- `uv run pytest tests/release -q`: 952 passed.
- `/tmp/actionlint -color=false .github/workflows/_container-ci.yml .github/workflows/_container-release.yml`: passed.
- `git diff --check`: passed.
- `make ci-local`: passed.

## Final Verdict

Follow-up blockers were fixed and verified locally. Final Opus rerun was stopped by maintainer direction on 2026-07-13; merge proceeded on local verification and GitHub CI.
