# GeneFoundry Fleet Container Release Design

**Status:** Approved for implementation on 2026-07-13

**Scope:** `genefoundry-router` plus all 21 repositories registered in
`servers.yaml`

**Audience:** Fleet maintainers, release engineers, deployment operators, and
institutional security reviewers

## Summary

Every GeneFoundry repository will publish a public, versioned, code-only OCI
image to GitHub Container Registry (GHCR). A protected `vX.Y.Z` source tag is
the sole publication authority. Pull requests build and test an image but never
publish it. Release jobs build once, test and scan that exact image, push the
same bytes, attach signed GitHub provenance and an SBOM to the resulting digest,
then capture MCP definitions from the published digest.

Reference datasets and runtime state remain outside application images. Data
artifacts have an independent immutable release line and are selected in
production by exact version and digest. Production Compose configurations remove
all inherited `build:` declarations and accept only digest-addressed images.

The router repository owns the standard, schemas, validation tools, reusable
GitHub workflows, deployment verifier, and fleet reconciliation automation.
Each fleet repository contains only a small typed configuration and thin
SHA-pinned workflow callers.

This design strengthens deployment provenance and rollback. It does not solve
prompt injection in legitimate upstream content, prove that an image is free of
vulnerabilities, or turn research software into clinical decision support.

## Motivation and current evidence

The pasted proposal correctly identifies the benefits of a registry-backed
release chain:

```text
source commit
  -> CI
  -> versioned image
  -> vulnerability evidence
  -> SBOM and provenance
  -> immutable digest
  -> reviewed fleet inventory
  -> deployment by digest
```

The present fleet does not yet implement that chain:

- None of the 22 repositories has a standardized GHCR application-image
  publication workflow.
- Docker builds are frequently duplicated across `docker.yml`,
  `conformance.yml`, `container-security.yml`, and `release.yml`.
- The router production Compose configuration inherits a local `build:` block.
- Fourteen repositories have a `pyproject.toml` version newer than their newest
  `v*` source tag. A version-change publication trigger would therefore release
  fourteen previously unreleased versions immediately.
- `clingen-link` copies `clingen_link/data/clingen.sqlite.zst` into its runtime
  image.
- Several data services resolve a mutable `latest` data release in production.
- MaveDB's data workflow currently permits asset replacement with
  `gh release upload --clobber`.
- The router's Trivy job uses SARIF as the gating scan even though the canonical
  hardening standard documents that the SARIF/exit-code combination is not a
  reliable vulnerability gate.

The design treats these as release blockers, not documentation issues.

## Goals

1. Publish one public application image for the router and every registered
   `-link` repository.
2. Bind each image digest to one source revision, application version, build
   workflow revision, SBOM, provenance attestation, and MCP definition digest.
3. Keep reference data, patient-derived data, secrets, logs, caches, OAuth
   state, and persistent runtime volumes out of application images.
4. Make publication an explicit, protected, idempotent release transition.
5. Ensure the image tested and scanned is the image pushed and attested.
6. Deploy by verified digest with no fallback to a local build or mutable tag.
7. Preserve independent application and data release/rollback lifecycles.
8. Consolidate repeated GitHub Actions work and avoid unnecessary multi-platform
   emulation, repeated dataset downloads, and duplicate image builds.
9. Provide centrally maintained defaults with narrowly typed per-repository
   exceptions.
10. Extend the router's reviewed candidate inventory so image, data, and tool
    definition identity are atomic.

## Non-goals

- Publishing data-bearing application-image variants.
- Offering SSE transport or changing MCP runtime behavior.
- Forwarding caller authorization to backends.
- Automatically deploying every published image.
- Automatically publishing on any merge that changes `pyproject.toml`.
- Supporting ARM64 before every dependency and runtime contract is tested on
  ARM64.
- Claiming byte-for-byte reproducible builds while Debian package repositories,
  wheel selection, and build timestamps remain outside a hermetic build graph.
- Replacing runtime tool-definition drift detection.
- Treating container provenance as evidence that upstream biomedical content is
  trustworthy.

## Design principles

### Digests are identities; tags are aliases

Humans use exact version and source aliases:

```text
ghcr.io/berntpopp/gnomad-link:8.0.3
ghcr.io/berntpopp/gnomad-link:sha-<40-hex-source-revision>
```

Automation and production use:

