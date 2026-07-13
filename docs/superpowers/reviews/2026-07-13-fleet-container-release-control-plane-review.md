# Fleet Container Release Control Plane Review

Date: 2026-07-13
PR: https://github.com/berntpopp/genefoundry-router/pull/59
Reviewed head: 37a7050bf030227d8d037865805b1e28d7f9d531
Reviewer command: `claude -p --model claude-opus-4-8 --effort xhigh`

## Verdict

PASS.

No release blockers remain.

## Review Attempts

Two full-repository autonomous Opus invocations stalled with no useful output. A focused packet review then produced one substantiated blocker: `_container-release.yml` passed `inspect-oci --image-allowlist`, while the release CLI only exposes `--allowlist`.

Disposition: fixed in `37a7050` by changing the release workflow to `allowlist_args+=(--allowlist "$allowed_path")` and adding a regression assertion in `tests/release/test_container_release_workflow.py`.

Final bounded Opus review returned PASS after the fix and stated: "No release blockers remain."

## Required Finding Dispositions

- B1, Trivy evidence envelope mismatch: closed. CI and release workflows generate native Trivy JSON plus `trivy version --format json`, assemble `{schema_version: 1, scan, version}`, and the evaluator consumes the envelope.
- B2, release artifact path mismatch: closed. Release build paths are written from a setup step through `$GITHUB_ENV`/`$GITHUB_OUTPUT`; job-level env no longer uses unavailable `runner` context or hard-coded `/tmp/release-build`.
- H2, release-control ledger: closed for publication gating. The release workflow invokes `require_compliant_controls(load_control_ledger(...), expected_fleet_repositories(...))` before publication jobs. The checked-in ledger still contains explicit unavailable rows until live controls are configured, so publication fails closed until the ledger is populated.
- H3, MCP evidence authenticity: closed. Release capture runs the exact published digest twice, initializes MCP, conditionally sends `Mcp-Session-Id`, calls real `tools/list`, requires a non-empty `.result.tools` array, and removes fabricated empty arrays.
- Build metadata: closed. CI and release builds pass `APP_VERSION`, `VCS_REF`, and deterministic `BUILD_DATE`.
- `tests/release` in `make ci-local`: closed. `make ci-local` runs `test-release`, and local plus GitHub CI pass the release suite.
- Pinned GitHub CLI support: closed. The pinned `gh` install checks `release verify`, `release verify-asset`, `attestation verify`, `attestation download`, and `attestation trusted-root` before publish/finalize operations.
- Draft comparison: closed. Finalize compares the downloaded draft release against the exact final release asset directory including `application-release-manifest.json`.
- Offline trust anchoring: closed. Attestation evidence captures `gh attestation download` output and `gh attestation trusted-root` output instead of fabricated placeholder trust material.
- OCI allowlist CLI flag: closed. `_container-release.yml` now uses the supported `--allowlist` flag; workflow tests reject `--image-allowlist`.

## Verification Evidence

- `uv run pytest -q tests/release/test_container_release_workflow.py tests/release/test_cli.py::test_inspect_oci_success_wires_layout_and_allowlist --tb=short`: 13 passed.
- `/tmp/actionlint -color=false .github/workflows/_container-ci.yml .github/workflows/_container-release.yml`: passed.
- `uv run ruff check genefoundry_router tests --output-format=github`: passed.
- `git diff --check`: passed.
- `uv run pytest tests/release -q`: 900 passed.
- `make ci-local`: passed.
- GitHub PR #59 at `37a7050`: CI, Container CI, and Security passed.

## Residual Notes

No release blockers remain. The live fleet control ledger must be populated with verified GitHub/GHCR values before any release publication can proceed; the workflow intentionally fails closed until that happens.
