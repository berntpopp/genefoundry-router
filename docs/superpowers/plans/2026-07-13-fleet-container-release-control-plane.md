# Fleet Container Release Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and pilot the router-owned schemas, validators, evidence tools, reusable workflows, and deployment verifier that publish one gated code-only AMD64 image from an exact protected source tag.

**Architecture:** A focused `genefoundry_router.release` package owns pure validation and evidence logic; thin scripts expose it to reusable GitHub workflows. CI executes repository code with read-only permissions, transfers one verified Docker archive to a non-executing publisher, and finalizes a release only after digest, attestation, SBOM, vulnerability, and MCP-definition evidence pass.

**Tech Stack:** Python 3.12, Pydantic 2, PyYAML, Docker Buildx/Compose, Trivy JSON, Syft SPDX JSON, GitHub Actions reusable workflows, GHCR, GitHub artifact attestations, pytest, ruff, mypy, actionlint.

---

## File map

- `genefoundry_router/release/models.py`: strict configuration and evidence models.
- `genefoundry_router/release/source.py`: SemVer, tag, version, ancestry, and collision validation.
- `genefoundry_router/release/compose.py`: rendered Compose policy validation.
- `genefoundry_router/release/content.py`: OCI-layer/config and build-context content policy.
- `genefoundry_router/release/vulnerabilities.py`: Trivy JSON operational/policy separation.
- `genefoundry_router/release/definitions.py`: canonical MCP capture and definition contracts.
- `genefoundry_router/release/evidence.py`: SHA-256 manifests and release-manifest assembly.
- `genefoundry_router/release/deploy.py`: online and offline deployment verification.
- `genefoundry_router/release/cli.py`: stable workflow-facing subcommands.
- `genefoundry_router/data/*.schema.json`: packaged JSON Schemas.
- `scripts/container_release.py`: repository-local executable entry point.
- `.github/workflows/_container-ci.yml`: read-only reusable image CI.
- `.github/workflows/_container-release.yml`: isolated multi-job release state machine.
- `.github/workflows/container-ci.yml`: router CI caller.
- `.github/workflows/container-release.yml`: router stable-tag-push caller.
- `container-release.json`: router release configuration.
- `ci/container-controls.json`: generated repository/package control ledger.
- `tests/release/`: unit, workflow-static, integration, and synthetic-image tests.

### Task 1: Strict configuration and release-manifest models

**Files:**
- Create: `genefoundry_router/release/__init__.py`
- Create: `genefoundry_router/release/models.py`
- Create: `genefoundry_router/data/container-release.schema.json`
- Create: `genefoundry_router/data/application-release-manifest.schema.json`
- Create: `tests/release/test_models.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Write failing model tests**

Add table-driven tests that load the router example, reject unknown keys, reject shell-valued preparation, require `linux/amd64`, distinguish `none`, `external-reference`, `restored-database`, and `upstream-live`, model runtime cache separately, and require `definitions.contract` to be `data-independent` or `data-bound`.

```python
def test_release_config_rejects_unknown_key(valid_config: dict[str, object]) -> None:
    valid_config["unexpected"] = True
    with pytest.raises(ValidationError):
        ReleaseConfig.model_validate(valid_config)


def test_data_bound_requires_exact_data_identity(valid_config: dict[str, object]) -> None:
    valid_config["definitions"] = {"contract": "data-bound"}
    valid_config["data"] = {"mode": "external-reference", "image_allowlist": []}
    with pytest.raises(ValidationError, match="release_tag.*sha256"):
        ReleaseConfig.model_validate(valid_config)
```

- [ ] **Step 2: Prove the tests fail**

Run: `uv run pytest tests/release/test_models.py -q`

Expected: collection fails because `genefoundry_router.release.models` does not exist.

- [ ] **Step 3: Implement strict Pydantic models and schemas**

Use `ConfigDict(extra="forbid", frozen=True)`, `Literal` enums, SHA/tag regexes, and a model validator that enforces exact data identity for `data-bound`. Export both schemas with `model_json_schema()` through a checked-in generation test; package them through `force-include` entries in `pyproject.toml`.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/release/test_models.py -q && uv run mypy genefoundry_router`