```text
ghcr.io/berntpopp/gnomad-link@sha256:<manifest-digest>
```

The fleet will not publish or consume `latest`, moving major tags, or moving
minor tags. GHCR tags are treated as mutable even when operational controls make
mutation difficult. A tag is never accepted as a security identity.

### Code and data have separate release lines

An application version changes when code or dependencies change. A data version
changes when the upstream dataset, transformation, schema, or license record
changes. Coupling them causes needless multi-gigabyte image rebuilds and makes
rollback ambiguous.

Every application image is code-only. Data-aware repositories use one of three
declared modes:

- `none`: no persistent local dataset is required;
- `external-reference`: an immutable reference artifact is mounted or
  materialized into a volume;
- `runtime-cache`: only derived cache/state is written to an explicit volume.

A repository may use both `external-reference` and `runtime-cache`; the
configuration records both properties without inventing a fourth mode.

### Publication requires an explicit source release

Merging a version bump is preparation, not publication. A protected exact SemVer
tag, `vX.Y.Z`, authorizes publication. The workflow proves that the tag matches
installed project metadata and points to reviewed `main` history.

Version bumps on `main` trigger a cheap release-readiness check that reports
whether changelog, lockfile, and version metadata are coherent. They may prepare
a reviewed release PR or issue. They do not create a public package.

### Build once per trust transition

A pull request and a release are different trust transitions and therefore each
performs its own build. Within either transition the image is built exactly once.
The job must not scan one build and push another.

For the initial AMD64-only release, BuildKit loads the production image into the
runner's Docker image store. All smoke, content, hardening, and vulnerability
gates operate on that local image. The job then pushes that image and records the
digest returned by the registry. It does not invoke a second Docker build.

### Verification is a deployment action

Generating an attestation is not a control unless consumers verify it. Every
production deployment verifies the expected source repository, reusable
workflow, source revision, image digest, and release record before startup.

## Approaches considered

### A. Protected tag plus central reusable workflows — selected

Advantages:

- explicit human publication boundary;
- stable source identity;
- compatible with immutable GitHub Releases;
- least-privilege separation between PR and release jobs;
- one centrally versioned implementation;
- clear idempotency key `(repository, vX.Y.Z)`.

Trade-offs:

- maintainers must create a protected tag after merging release preparation;
- the central workflow SHA must be rolled through callers when the standard
  changes;
- repository rulesets and immutable-release settings require a one-time GitHub
  configuration audit.

### B. Reviewed release PR as publication trigger — rejected as the authority

A release PR is useful for preparing version, lockfile, and changelog changes.
Its merge is not a sufficiently explicit external-publication signal. Merge
commits also complicate exact source identity. Release PR automation remains
optional preparation; the protected tag remains authoritative.

### C. Automatic publication on a version change to `main` — rejected

This makes an ordinary merge an irreversible public release. It would also
publish the fourteen versions already ahead of tags. The operational convenience
does not justify the ambiguity or blast radius.

## Fleet configuration contract

Each repository contains `container-release.json`. The router contains the JSON
Schema and the validator. Unknown keys and unknown enum values fail closed.

Values derived by the workflow are intentionally absent from the configuration:

- GitHub repository and GHCR image name;
- application version from installed metadata;
- source revision from the release ref;
- standard workflow repository, ref, and SHA;
- build timestamp;
- registry digest;
- standard OCI labels.

The configuration includes only repository-specific facts:

```json
{
  "schema_version": 1,
  "dockerfile": "docker/Dockerfile",
  "target": "production",
  "service": {
    "compose_files": ["docker/docker-compose.yml"],
    "name": "gnomad-link",
    "container_port": 8000,
    "health_path": "/health",
    "mcp_path": "/mcp",
    "startup_timeout_seconds": 90
  },
  "data": {
    "mode": "none",
    "image_allowlist": []
  },
  "smoke": {
    "profile": "compose"
  }
}
```

Defaults are:

- Dockerfile `docker/Dockerfile`;
- target `production`;
- platform `linux/amd64`;
- container port `8000`;
- health path `/health`;
- MCP path `/mcp`;
- startup timeout 90 seconds;
- smoke profile `compose`;
- data mode `none`;
- no data-file allowlist.

