# P0 Runtime Data Identity Implementation Plan

> Historical record — this plan records the approved 2026-07-18 implementation sequence. Current
> behavior is defined by implemented controls, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an adopted data-bound release prove that its published container serves the exact materialized data identity declared by the release manifest, beginning with ClinGen and without blocking unadopted backends.

**Architecture:** A stdlib-only canonical helper creates and verifies a sorted runtime-input manifest whose SHA-256 is the data identity. The router release library validates the `release_identity` readiness fragment and seals the observed identity into definition evidence. The reusable workflow invokes that verifier before publication and after pulling the published OCI digest. `data_identity_contract: "runtime-v1"` is an explicit per-backend opt-in; ClinGen is the first adopter, while other data-bound repositories remain visibly `unadopted` until their own PRs migrate them.

**Tech Stack:** Python 3.12, standard-library `hashlib`/`json`/`pathlib`, Pydantic v2, Typer, GitHub Actions, Docker Compose, pytest, `uv`, Ruff, mypy.

---

## File structure

### Router repository

- Create: `docs/conformance/runtime_data_identity.py` — byte-identical canonical manifest writer/verifier for backend runtime use.
- Create: `tests/conformance/test_runtime_data_identity.py` — fixture-driven tests of canonicalization, traversal, extra-file, and corruption rejection.
- Create: `genefoundry_router/release/runtime_identity.py` — strict readiness-fragment parser and manifest/config comparison for release evidence.
- Modify: `genefoundry_router/release/definitions.py` — accept a verifier-produced observed identity rather than configuration-derived CLI flags.
- Modify: `genefoundry_router/release/cli.py` — add `verify-runtime-data-identity` and adapt `capture-definitions`.
- Modify: `genefoundry_router/release/models.py` — explicit `data_identity_contract` adoption state on `ReleaseConfig`.
- Modify: `genefoundry_router/release/evidence.py` — distinguish an observed `runtime-v1` identity from legacy capture data.
- Modify: `genefoundry_router/data/container-release.schema.json` — checked-in schema synchronized with `ReleaseConfig`.
- Modify: `.github/workflows/_container-release.yml` — pre-publish and published-digest verifier call sites.
- Modify: `tests/release/test_definitions.py`, `tests/release/test_evidence.py`, `tests/release/test_cli.py`, `tests/release/test_models.py`, `tests/release/test_model_schema.py`, and `tests/release/test_container_release_workflow.py` — focused TDD coverage.
- Create: `ci/data-identity-rollout-v1.json` — all data-bound repositories with explicit `unadopted` or `runtime-v1` state.
- Create: `tests/release/test_data_identity_rollout.py` — validates complete coverage and that only adopted rows can claim observed evidence.

### ClinGen canary repository (`/home/bernt-popp/development/clingen-link`)

- Create: `clingen_link/runtime_data_identity.py` — byte-identical vendored copy of the router canonical helper.
- Modify: `clingen_link/store/db.py` — write `data-identity-manifest.json` beside the selected materialized snapshot.
- Modify: `clingen_link/config.py` — configure the expected release tag and identity digest.
- Modify: `clingen_link/server_manager.py` — expose the strict `release_identity` readiness fragment only when materialization verification succeeds.
- Modify: `docker/ci-prepare-smoke.sh`, `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`, `docker/docker-compose.npm.yml`, `.env.docker.example`, and `container-release.json` — carry the same expected release tag/digest into every deployed profile and opt into `runtime-v1`.
- Modify: `tests/unit/store/test_bundle_verification.py`, `tests/unit/test_server_manager.py`, and `tests/unit/test_compose_hardening.py` — prove derivation, corruption failure, and Compose parity.

## Contract to implement

`data-identity-manifest.json` has this exact v1 shape before canonical serialization:

```json
{
  "schema_version": 1,
  "release_tag": "data-clingen-2026-07-16",
  "inputs": [
    {
      "path": "clingen.sqlite",
      "size_bytes": 123456,
      "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

Canonical bytes are UTF-8 JSON with sorted object keys, `ensure_ascii=False`, no insignificant
whitespace, and `allow_nan=False`. `inputs` are lexically ascending POSIX relative paths. The
identity digest is `sha256:` plus the lowercase SHA-256 of those canonical bytes. Runtime
verification reads that manifest, rejects keys outside the exact v1 shape, rejects absolute/traversing
paths, symlinks, missing inputs, byte-length mismatches, content-hash mismatches, or an extra regular
file in the materialized data root, then recomputes the same canonical digest. The manifest itself is
metadata and is excluded from the input inventory.

### Task 1: Build and test the canonical runtime-data helper

**Files:**

- Create: `tests/conformance/test_runtime_data_identity.py`
- Create: `docs/conformance/runtime_data_identity.py`

- [ ] **Step 1: Write the failing canonicalization and corruption tests**

Create `tests/conformance/test_runtime_data_identity.py` using a temporary data root:

```python
from __future__ import annotations