Expected: all model tests pass and mypy reports no issues.

```bash
git add genefoundry_router/release genefoundry_router/data pyproject.toml tests/release/test_models.py
git commit -m "feat(release): add strict container release contracts"
```

### Task 2: Exact source-release validation

**Files:**
- Create: `genefoundry_router/release/source.py`
- Create: `tests/release/test_source.py`

- [ ] **Step 1: Write failing tests for tag-push-only identity**

Test stable SemVer only, tag/package mismatch, changelog absence, non-ancestor tags, remote tag movement, version downgrade, branch dispatch rejection, and rejection of pre-adoption tags whose tree lacks the release configuration/caller.

```python
@pytest.mark.parametrize("tag", ["v1", "v1.2", "1.2.3", "v1.2.3-rc.1", "v1.2.3+local"])
def test_parse_release_tag_rejects_non_stable_semver(tag: str) -> None:
    with pytest.raises(SourceReleaseError):
        parse_release_tag(tag)


def test_release_source_requires_tag_push() -> None:
    with pytest.raises(SourceReleaseError, match="tag push"):
        resolve_event_tag("workflow_dispatch", "refs/heads/main")
```

- [ ] **Step 2: Prove failure, implement, and verify**

Run before implementation: `uv run pytest tests/release/test_source.py -q`

Expected: import failure for `source.py`.

Implement pure parsing plus a command-runner interface for `git merge-base --is-ancestor`, `git rev-parse refs/tags/{validated_tag}^{commit}`, remote tag lookup, `uv version --short`, and previous stable tag comparison. Never interpolate an unvalidated tag into a command string; pass argument arrays.

Run after implementation: `uv run pytest tests/release/test_source.py -q`

Expected: all source-release tests pass.

- [ ] **Step 3: Commit**

```bash
git add genefoundry_router/release/source.py tests/release/test_source.py
git commit -m "feat(release): validate protected source release identity"
```

### Task 3: Rendered Compose contract

**Files:**
- Create: `genefoundry_router/release/compose.py`
- Create: `tests/release/test_compose.py`
- Modify: `docker/docker-compose.prod.yml`

- [ ] **Step 1: Write synthetic rendered-Compose tests**

Require the application service to have no `build`, a digest image, no published ports, read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges:true`, PID/resource/log limits, explicit writable mounts, and no Docker socket, host network, or privilege escalation. Reject image tags and any effective inherited build.

```python
def test_production_rejects_effective_build(valid_render: dict[str, object]) -> None:
    service = valid_render["services"]["genefoundry"]
    service["build"] = {"context": "."}
    assert "services.genefoundry.build" in validate_compose(valid_render, "genefoundry")


def test_production_requires_digest(valid_render: dict[str, object]) -> None:
    valid_render["services"]["genefoundry"]["image"] = "ghcr.io/berntpopp/genefoundry-router:0.6.4"
    assert "digest" in " ".join(validate_compose(valid_render, "genefoundry"))
```

- [ ] **Step 2: Implement and update router production overlay**

Parse `docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config --format json`; validate the merged object. Set the router application service to `build: !reset null`, `image: ${GENEFOUNDRY_IMAGE:?set a verified digest-addressed image}`, and `pull_policy: missing` in production. Keep local development build behavior in the base file.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_compose.py tests/unit/test_deployment_profiles.py -q`

Expected: all tests pass and rendered production config contains no effective application build.

```bash
git add genefoundry_router/release/compose.py tests/release/test_compose.py docker/docker-compose.prod.yml tests/unit/test_deployment_profiles.py
git commit -m "feat(release): enforce digest-only production compose"
```