`spliceailookup-link` overrides its port. Services with PostgreSQL or initial
data preparation override timeout and smoke fixture declarations. Configuration
does not accept arbitrary inline shell. A repository that needs preparation uses
the fixed, reviewable path `docker/ci-prepare-smoke.sh`; the workflow runs it
without secrets or publication permissions and validates its generated fixture
manifest.

## Central automation

### Separate reusable workflows

The router exposes two reusable workflows:

1. `_container-ci.yml` accepts no write permissions and is callable from pull
   request or `main` validation workflows.
2. `_container-release.yml` is callable only from exact `v*` tag workflows and
   receives the minimum publishing permissions from the caller.

The split is deliberate. A boolean `publish` input in one workflow would make it
too easy to grant release permissions to untrusted PR code.

Each called workflow:

1. checks out the caller repository into the normal workspace;
2. checks out `job.workflow_repository` at `job.workflow_sha` into an isolated
   standard-tools directory;
3. runs validation tools from that exact central workflow revision.

GitHub exposes `job.workflow_repository`, `job.workflow_ref`, and
`job.workflow_sha` for this purpose. The source and called-workflow identities
also appear in the OIDC token used for attestations.

### Thin caller workflows

Each repository has:

- `container-ci.yml`, triggered only when application, lockfile, Docker,
  Compose, release configuration, or workflow files change;
- `container-release.yml`, triggered on exact `v*` tag pushes and supporting a
  guarded manual backfill for an already-existing tag.

Both callers reference the router reusable workflow by a full 40-character
commit SHA. Action pins inside the central workflow are also full SHAs.

Existing Python quality, PyPI/TestPyPI, documentation, data-refresh, and security
workflows remain where they have a distinct purpose. Duplicate Docker build,
container conformance, and image-security jobs are removed or reduced to
non-building work.

### Permissions

PR/container CI:

```yaml
permissions:
  contents: read
```

Release publication:

```yaml
permissions:
  contents: write
  packages: write
  attestations: write
  id-token: write
```

`artifact-metadata: write` is added only if the linked-artifact feature is used
and supported. Permissions are job-scoped. No PAT is distributed to the fleet.

### Concurrency

- PR jobs use a ref-scoped group and `cancel-in-progress: true`.
- Release jobs use a repository/tag-scoped group and
  `cancel-in-progress: false`.
- Data-publication jobs use an artifact/tag-scoped group and never overwrite a
  completed release.

## Pull-request container CI

The PR workflow performs the following against one local production image:

1. validate `container-release.json` and the Docker/Compose source contract;
2. validate the single-source application version and frozen lockfile;
3. build the configured production target for `linux/amd64`;
4. run the image with the configured Compose stack using `--no-build`;
5. wait for `/health` and MCP readiness;
6. run MCP initialize and list-tools conformance;
7. assert runtime identity and hardening;
8. inspect the merged root filesystem for forbidden content;
9. run the table-format Trivy vulnerability gate;
10. optionally emit non-gating SARIF;
11. generate a non-attested SBOM as short-retention CI evidence;
12. tear down the stack in an `always()` step.

The Compose smoke override points the application service at the already-built
local tag. `docker compose up --no-build` guarantees Compose cannot rebuild it.
Dependent services such as PostgreSQL may still be started by Compose.

The MCP test is against the container, not an in-process checkout. It verifies
Streamable HTTP initialization, advertised server name/version, and tool listing.

## Image-content policy

`.dockerignore` is necessary but not sufficient. The release tooling exports the
container's merged root filesystem and evaluates the actual result.

The default deny policy includes:

- `.env`, `.env.*`, private keys, certificates with private material, and common
  credential-file names;
- `.git`, CI credentials, editor state, caches, and test reports;
- SQLite/database files outside an explicit allowlist;
- compressed database/corpus archives;
- VCF/BCF, parquet, bulk CSV/TSV, ontology dumps, and full-text corpora;
- repository `data/`, `datasets/`, `corpus/`, and runtime-state trees;
- unexpected single files over the standard threshold.

Small code resources such as SQL schemas, JSON schemas, controlled vocabularies,
and package metadata are allowed only by exact path. Globally allowing a `data`
directory is prohibited because it would have hidden the ClinGen violation.

The validator also checks `.dockerignore` for `.git`, environment files, root
data/output directories where applicable, and local caches. It reports build
context size so a multi-gigabyte ignored-data regression is visible before the
Docker build.

## Runtime hardening smoke test

The application container is started with the same invariants required by the
fleet hardening standard:

- non-root UID;
- read-only root filesystem;
- `cap_drop: ALL`;
- `no-new-privileges`;
- bounded PID count;
- explicit tmpfs with `noexec,nosuid`;
- only declared writable volumes;
- no Docker socket;
- no host network;
- no privileged mode.

The workflow renders production and proxy Compose configurations and fails if a
backend service publishes a production host port, retains `build:`, enables a
privilege escape, or omits resource/logging controls.

## Vulnerability, SBOM, and provenance policy

### Vulnerability gate

The authoritative gate is Trivy table output with:

```yaml
format: table
severity: CRITICAL,HIGH
ignore-unfixed: true
exit-code: "1"
```

SARIF, if enabled, is a separate `exit-code: "0"` reporting step. Evidence is
uploaded with `if: always()`.

### SBOM

CI may retain a short-lived SPDX JSON SBOM. A release attaches an SPDX or
CycloneDX SBOM attestation to the pushed image digest and includes a copy in the
draft immutable GitHub Release.

### Provenance

The release job invokes GitHub's `actions/attest` for the fully qualified image
name and pushed digest. Verification requires the expected source owner/repo and
the SHA-pinned router reusable workflow. The release manifest records both caller
and called workflow identity.

Build arguments and labels must never contain secrets. Maximum-level BuildKit
provenance can expose build arguments; all future authenticated build inputs must
use BuildKit secret mounts.

### Terminology

The standard calls images `traceable` and `rebuildable from pinned inputs`, not
`byte-reproducible`. Digest-pinned bases and `uv.lock` do not freeze Debian
repositories, wheel availability, timestamps, or compression behavior.

## OCI metadata

Every application image includes:

- `org.opencontainers.image.title`;
- `org.opencontainers.image.description`;
- `org.opencontainers.image.source`;
- `org.opencontainers.image.url`;
- `org.opencontainers.image.documentation`;
- `org.opencontainers.image.version`;
- `org.opencontainers.image.revision` with the full 40-character commit;
- `org.opencontainers.image.created` derived consistently from the build
  record;
- `org.opencontainers.image.licenses` for the application code;
- `org.opencontainers.image.vendor=GeneFoundry`;
- `org.genefoundry.research-use-only=true`;
- `org.genefoundry.data-policy=code-only`.

The app can report version and source revision through health/build metadata.
It cannot authoritatively discover its registry digest from inside the
container; deployment tooling records that digest.

## Release validation and state machine

The release key is `(repository, vX.Y.Z)`.

### VALIDATE

- tag matches strict stable SemVer syntax;
- tag version equals installed metadata and `pyproject.toml`;
- tagged commit is reachable from protected `main`;
- remote tag still equals `GITHUB_SHA`;
- lockfile is frozen and contains the project version;
- changelog contains the version;
- repository configuration and Docker/Compose contracts pass;
- the version is greater than the previous stable application release;
- no existing exact-version image alias points to a different source revision.

This phase is read-only and repeatable. It probes the newly built container later;
it does not fail because an older deployed backend is temporarily unreachable.

### BUILD

- build the production target once;
- pass the full source revision and deterministic source-derived metadata;
- add required OCI labels;
- load the AMD64 image into Docker;
- record local image ID and configuration digest.

### GATE

- run health/MCP conformance on the local image;
- run content and hardening assertions;
- run the Trivy gate;
- generate the SBOM;
- capture pre-push evidence.

### PUSH

- authenticate to GHCR using `GITHUB_TOKEN`;
- push exact version and full-source-SHA aliases from the same local image;
- record the registry digest;
- verify both aliases resolve to that digest;
- fail on a pre-existing mismatched alias;
- verify anonymous pull succeeds so package visibility is public.

The workflow never deletes or overwrites a package version to make a rerun pass.

### ATTEST

- create GitHub provenance for the image name/digest;
- attach the SBOM attestation;
- verify the attestation using the expected repository and reusable workflow;
- retain verification output in the release record.

### CAPTURE

- pull by the published digest;
- start that digest with the configured smoke fixture/data binding;
- capture raw MCP definitions;
- compute the canonical definition SHA-256;
- verify reported application version/revision against release identity.

Tool definitions should not depend on dataset contents. The capture nevertheless
records its fixture/data context so a hidden dependency can be detected.

### RELEASE