import json
from pathlib import Path

import pytest

from docs.conformance.runtime_data_identity import (
    RuntimeDataIdentityError,
    build_identity_manifest,
    verify_runtime_identity,
)


def test_runtime_identity_is_the_digest_of_a_canonical_manifest(tmp_path: Path) -> None:
    data = tmp_path / "clingen.sqlite"
    data.write_bytes(b"reference-snapshot")
    manifest = build_identity_manifest(tmp_path, "data-clingen-2026-07-16", [data])
    (tmp_path / "data-identity-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    identity = verify_runtime_identity(tmp_path)

    assert identity["release_tag"] == "data-clingen-2026-07-16"
    assert identity["digest"].startswith("sha256:")


def test_runtime_identity_rejects_corrupted_materialized_input(tmp_path: Path) -> None:
    data = tmp_path / "clingen.sqlite"
    data.write_bytes(b"reference-snapshot")
    manifest = build_identity_manifest(tmp_path, "data-clingen-2026-07-16", [data])
    (tmp_path / "data-identity-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    data.write_bytes(b"corrupted-snapshot")

    with pytest.raises(RuntimeDataIdentityError, match="sha256"):
        verify_runtime_identity(tmp_path)
```

Also add parametrized failures for an input path of `../escape.sqlite`, unsorted `inputs`, an
unexpected `extra.sqlite`, a wrong `size_bytes`, an upper-case digest, and a symlink input.

- [ ] **Step 2: Run the helper tests to verify they fail**

Run:

```bash
uv run pytest tests/conformance/test_runtime_data_identity.py -q
```

Expected: FAIL because `docs.conformance.runtime_data_identity` does not exist.

- [ ] **Step 3: Implement the stdlib-only v1 helper**

Create `docs/conformance/runtime_data_identity.py` with these public names:

```python
class RuntimeDataIdentityError(ValueError):
    """A materialized data root cannot prove its runtime identity."""


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def build_identity_manifest(root: Path, release_tag: str, files: Sequence[Path]) -> dict[str, object]:
    """Create the exact v1 manifest from regular files beneath one materialized root."""


def verify_runtime_identity(root: Path) -> dict[str, str]:
    """Rehash every authoritative runtime file and return release_tag/digest on success."""
```

Implement all validation before returning an identity. Use `Path.resolve()` and
`relative_to(root.resolve())` to enforce containment; require `path.as_posix()` to equal the stored
path; reject non-regular files and symlinks; discover all regular files below `root` excluding the
manifest and require that set to equal the manifest inventory. Return only:

```python
{"release_tag": release_tag, "digest": f"sha256:{hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()}"}
```

- [ ] **Step 4: Run helper tests and quality checks**

Run:

```bash
uv run pytest tests/conformance/test_runtime_data_identity.py -q
uv run ruff check docs/conformance tests/conformance
```

Expected: PASS.

- [ ] **Step 5: Commit the canonical helper**

```bash
git add docs/conformance/runtime_data_identity.py tests/conformance/test_runtime_data_identity.py
git commit -m "feat(conformance): define runtime data identity v1"
```

### Task 2: Add an adopted-readiness verifier and seal observed evidence

**Files:**

- Create: `genefoundry_router/release/runtime_identity.py`
- Modify: `genefoundry_router/release/definitions.py`
- Modify: `genefoundry_router/release/evidence.py`
- Modify: `tests/release/test_definitions.py`
- Modify: `tests/release/test_evidence.py`

- [ ] **Step 1: Write failing release-library tests**

Add a fixture and assertions in `tests/release/test_definitions.py`:

```python
RUNTIME_IDENTITY = {
    "release_identity": {
        "schema_version": 1,
        "data_identity": {
            "expected": {"release_tag": "data-clingen-2026-07-16", "digest": "sha256:" + "a" * 64},
            "actual": {"release_tag": "data-clingen-2026-07-16", "digest": "sha256:" + "a" * 64},
        },
    }
}


def test_adopted_data_bound_capture_seals_observed_health_identity() -> None:
    observed = verify_readiness_data_identity(
        RUNTIME_IDENTITY,
        release_tag="data-clingen-2026-07-16",
        digest="sha256:" + "a" * 64,
        adoption="runtime-v1",
    )
    capture = capture_definitions(_tools(), context={"runtime": "published"}, observed_identity=observed)

    evidence = verify_definition_contract("data-bound", [capture], observed_identity=observed)

    assert evidence.data_identity == RUNTIME_IDENTITY["release_identity"]["data_identity"]["actual"]
```

Add failures for missing `release_identity`, unknown nested keys, `schema_version: 2`, mismatched
expected, mismatched actual, a data-independent config passed as adopted, and a `runtime-v1`
manifest with no readiness fragment. In `tests/release/test_evidence.py`, add a regression proving
that changing only the manifest's declared identity after capture makes assembly fail.

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
uv run pytest tests/release/test_definitions.py tests/release/test_evidence.py -q
```

Expected: FAIL because `verify_readiness_data_identity` and `observed_identity` do not exist.

- [ ] **Step 3: Implement strict readiness parsing and observed capture**

Create `genefoundry_router/release/runtime_identity.py` with strict Pydantic models using
`ConfigDict(extra="forbid", frozen=True)`:

```python
DataIdentityAdoption = Literal["unadopted", "runtime-v1"]


class RuntimeIdentityPair(StrictModel):
    release_tag: DataReleaseTag
    digest: Sha256Digest


class RuntimeDataIdentity(StrictModel):
    expected: RuntimeIdentityPair
    actual: RuntimeIdentityPair


class ReleaseIdentity(StrictModel):
    schema_version: Literal[1]
    data_identity: RuntimeDataIdentity


def verify_readiness_data_identity(
    readiness: Mapping[str, object], *, release_tag: str, digest: str, adoption: DataIdentityAdoption
) -> dict[str, str] | None:
    """Return an adopted observed identity, rejecting unprovable runtime-v1 readiness."""
```

For `unadopted`, return `None` and do not inspect an optional legacy health field. For `runtime-v1`,
require that the only accepted fragment is `readiness["release_identity"]`, validate it exactly,
then require both `expected` and `actual` to equal the declared `release_tag`/`digest`. Return a
plain canonical copy of `actual` only after all checks pass.

Change `DefinitionCapture`, `capture_definitions`, and `verify_definition_contract` to use one
`observed_identity: Mapping[str, str] | None` instead of `data_release_tag`/`data_digest`. Preserve
legacy behavior only for an explicitly `unadopted` data-bound release. In `evidence.py`, require
an observed identity for `runtime-v1` and compare sealed evidence to the sealed data requirements;
never reread `container-release.json` during assembly.

- [ ] **Step 4: Run focused release tests**

Run:

```bash
uv run pytest tests/release/test_definitions.py tests/release/test_evidence.py -q
uv run mypy genefoundry_router/release
```

Expected: PASS; a configuration-copy payload passes only if the backend's independent runtime
corruption test proves it came from the canonical helper.

- [ ] **Step 5: Commit observed-evidence semantics**

```bash
git add genefoundry_router/release/runtime_identity.py genefoundry_router/release/definitions.py \
  genefoundry_router/release/evidence.py tests/release/test_definitions.py tests/release/test_evidence.py
git commit -m "feat(release): seal observed runtime data identity"
```

### Task 3: Make adoption explicit in release configuration and CLI

**Files:**

- Modify: `genefoundry_router/release/models.py:400-435`
- Modify: `genefoundry_router/release/cli.py:371-415`
- Modify: `genefoundry_router/data/container-release.schema.json`
- Modify: `tests/release/test_models.py`
- Modify: `tests/release/test_model_schema.py`
- Modify: `tests/release/test_cli.py`
- Create: `ci/data-identity-rollout-v1.json`
- Create: `tests/release/test_data_identity_rollout.py`

- [ ] **Step 1: Write failing configuration, CLI, and rollout-ledger tests**

Add tests that reject these invalid states:

```python
{"definitions": {"contract": "data-independent"}, "data_identity_contract": "runtime-v1"}
{"definitions": {"contract": "data-bound"}}
{"definitions": {"contract": "data-bound"}, "data_identity_contract": "unknown"}
```

The first must fail because only data-bound services may adopt; the second must fail because every
data-bound config needs an explicit adoption state; the third must fail the literal enum. Add a CLI
test that writes `RUNTIME_IDENTITY` to `health.json`, invokes
`verify-runtime-data-identity --config config.json --health health.json --out observed.json`, and
asserts `observed.json` is the canonical `actual` pair.

Create `tests/release/test_data_identity_rollout.py` to assert the rollout file has exactly the
data-bound repository names from `ci/fleet-application-releases.json`, each status is
`unadopted` or `runtime-v1`, and only a `runtime-v1` entry can contain `verified_commit` and a
non-null `observed_identity_sha256`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/release/test_models.py tests/release/test_model_schema.py tests/release/test_cli.py \
  tests/release/test_data_identity_rollout.py -q
```

Expected: FAIL because neither the config field nor the CLI command/rollout ledger exists.

- [ ] **Step 3: Implement the explicit adoption gate**

Add this field to `ReleaseConfig` in `genefoundry_router/release/models.py`:

```python
data_identity_contract: Literal["unadopted", "runtime-v1"] | None = None
```

Add a model validator with these exact rules:

1. `data-independent` and `mode == "none"` require `data_identity_contract is None`.
2. `data-bound` requires `data_identity_contract` to be exactly `"unadopted"` or `"runtime-v1"`.
3. `runtime-v1` additionally requires an exact data release tag/digest, as already required for a
   data-bound definitions contract.

Add Typer command `verify-runtime-data-identity` taking `--config`, `--health`, and `--out`. It
loads `ReleaseConfig`, calls `verify_readiness_data_identity`, writes only the observed pair using
`write_json_atomic`, and exits non-zero with the existing JSON CLI error shape on every mismatch.
Make `capture-definitions` take `--observed-identity FILE` for `runtime-v1`, retaining
`--data-release-tag`/`--data-digest` only when the config's state is `unadopted`.

Create `ci/data-identity-rollout-v1.json` with a top-level `schema_version: 1` and one entry for
every current data-bound backend. Set `clingen-link` to `runtime-v1` only after Task 5; set every
other current data-bound backend to `unadopted` with `verified_commit`, `observed_identity_sha256`,
and `evidence` all `null`.

Regenerate `genefoundry_router/data/container-release.schema.json` from
`ReleaseConfig.model_json_schema()` in the same deterministic JSON formatting used by the existing
checked-in schema, then run the schema parity tests.

- [ ] **Step 4: Run focused configuration verification**

Run:

```bash
uv run pytest tests/release/test_models.py tests/release/test_model_schema.py tests/release/test_cli.py \
  tests/release/test_data_identity_rollout.py -q
```

Expected: PASS. A data-bound service cannot silently omit its adoption state.

- [ ] **Step 5: Commit the adoption control plane**

```bash
git add genefoundry_router/release/models.py genefoundry_router/release/cli.py \
  genefoundry_router/data/container-release.schema.json ci/data-identity-rollout-v1.json \
  tests/release/test_models.py tests/release/test_model_schema.py tests/release/test_cli.py \
  tests/release/test_data_identity_rollout.py
git commit -m "feat(release): gate runtime data identity adoption"
```

### Task 4: Invoke the verifier at both release trust boundaries

**Files:**

- Modify: `.github/workflows/_container-release.yml:430-465`
- Modify: `.github/workflows/_container-release.yml:825-915`
- Modify: `tests/release/test_container_release_workflow.py`

- [ ] **Step 1: Write failing workflow contract tests**

In `tests/release/test_container_release_workflow.py`, assert the reusable workflow:

```python
assert "verify-runtime-data-identity --config container-release.json" in text
assert "release-gate-health.json" in text
assert "a-health.json" in text
assert "--observed-identity" in text
assert "--data-release-tag" not in capture_section_for_runtime_v1
```

Use a helper that slices only the `if [ "$contract" = "data-bound" ] && [
"$data_identity_contract" = "runtime-v1" ]` branch, so the explicit legacy branch may retain the
old flags during staged adoption.

- [ ] **Step 2: Run the workflow test to verify it fails**

Run:

```bash
uv run pytest tests/release/test_container_release_workflow.py -q
```

Expected: FAIL because the workflow has no readiness verifier invocation.

- [ ] **Step 3: Wire pre-publish and published-digest verification**

Immediately after the local health curl writes `$RUNNER_TEMP/release-gate-health.json`, read
`definitions.contract` and `data_identity_contract` from `container-release.json`. For only
`data-bound` plus `runtime-v1`, run:

```bash
uv run --project .container-release-tools \
  python .container-release-tools/scripts/container_release.py verify-runtime-data-identity \
  --config container-release.json \
  --health "$RUNNER_TEMP/release-gate-health.json" \
  --out "$RUNNER_TEMP/smoke-observed-data-identity.json"
```

In the published-digest capture, after `capture_tools a` has written `a-health.json`, invoke the
same command with `--health "$RUNNER_TEMP/capture/a-health.json"` and
`--out "$RUNNER_TEMP/capture/observed-data-identity.json"`. Pass that output to
`capture-definitions --observed-identity` only in the adopted branch. Preserve the current direct
`--data-release-tag`/`--data-digest` path only in the explicit `unadopted` branch. When projecting
`data-requirements.json`, include the exact top-level adoption state so assembly can distinguish
legacy from observed evidence.

- [ ] **Step 4: Run workflow and release-library tests**

Run:

```bash
uv run pytest tests/release/test_container_release_workflow.py tests/release/test_cli.py \
  tests/release/test_definitions.py tests/release/test_evidence.py -q
make lint-actions
```

Expected: PASS. No complete health payload is printed; only the canonical identity file is sealed.

- [ ] **Step 5: Commit workflow integration**

```bash
git add .github/workflows/_container-release.yml tests/release/test_container_release_workflow.py
git commit -m "ci(release): verify observed runtime data identity"
```

### Task 5: Adopt ClinGen as the corruption-tested canary

**Files:**

- Create: `/home/bernt-popp/development/clingen-link/clingen_link/runtime_data_identity.py`
- Modify: `/home/bernt-popp/development/clingen-link/clingen_link/store/db.py`
- Modify: `/home/bernt-popp/development/clingen-link/clingen_link/config.py`
- Modify: `/home/bernt-popp/development/clingen-link/clingen_link/server_manager.py`
- Modify: `/home/bernt-popp/development/clingen-link/container-release.json`
- Modify: `/home/bernt-popp/development/clingen-link/docker/ci-prepare-smoke.sh`
- Modify: `/home/bernt-popp/development/clingen-link/docker/docker-compose.yml`
- Modify: `/home/bernt-popp/development/clingen-link/docker/docker-compose.prod.yml`
- Modify: `/home/bernt-popp/development/clingen-link/docker/docker-compose.npm.yml`
- Modify: `/home/bernt-popp/development/clingen-link/.env.docker.example`
- Modify: `/home/bernt-popp/development/clingen-link/tests/unit/store/test_bundle_verification.py`
- Modify: `/home/bernt-popp/development/clingen-link/tests/unit/test_server_manager.py`
- Modify: `/home/bernt-popp/development/clingen-link/tests/unit/test_compose_hardening.py`
- Modify: `ci/data-identity-rollout-v1.json`

- [ ] **Step 1: Write failing ClinGen runtime and health tests**

Copy `docs/conformance/runtime_data_identity.py` byte-for-byte to
`clingen_link/runtime_data_identity.py`. In the ClinGen test suite, first assert the SHA-256 of the
vendored file equals the router canonical file SHA recorded in the adoption PR. Add:

```python
async def test_health_emits_runtime_v1_release_identity(injected_services: ClingenServices) -> None:
    app = await _manager()._create_fastapi_app(ServerConfig(transport="unified"))
    health_route = next(route for route in app.routes if getattr(route, "path", None) == "/health")

    async with app.router.lifespan_context(app):
        body = await health_route.endpoint()

    identity = body["release_identity"]["data_identity"]
    assert body["status"] == "healthy"
    assert identity["expected"] == identity["actual"]
    assert identity["actual"]["digest"].startswith("sha256:")
```

Add a store test that materializes a valid snapshot, flips one byte in `clingen.sqlite`, and asserts
the shared runtime verifier raises rather than returning the configured digest. Add Compose tests
that each of the base, prod, and NPM profiles receives the same release tag and identity digest.

- [ ] **Step 2: Run the ClinGen focused tests to verify they fail**

Run from `/home/bernt-popp/development/clingen-link`:

```bash
uv run pytest tests/unit/store/test_bundle_verification.py tests/unit/test_server_manager.py \
  tests/unit/test_compose_hardening.py -q
```

Expected: FAIL because no runtime identity manifest or `release_identity` fragment exists.

- [ ] **Step 3: Implement ClinGen manifest materialization and readiness**

After `materialize_bundle()` has atomically selected `clingen.sqlite`, write
`data-identity-manifest.json` to the same data root with `build_identity_manifest`. Supply the
immutable release tag from `CLINGEN_LINK_DATA_RELEASE_TAG`; set
`CLINGEN_LINK_DATA_IDENTITY_DIGEST` to the expected manifest digest, and validate both in
`Settings`. Do not derive `actual` from either setting: `server_manager.py` must call
`verify_runtime_identity(settings.data_root)` and only then build:

```python
"release_identity": {
    "schema_version": 1,
    "data_identity": {
        "expected": {
            "release_tag": settings.data_release_tag,
            "digest": settings.data_identity_digest,
        },
        "actual": verify_runtime_identity(Path(settings.data_root)),
    },
}
```

If verification raises, retain the existing 503 degraded readiness behavior and do not emit a
partial identity. Add `"data_identity_contract": "runtime-v1"` to ClinGen's
`container-release.json`; change its data digest to the canonical identity-manifest digest, update
the data-release asset/metadata that records it, and propagate the exact tag/digest through all
Compose profiles and `docker/ci-prepare-smoke.sh`.

- [ ] **Step 4: Run ClinGen verification and a release canary**

Run from `/home/bernt-popp/development/clingen-link`:

```bash
make ci-local
```

Then publish a disposable reviewed canary tag through the reusable workflow. Verify its
published-digest artifact contains `mcp-capture-context.json` with the canonical observed
`data_identity`, and verify the generated application release manifest has equal declared and
observed identity values. Do not close #63 on a local-only result.

- [ ] **Step 5: Record adoption through two atomic commits**

In the ClinGen repository:

```bash
git add clingen_link/runtime_data_identity.py clingen_link/store/db.py clingen_link/config.py \
  clingen_link/server_manager.py container-release.json docker .env.docker.example tests
git commit -m "feat(release): attest ClinGen runtime data identity"
```

After the canary is published, in this router repository update the `clingen-link` entry in
`ci/data-identity-rollout-v1.json` to `runtime-v1`, its merged commit, and the SHA-256 of the
sealed observed-identity artifact:

```bash
git add ci/data-identity-rollout-v1.json
git commit -m "chore(release): record ClinGen data identity adoption"
```

### Task 6: Validate staged rollout semantics

**Files:**

- Modify: `ci/release-candidate-inventory.json` after a reviewed fleet capture
- Modify: `ci/fleet-application-releases.json` after the release manifest changes
- Modify: `genefoundry_router/data/fleet-baseline.json` only through the reviewed snapshot process

- [ ] **Step 1: Write a failing candidate-inventory test for adoption state**

Extend `tests/integration/test_release_candidate_baseline.py` so a `runtime-v1` backend requires a
sealed observed identity in its application release evidence, while `unadopted` backends remain
explicitly legacy and cannot claim a runtime identity.

- [ ] **Step 2: Run the test to verify it fails before capture refresh**

Run:

```bash
uv run pytest tests/integration/test_release_candidate_baseline.py -q
```

Expected: FAIL until the post-canary candidate inventory and application release evidence are
captured from the published ClinGen digest.

- [ ] **Step 3: Refresh only reviewed release evidence**

After the ClinGen canary has passed, run from the router repository with the fleet endpoints
available:

```bash
make release-candidate
make snapshot-fleet
```

Inspect the diff before staging. The capture must preserve exact identity for every endpoint and
must not silently carry a stale prior entry for an unreachable backend.

- [ ] **Step 4: Run full router verification**

Run:

```bash
make ci-local
uv run pytest tests/integration/test_release_candidate_baseline.py -q
```

Expected: PASS. Unadopted data-bound entries remain listed, while only ClinGen is required to
produce a `runtime-v1` observed readiness fragment.

- [ ] **Step 5: Commit reviewed fleet evidence**

```bash
git add ci/release-candidate-inventory.json ci/fleet-application-releases.json \
  genefoundry_router/data/fleet-baseline.json tests/integration/test_release_candidate_baseline.py
git commit -m "chore(release): capture ClinGen runtime identity canary"
```