### Task 4: Positive image-content and build-context policy

**Files:**
- Create: `genefoundry_router/release/content.py`
- Create: `genefoundry_router/data/image-content-policy-v1.json`
- Create: `tests/release/test_content.py`
- Modify: `.dockerignore`

- [ ] **Step 1: Write failing OCI-layer/config policy tests**

Build synthetic OCI layouts and assert denial of `.env`, private keys, `.git`, SQLite, compressed datasets, VCF/BCF, parquet, corpus paths, and oversized unexpected files in every layer, including files later removed by whiteout. Reject secret-shaped config environment/history/commands and root users. Assert exact-path allowlisting permits a small SQL schema but does not permit its parent `data/` tree; centrally cap allowlist extensions, media types, entry count, and aggregate bytes.

```python
def test_database_hidden_under_package_is_denied(tmp_path: Path) -> None:
    archive = make_tar(tmp_path, {"opt/app/pkg/data/reference.sqlite": b"SQLite format 3\x00"})
    result = inspect_rootfs(archive, ContentPolicy.default(), ())
    assert result.denied_paths == ("opt/app/pkg/data/reference.sqlite",)
```

- [ ] **Step 2: Implement streaming OCI inspection**

Verify `index.json` and descriptor/blob digests, iterate every referenced layer tar without flattening, normalize POSIX paths, reject absolute/traversal/hardlink/device/FIFO/set-id escapes, treat whiteout-deleted denied paths as violations, inspect names plus magic bytes, and parse the image-config blob. Enforce per-file and aggregate limits without extracting untrusted archives, and return a deterministic JSON report with policy/allowlist digests, denied paths, allowlisted paths, and context size.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_content.py -q`

Expected: all malicious and allowed fixtures produce the asserted reports.

```bash
git add genefoundry_router/release/content.py genefoundry_router/data/image-content-policy-v1.json tests/release/test_content.py .dockerignore
git commit -m "feat(release): gate public images with positive content policy"
```

### Task 5: Trivy operational/policy separation

**Files:**
- Create: `genefoundry_router/release/vulnerabilities.py`
- Create: `tests/release/test_vulnerabilities.py`
- Modify: `docs/CONTAINER-HARDENING-STANDARD-v1.md`

- [ ] **Step 1: Write fixture tests**

Cover valid-clean JSON, valid fixable HIGH/CRITICAL findings, unfixable findings, stale database metadata, malformed JSON, missing metadata, and a non-zero scanner process result. Define one shared exit enum: 0 success, 1 policy violation, 2 invalid/incomplete evidence, and 3 infrastructure failure; every CLI subcommand and workflow consumes the JSON `verdict` rather than inferring meaning from a raw scanner exit.

- [ ] **Step 2: Implement the evaluator and update the standard**

Expose `evaluate-trivy --report trivy.json --scanner-exit scanner.exit --out verdict.json`. Record Trivy version and database update metadata. Change the standard from raw table/exit-code gating to JSON plus this evaluator; keep SARIF non-gating.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_vulnerabilities.py tests/unit/test_ci_container_security.py -q`

Expected: policy, infrastructure, and clean outcomes are distinct and the old raw-exit assertion is removed.

```bash
git add genefoundry_router/release/vulnerabilities.py tests/release/test_vulnerabilities.py tests/unit/test_ci_container_security.py docs/CONTAINER-HARDENING-STANDARD-v1.md
git commit -m "feat(release): separate Trivy evidence from policy verdict"
```

### Task 6: MCP definitions and release evidence

**Files:**
- Create: `genefoundry_router/release/definitions.py`
- Create: `genefoundry_router/release/evidence.py`
- Create: `tests/release/test_definitions.py`
- Create: `tests/release/test_evidence.py`

- [ ] **Step 1: Write failing canonicalization and manifest tests**