- create or reuse a draft GitHub Release for the existing tag;
- attach SBOM, release manifest, definition capture, and verification evidence;
- verify the remote tag still points to `GITHUB_SHA`;
- publish the draft once all prior phases pass.

Repository release immutability locks the release assets and associated tag after
publication. Draft-first publication is required because immutable release assets
cannot be replaced later.

### Recovery and idempotency

- Failure before PUSH leaves no public package.
- Failure after PUSH leaves an unaccepted digest with no completed immutable
  release record. Production cannot select it.
- A rerun that finds a version alias with expected source labels resumes from
  registry verification and repeats gates against the existing digest.
- A rerun that finds a mismatched digest fails as a collision/incident.
- A completed immutable release short-circuits successfully only after all
  release assets and attestations verify.

This recovery behavior avoids relying on a second non-hermetic build reproducing
the same digest after a late workflow failure.

## Application release manifest

Every immutable source release contains a machine-readable manifest with:

```json
{
  "schema_version": 1,
  "repository": "berntpopp/gnomad-link",
  "version": "8.0.3",
  "source": {
    "tag": "v8.0.3",
    "revision": "<40-hex>"
  },
  "image": {
    "name": "ghcr.io/berntpopp/gnomad-link",
    "digest": "sha256:<digest>",
    "platforms": [
      {
        "platform": "linux/amd64",
        "digest": "sha256:<digest>"
      }
    ]
  },
  "workflow": {
    "caller": "berntpopp/gnomad-link/.github/workflows/container-release.yml",
    "standard": "berntpopp/genefoundry-router/.github/workflows/_container-release.yml",
    "standard_revision": "<40-hex>"
  },
  "mcp": {
    "definitions_sha256": "<sha256>",
    "capture_context_sha256": "<sha256>"
  },
  "data_requirements": {
    "mode": "none",
    "schema_compatibility": []
  }
}
```

For a single-platform release, `image.digest` is the registry manifest digest.
The schema remains ready for a future multi-platform index without pretending
that an index already exists.

## Reference-data release design

Data publishing is independent of application publishing.

Each data-bearing repository records:

- upstream source and stable identifier;
- retrieval timestamp;
- upstream checksum/ETag where meaningful;
- transformation tool repository and full commit;
- transformation/schema version;
- record counts and validation results;
- compressed and expanded SHA-256;
- license and redistribution decision;
- maximum compressed/expanded sizes;
- compatible application/schema range;
- research-use disclaimer where applicable.

Publication uses:

1. an exact `data-<stable-version>` or dataset-specific immutable tag;
2. a draft GitHub Release;
3. upload without `--clobber`;
4. asset digest verification;
5. publish once;
6. GitHub immutable-release verification.

A checksum sidecar stored beside a bundle detects transport corruption but is not
the trust root. Production configuration contains the reviewed expected digest
and exact release identity. `latest` is allowed only for local development.

Reference data is mounted read-only after materialization. Mutable caches live in
separate writable paths. Downloaders retain the fleet's HTTPS allowlist, redirect,
size, time, archive-expansion, and atomic-replacement controls.

### ClinGen migration

Before `clingen-link` publishes a public application image:

1. remove its committed bundle from the Docker build context and package wheel;
2. add an external snapshot-path/bundle configuration;
3. update the weekly data workflow to publish a draft immutable data release
   rather than committing refreshed bytes for image inclusion;
4. require exact bundle identity and digest in production;
5. mount/decompress the verified bundle through explicit tmpfs/volume paths;
6. retain a tiny synthetic fixture for CI only;
7. add regression tests proving the production image contains no snapshot.

### Existing bundle producers

ClinVar, MaveDB, HPO, Orphanet, GeneReviews, and any other producer found by the
implementation audit adopt the same immutable publisher contract. MaveDB removes
`--clobber`. Production defaults that resolve `latest` are replaced by required
exact configuration.

## Production Compose design

Development Compose may build locally. Production overlays must clear that
declaration:

```yaml
services:
  gnomad-link:
    build: !reset null
    image: "${GNOMAD_LINK_IMAGE:?set an attested image@sha256 digest}"
    pull_policy: always
```

The deployment verifier requires the environment value to match:

```text
^ghcr\.io/berntpopp/<expected-repo>@sha256:[0-9a-f]{64}$
```