Reuse the router's canonical schema normalization. Require two different context-manifest hashes plus equal definition hashes for `data-independent`; require the exact data tag/digest for `data-bound`. Require full source SHA, image digest, workflow SHA, SBOM digest, scanner evidence, attestation bundle/root, and release-asset hashes.

- [ ] **Step 2: Implement deterministic evidence assembly**

Hash files in binary chunks, serialize JSON with sorted keys and compact separators before hashing, reject duplicate asset names, and validate the final document through `ApplicationReleaseManifest`. Write files atomically with mode `0644` and no ambient environment capture.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_definitions.py tests/release/test_evidence.py -q`

Expected: stable hashes, contract failures, and complete-manifest acceptance pass.

```bash
git add genefoundry_router/release/definitions.py genefoundry_router/release/evidence.py tests/release/test_definitions.py tests/release/test_evidence.py
git commit -m "feat(release): bind MCP definitions and release evidence"
```

### Task 7: Workflow-facing CLI and deployment verifier

**Files:**
- Create: `genefoundry_router/release/cli.py`
- Create: `genefoundry_router/release/deploy.py`
- Create: `scripts/container_release.py`
- Create: `tests/release/test_cli.py`
- Create: `tests/release/test_deploy.py`
- Modify: `Makefile`

- [ ] **Step 1: Write CLI and verifier tests**

Test every subcommand with `CliRunner`: `validate-config`, `validate-source`, `validate-compose`, `inspect-oci`, `evaluate-trivy`, `capture-definitions`, `assemble-manifest`, and `verify-deployment`. Mock command execution and require `gh attestation verify --signer-repo`, `--signer-workflow`, `--signer-digest`, `--source-ref`, `--source-digest`, `--predicate-type https://slsa.dev/provenance/v1`, and `--deny-self-hosted-runners`; use distinct source/signer repositories in the fixture. Test offline verification of local manifest bytes whose SHA-256 equals the OCI digest with a saved bundle/trusted root and pin/check the minimum GitHub CLI version supporting these flags plus `gh release verify`.

- [ ] **Step 2: Implement narrow CLI adapters**

CLI functions parse paths/arguments, call the pure modules, emit one JSON result, and use the shared exit enum from Task 5. Add `make container-validate`, `container-content`, and `container-deploy-verify` targets.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_cli.py tests/release/test_deploy.py -q && uv run python scripts/container_release.py --help`

Expected: tests pass and help lists all workflow-facing subcommands.

```bash
git add genefoundry_router/release/cli.py genefoundry_router/release/deploy.py scripts/container_release.py tests/release/test_cli.py tests/release/test_deploy.py Makefile
git commit -m "feat(release): add release tooling CLI and deploy verifier"
```

### Task 8: Read-only reusable container CI

**Files:**
- Create: `.github/workflows/_container-ci.yml`
- Create: `.github/workflows/container-ci.yml`
- Create: `container-release.json`
- Create: `tests/release/test_container_ci_workflow.py`
- Delete: `.github/workflows/container-security.yml`

- [ ] **Step 1: Write static workflow tests**

Parse YAML and assert `workflow_call`, caller path filters, workflow-level `permissions: {}`, full action SHAs, called-workflow identity checks, one `docker/build-push-action` invocation exporting an OCI layout with `push: false`, `provenance: false`, `sbom: false`, `platforms: linux/amd64`, Compose `--no-build`, JSON Trivy, SBOM, per-layer/config policy, hardening, MCP conformance, and unconditional teardown. Leaf-code jobs have only `contents: read`. If SARIF upload is retained, a separate no-checkout/non-executing report job alone has `security-events: write`.

- [ ] **Step 2: Implement the workflow with pinned actions**

Pin checkout `9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0`, Buildx `bb05f3f5519dd87d3ba754cc423b652a5edd6d2c`, build-push `53b7df96c91f9c12dcc8a07bcb9ccacbed38856a`, Trivy `a9c7b0f06e461e9d4b4d1711f154ee024b8d7ab8`, SBOM `e22c389904149dbc22b58101806040fa8d37a610`, CodeQL/SARIF `1ad29ea4a422cce9a242a9fae469541dcd08addc`, and upload-artifact `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a`. Pin/checksum the daemonless OCI import tool used to run the layout locally. Derive CI cache scope from repository, Dockerfile, platform, Buildx version, and lockfile hash. Configure the router as service `genefoundry`, port `8000`, data mode `none`, definition contract `data-independent`.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_container_ci_workflow.py tests/unit/test_workflows_parse.py -q && make lint-actions`