Docker Compose's `!reset null` is load-bearing. Adding `image:` without removing
`build:` permits a source build fallback when pull behavior changes or an image is
missing.

The proxy overlay continues to use `ports: !reset []` and expose-only networking.
Rendered production configuration must have:

- no effective application `build:`;
- digest-addressed `image:`;
- no published backend port;
- read-only rootfs and explicit writable paths;
- no extra capabilities or privilege escalation;
- resource/PID/log limits;
- per-stack project name;
- healthcheck and restart policy.

## Deployment verification

The deployment command fails before `compose up` unless all checks pass:

1. parse the reviewed deployment record;
2. verify the immutable application release;
3. verify GitHub image provenance for the expected repository;
4. require the expected router reusable workflow identity/revision;
5. inspect image labels by digest;
6. verify SBOM attestation exists;
7. verify exact data release/digest when data is required;
8. render Compose and enforce production invariants;
9. pull by digest;
10. start services;
11. probe health and MCP initialization;
12. compare application version/revision and definition digest;
13. record the deployed tuple.

The deployment host never trusts an image-reported digest. It records the digest
it verified and passed to Docker.

## Rollback and data compatibility

The rollback unit is:

```text
application image digest
+ application version/revision
+ data release and digest
+ schema version
+ MCP definition digest
```

Before a data-schema migration, the deployment tooling snapshots the named volume
or database using the repository's documented method. Startup fails closed when
an image is incompatible with the mounted data schema. Destructive automatic
migrations are not added as part of this release standard.

A rollback test proves that the prior deployment tuple can still start against
its prior data snapshot. Merely pulling an older image is not evidence of a safe
rollback.

## Router fleet reconciliation

Leaf repositories do not receive a fleet-wide PAT or permission to write to the
router repository.

The router owns a reconciliation Action that:

1. enumerates registered repositories from `servers.yaml`;
2. discovers each repository's selected immutable application release manifest;
3. verifies source release and image attestations;
4. pulls the published image digest;
5. captures MCP definitions using the declared smoke profile;
6. combines the chosen production data binding;
7. opens or refreshes one reviewed router PR.

The PR updates the candidate inventory atomically. A backend entry contains at
least:

- endpoint;
- application version and full source revision;
- image repository and digest;
- platform/digest mapping;
- source release tag;
- reusable workflow identity/revision;
- attestation verification result/time;
- definition SHA-256;
- data release/digest/schema, or explicit `none`;
- compatibility result.

The existing candidate snapshot and packaged baseline remain all-or-nothing.
They cannot silently retain a stale backend or omit new provenance fields.

## Scheduled vulnerability response

The router reads deployed digests from the reviewed inventory and scans only
those digests on a weekly off-peak schedule.

- It does not rebuild images.
- It does not scan every historical tag.
- It bounds matrix parallelism.
- It opens or refreshes one actionable tracking issue.
- It distinguishes new fixable HIGH/CRITICAL findings from unavailable registry
  or vulnerability-database infrastructure.
- It retains enough evidence to identify image, package, advisory, fixed version,
  and owning repository.

The response is a dependency/base update and new application release. A
previously published digest is never mutated.

## Compute-efficiency policy

1. One container build per PR event and one per release event.
2. Consolidate build, conformance, hardening, scan, and SBOM work into the same
   container job.
3. Use path filters so documentation-only changes do not build images.
4. Cancel superseded PR runs.
5. Never cancel release or data-publication runs.
6. Use BuildKit GitHub Actions cache scoped by repository, platform, Dockerfile
   target, and workflow generation.
7. Default cache export to bounded `mode=min`; cache failures do not fail a
   correct build.
8. Do not publish or sign PR images.
9. Do not download production-scale data when a deterministic small fixture can
   prove startup and MCP definitions.
10. Publish AMD64 only until another platform is required and fully tested.
11. Scan deployed digests weekly rather than rebuilding 22 images on a schedule.
12. Use short retention for transient CI SBOM/log artifacts; immutable release
    evidence follows the release retention policy.

## Multi-platform policy

`linux/amd64` is the v1 fleet platform because it matches the current deployment
target and avoids QEMU cost.

ARM64 is enabled per repository only after:

- native/compiled dependencies resolve for ARM64;
- the Dockerfile builds on ARM64;
- image-content and vulnerability gates run on the ARM64 manifest;
- the ARM64 container reaches health and passes MCP initialization/list-tools;
- data tooling works on ARM64;
- the release manifest records an OCI index digest plus per-platform digests.