Expected: workflow tests pass; actionlint passes when installed.

```bash
git add .github/workflows/_container-ci.yml .github/workflows/container-ci.yml container-release.json tests/release/test_container_ci_workflow.py .github/workflows/container-security.yml
git commit -m "ci(release): consolidate container CI into one gated build"
```

### Task 9: Isolated reusable release workflow

**Files:**
- Create: `.github/workflows/_container-release.yml`
- Create: `.github/workflows/container-release.yml`
- Create: `tests/release/test_container_release_workflow.py`

- [ ] **Step 1: Write state-machine and permission tests**

Assert stable exact tag pushes only, runtime SemVer revalidation, `cancel-in-progress: false`, caller permission ceiling, workflow-level `permissions: {}`, and a six-job permission graph. `prepare`, `build-gate`, `capture`, and `assemble-evidence` are read-only; `publish-attest` and `finalize` are protected-environment, non-executing jobs with no checkout/leaf script/Compose/container run. Assert one build only when the source alias is absent, OCI-layout artifact/digest verification, tamper rejection before registry write, no release cache, SHA alias before attest, published-digest capture, draft publication before a manifest-identical version alias, and no `--clobber` or wrapper-index alias. Cover no prior image, existing matching source digest with re-gating/no rebuild, mismatched SHA alias, completed immutable release, missing attestation, matching/mismatched draft assets, and safe recreation of a missing version alias.

- [ ] **Step 2: Implement the six-job workflow**

Use the CI pins plus download-artifact `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c`, login `af1e73f918a031802d376d3c8bbc3fe56130a9b0`, attest-build-provenance `43d14bc2b83dec42d39ecae14e916627a18bb661`, and attest-sbom `51e74621a501c89df81fc1391c5a8f4cfc9fab2f`. Pin/checksum the current `gh` and `crane` (or `oras`) binaries. `prepare` selects new-build versus existing-digest recovery. `build-gate` exports/imports one exact OCI layout, calculates its manifest digest before credentials, runs the complete CI gate with no PR-writable cache, and uploads an immutable artifact. `publish-attest` performs a byte-preserving source-SHA push and proves digest equality before attestation. `capture` runs the published digest. `assemble-evidence` builds the final asset set read-only. `finalize` re-downloads/hashes draft assets, publishes, uses current `gh release verify`/`verify-asset`, then applies identical manifest bytes to the version tag and proves both aliases equal the attested digest.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_container_release_workflow.py tests/unit/test_workflows_parse.py -q && make lint-actions`

Expected: every privilege and ordering assertion passes.

```bash
git add .github/workflows/_container-release.yml .github/workflows/container-release.yml tests/release/test_container_release_workflow.py
git commit -m "ci(release): add isolated protected-tag publication workflow"
```

### Task 10: Package controls and fleet candidate provenance

**Files:**
- Create: `genefoundry_router/release/controls.py`
- Create: `ci/container-controls.json`
- Create: `tests/release/test_controls.py`
- Modify: `scripts/make_release_candidate.py`
- Modify: `scripts/snapshot_fleet.py`
- Modify: `tests/integration/test_release_candidate_baseline.py`

- [ ] **Step 1: Write control-ledger and candidate tests**

Require a row for the router plus all 21 `servers.yaml` repositories. Require active tag ruleset semantics/bypass actors, protected release environment, immutable release setting, linked public package, anonymous pull, no standing package PAT, retention status, review timestamp, and evidence URL. Distinguish API-verified controls from named manual evidence but fail when any hard prerequisite is unavailable. Extend each candidate backend with image name/digest, source tag/revision, workflow digest, SBOM/attestation identities, data mode/release/digest, and definition contract/context/digest; reject partial coverage atomically.

- [ ] **Step 2: Implement fail-closed parsers and capture**

Keep `make_release_candidate.py` online but make it consume verified application release manifests rather than a bare revision map. `snapshot_fleet.py` must compare live definitions to the manifest-bound digest and preserve the full release tuple in the packaged baseline.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_controls.py tests/integration/test_release_candidate_baseline.py tests/unit/test_snapshot_merge.py -q`