Adding QEMU and producing an index without platform-specific runtime tests is not
compliance.

## GitHub repository and registry controls

The implementation audits or configures:

- protected `v*` tag rulesets;
- immutable GitHub Releases for future releases;
- default `GITHUB_TOKEN` read-only permissions;
- full-SHA action pinning;
- public GHCR package visibility and repository linkage;
- anonymous pull verification;
- preservation of all released/deployed digests and their attestations;
- no automated package deletion that can remove a deployed or rollback digest;
- branch protection required by tag ancestry validation.

Package visibility is not assumed from source visibility. The first publication
fails until anonymous pull works.

## Testing strategy

### Router unit tests

- JSON Schema accepts every fleet configuration and rejects unknown/unsafe
  fields.
- Version/tag validation covers stable SemVer, prerelease/local versions,
  mismatch, downgrade, missing changelog, and unreachable tags.
- Content-policy tests use synthetic exported root filesystems and verify default
  denies plus exact allowlists.
- OCI label validation requires full source revision and code-only policy.
- Release-manifest parsing rejects missing/malformed digests and workflow
  identities.
- Idempotency tests cover no prior image, matching partial publication,
  mismatched collision, completed release, and missing attestation.
- Compose validation rejects inherited build, tags, published ports, and missing
  hardening.
- Candidate inventory validation requires image/data/definition provenance for
  every enabled backend.

### Workflow/static tests

- YAML parsing and `actionlint` for all central and caller workflows;
- full-SHA action and reusable-workflow pin checks;
- permission tests proving PR jobs are read-only;
- trigger tests proving release jobs accept only exact tags or guarded backfill;
- concurrency tests proving release runs are never cancelled;
- test that SARIF is non-gating and table output is gating;
- test that no workflow besides the release caller can publish the application
  package.

### Image integration tests

- build every production target;
- run the exact local image through health/MCP conformance;
- assert required runtime hardening;
- inspect merged rootfs for forbidden content;
- render base/prod/proxy Compose combinations;
- for pilots, push to a disposable test package or local registry and verify
  digest preservation and manifest generation;
- pull the published digest and recapture definitions during real release.

### Data tests

- bundle publication refuses an existing immutable tag/asset;
- exact expected digest is mandatory in production;
- corrupted, oversized, redirected, or expansion-bomb artifacts fail closed;
- materialization is atomic;
- data schema compatibility is checked before service startup;
- application image contains no bundle or runtime database;
- rollback fixture demonstrates prior image/prior data compatibility.

### Required repository verification

The router runs `make ci-local` before handoff. Every modified fleet repository
runs its own `make ci-local` plus the standardized container configuration,
build, content, Compose, and smoke checks. Failing external vulnerability or
registry infrastructure is reported distinctly from a policy violation.

## Rollout sequence

### Phase 0: reconcile release state

Inventory all package versions, source tags, GitHub Releases, data releases, and
current deployed revisions. For each of the fourteen package/tag mismatches,
make an explicit release-or-defer decision. Do not manufacture tags silently.

### Phase 1: router standard

Implement schemas, validation tools, reusable CI/release workflows, release
manifest, deployment verifier, documentation, and tests in the router.

### Phase 2: remove data from images

Migrate ClinGen before its first public image. Audit all other Docker contexts,
wheels, and runtime images. Harden existing bundle publishers and production
pins.

### Phase 3: representative pilots

Adopt and exercise the standard on:

- the router;
- one stateless API backend;
- one external-reference-data backend;
- one backend with a dependent database service.

Resolve standard defects centrally before fleet fan-out.

### Phase 4: fleet adoption

Add typed configuration and thin callers to the remaining repositories. Remove
duplicate image builds. Convert production Compose to digest-only images.

### Phase 5: GitHub controls and backfill

Audit rulesets, immutable releases, package visibility, and retention. Use the
guarded manual backfill only for reviewed existing tags. Create new release tags
only for versions explicitly selected in Phase 0.

### Phase 6: fleet reconciliation and deployment

Generate the reviewed candidate PR from verified release manifests, capture
definitions from published digests, deploy by digest, and verify the full tuple.

### Phase 7: scheduled operations

Enable weekly deployed-digest scanning and the response runbook after production
inventory is authoritative.

## Failure handling