Expected: complete tuples pass; missing router/backend/evidence/data fields fail.

```bash
git add genefoundry_router/release/controls.py ci/container-controls.json tests/release/test_controls.py scripts/make_release_candidate.py scripts/snapshot_fleet.py tests/integration/test_release_candidate_baseline.py
git commit -m "feat(release): bind fleet candidates to verified image provenance"
```

### Task 11: Router pilot integration

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `docker/docker-compose.npm.yml`
- Modify: `README.md`
- Modify: `docs/CONTAINER-HARDENING-STANDARD-v1.md`
- Create: `tests/release/test_router_image_integration.py`

- [ ] **Step 1: Add failing Docker/Compose integration assertions**

Assert required OCI labels, full revision, code-only label, non-root/read-only execution, no denied path in any OCI layer and no secret-shaped image config/history, `/health`, MCP initialize/list-tools, production effective config with no build/host port, and a digest-only deployment verifier fixture.

- [ ] **Step 2: Make the router the reference implementation**

Add build args/labels without secrets, ensure the wheel contains only code/schema/baseline assets, render local smoke through the already-built tag, document bootstrap/public package controls, tag release, verification, rollback tuple, incident recovery, research-use-only limits, and the ARM64 enablement gates. AMD64 remains the only accepted v1 platform.

- [ ] **Step 3: Verify and commit**

Run: `uv run pytest tests/release/test_router_image_integration.py -q && make docker-prod-config && make docker-npm-config`

Expected: the built router image passes content/runtime/MCP checks and production render is digest-only.

```bash
git add docker README.md docs/CONTAINER-HARDENING-STANDARD-v1.md tests/release/test_router_image_integration.py
git commit -m "feat(release): make router the container release reference"
```

### Task 12: Control-plane verification and review checkpoint

**Files:**
- Create: `docs/superpowers/reviews/2026-07-13-fleet-container-release-control-plane-review.md`

- [ ] **Step 1: Run the complete local gate**

Run: `make ci-local && make test-cov && make docker-prod-config && make docker-npm-config`

Expected: format, lint, 600-LOC, mypy, unit/integration, coverage at least 70%, and both Compose renders pass.

- [ ] **Step 2: Run static release invariants**

Run: `uv run pytest tests/release -q && rg -n --hidden 'push:\s*true|--clobber|image:.*:latest|pull_policy:\s*always|docker save|docker load|imagetools create' .github docker container-release.json`

Expected: release tests pass; the search has no policy-violating application publication or production references.

- [ ] **Step 3: Obtain Opus adversarial implementation/PR review**

Run Claude Code with model `claude-opus-4-8`, effort `xhigh`, and a prompt containing the approved design, this plan, `git diff origin/main...HEAD`, workflow files, release tests, and verification output. Record every blocking/high finding, commit accepted fixes, and document evidence-backed rejections in the review file.

- [ ] **Step 4: Commit the review record**

```bash
git add docs/superpowers/reviews/2026-07-13-fleet-container-release-control-plane-review.md
git commit -m "docs: record adversarial control-plane review"
```