- Configuration/schema failure: no build.
- Build or local gate failure: no push.
- Registry push failure: retry without rebuilding while the local job exists.
- Late workflow failure: existing matching digest may be re-pulled and re-gated;
  mismatched digest fails as an incident.
- Attestation failure: digest is not accepted into an immutable release or fleet
  inventory.
- Definition mismatch: release/candidate fails; runtime drift baseline is not
  updated.
- Data digest/schema mismatch: deployment fails before server startup.
- Anonymous-pull failure: package visibility/setup failure, not a deploy retry.
- Vulnerability database outage: availability warning and retry, not a false
  claim of a clean scan.
- Fixable HIGH/CRITICAL finding: policy failure and no release.
- Unfixable finding: retained in evidence and scheduled monitoring, not silently
  ignored.

## Security and medical-research boundary

Publicly distributing a dataset in a container or release asset is a
redistribution act. Each data producer requires a documented source license and
redistribution decision. Public availability of an upstream endpoint is not
permission to redistribute a derived bulk snapshot.

No patient-derived input, query payload, OAuth token/state, request log, or cache
may enter a public artifact. The content gate is defense-in-depth; runtime logging
and volume policies remain mandatory.

All images and manifests retain the research-use-only, not-clinical-decision-
support boundary. Container signing and provenance establish origin and integrity,
not clinical validity, regulatory approval, or correctness of biomedical data.

## Definition of done

The initiative is complete only when:

- [ ] Router and all 21 registered backends contain valid typed release
      configuration and SHA-pinned callers.
- [ ] PR container CI builds each application image once and performs smoke,
      hardening, content, Trivy, and SBOM checks on that exact image.
- [ ] Exact protected source releases publish public AMD64 GHCR images with
      immutable source releases, SBOM, verified provenance, and definition
      capture.
- [ ] No published application image contains reference data, secrets, runtime
      state, or patient-derived material.
- [ ] ClinGen and every other data producer use immutable external artifacts and
      reviewed production digests.
- [ ] MaveDB and other data publishers cannot overwrite published assets.
- [ ] Production Compose for router and every backend has no effective `build:`
      and requires a digest-addressed image.
- [ ] Deployment verifies image/data provenance and records the full rollback
      tuple.
- [ ] The router candidate inventory atomically binds image, data, source, and
      definition identity for every enabled backend.
- [ ] Duplicate image builds have been removed and scheduled scanning targets
      only deployed digests.
- [ ] GitHub tag, release immutability, package visibility, and retention controls
      have been audited for every repository.
- [ ] All modified repositories pass `make ci-local` and standardized container
      checks.
- [ ] Claude Code Opus 4.8 at `xhigh` has adversarially reviewed the specification,
      implementation plan, and resulting pull requests; all blocking findings are
      resolved or explicitly rejected with evidence.

## Authoritative references

- GitHub, Publishing Docker images:
  <https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images>
- GitHub, Artifact attestations:
  <https://docs.github.com/en/actions/concepts/security/artifact-attestations>
- GitHub, Using artifact attestations:
  <https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations>
- GitHub, Secure use of Actions:
  <https://docs.github.com/en/actions/reference/security/secure-use>
- GitHub, Reusable workflows and contexts:
  <https://docs.github.com/en/actions/reference/workflows-and-actions/reusing-workflow-configurations>
  and
  <https://docs.github.com/en/actions/reference/workflows-and-actions/contexts>
- GitHub, Immutable releases:
  <https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases>
- GitHub, GHCR access and visibility:
  <https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry>
- Docker, Build exporters and attestations:
  <https://docs.docker.com/build/exporters/> and
  <https://docs.docker.com/build/ci/github-actions/attestations/>
- Docker, GitHub Actions cache backend:
  <https://docs.docker.com/build/cache/backends/gha/>
- Docker, Compose merge and build/image behavior:
  <https://docs.docker.com/reference/compose-file/merge/> and
  <https://docs.docker.com/reference/compose-file/build/>
- NIST SP 800-190, Application Container Security Guide:
  <https://csrc.nist.gov/pubs/sp/800/190/final>
- GeneFoundry Container & Deployment Hardening Standard v1:
  `docs/CONTAINER-HARDENING-STANDARD-v1.md`
- GeneFoundry Versioning Standard v1:
  `docs/VERSIONING-STANDARD-v1.md`

