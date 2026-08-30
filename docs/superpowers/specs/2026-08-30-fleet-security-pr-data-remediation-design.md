# Fleet Security, Pull-Request, and Data Remediation Design

> Historical record — this document records the approved design as of 2026-08-30. It supersedes
> the operational inventory in the July 2026 fleet-remediation documents. Current truth is
> established from GitHub, upstream source metadata, immutable releases, deployed health
> responses, and repository tests.

**Status:** Architecture approved in chat on 2026-08-30; written-spec review required before
implementation planning.

**Audit timestamp:** 2026-08-30, Europe/Berlin.

## 1. Goal

Return the GeneFoundry router and all 21 configured `*-link` backends to a fully reviewed,
mergeable, releaseable, and data-current state. The program must:

- remediate all known fixable container vulnerabilities without waivers;
- resolve every open fleet Dependabot pull request by merging a verified equivalent or closing it
  only after a verified replacement has merged;
- repair broken CI, control-audit, data-build, and data-publish workflows;
- publish new immutable data releases where current upstream evidence proves production is stale;
- update application provenance where live data is mislabeled;
- preserve the router trust boundary, Streamable HTTP transport, research-use boundary, and
  container hardening standard;
- publish and, where the supported deployment mechanism is available, deploy changed application
  releases before refreshing the router baseline and running live fleet verification.

The program optimizes for the real end state. A smaller outcome such as making source CI green
while leaving container gates, stale data, superseded PRs, or disabled production probes unresolved
does not satisfy the goal.

## 2. Authoritative repository scope

The primary fleet is derived from `servers.yaml`, not from local directory names:

- trusted builder and gateway: `berntpopp/genefoundry-router`;
- backends: `autopvs1-link`, `clingen-link`, `clinvar-link`, `gencc-link`,
  `genereviews-link`, `gnomad-link`, `gtex-link`, `hgnc-link`, `hpo-link`, `litvar-link`,
  `mavedb-link`, `metadome-link`, `mgi-link`, `mondo-link`, `orphanet-link`, `panelapp-link`,
  `pubtator-link`, `spliceailookup-link`, `stringdb-link`, `uniprot-link`, and `vep-link`.

Supporting GeneFoundry repositories are included where they affect public release or security
truth: `genefoundry`, `genefoundry-bench`, and `genefoundry-mcp-security-profile`.

Before modifying any repository, the executor must read its current `AGENTS.md` and any linked
repository-specific instructions in full. A local clone is accepted only when its `origin` matches
the scoped GitHub repository. Existing user changes are never overwritten.

## 3. Reconciled starting evidence

At the audit timestamp:

- the router and 21 backends had 175 open Dependabot PRs;
- every public repository returned zero open Dependabot, code-scanning, and secret-scanning alerts;
- those empty alert APIs did not cover the fixable OS vulnerabilities detected by Trivy;
- all 22 application Dockerfiles pinned an obsolete Python slim image index;
- the obsolete Python 3.14 image contained `util-linux 2.41-5`, while the scanner requires
  `2.41.5-0+deb13u1` for CVE-2026-53612, CVE-2026-53613, CVE-2026-53614, and
  CVE-2026-53615;
- the current verified Python 3.14 multi-platform index is
  `sha256:cae66f2ef0ec51a9891263eeee7f987dacf0a9879e8aa9353d5606e0530619a5` and
  contains the fixed `util-linux` packages;
- GeneReviews uses Python 3.12; the current verified multi-platform index is
  `sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217`;
- split CodeQL `init` and `analyze` Dependabot PRs produced mixed 4.37.6/4.37.7 jobs and failed
  with an explicit action-version configuration mismatch;
- the exact UniProt live test that received one HTTP 503 passed locally, and the same commit passed
  the preceding 19 daily workflow runs, proving an upstream transient rather than a code defect;
- deployed endpoints were healthy and 21/21 backends passed direct conformance at audit time;
- the router's drift and production-fleet schedules were skipped because their opt-in variables
  were unset;
- the trusted-builder audit lacked its dedicated token and its strict ruleset parser rejected
  GitHub's newly returned `require_extra_approval_for_unattributed_changes` field.

This evidence is a starting snapshot. Executors must refresh branch heads, PR state, action pins,
image digests, upstream metadata, and release state immediately before each mutation.

## 4. Architectural decomposition

The program is split into three independently testable workstreams with explicit sequencing:

```text
Workstream A: trusted builder + shared security root
    router base image / control parser / reusable workflow evidence
                         |
                         v
Workstream B: one consolidated security/dependency PR per backend
    base digest + paired actions + reviewed dependency union + restart fixes
                         |
                         v
Workstream C: data pipelines and immutable data releases
    build/verify/publish/pin/deploy only when source evidence requires it
                         |
                         v
Fleet closure
    application releases -> deployment evidence -> router baseline -> 21/21 live verification
```

Workstreams B and C run concurrently only across disjoint repositories. Within one repository,
one writer owns one worktree and branch until its PR is merged or abandoned. The router workstream
lands first whenever a backend depends on a new trusted reusable-workflow release.

Separate implementation plans will cover:

1. router, GitHub controls, monitoring, and supporting repositories;
2. fleet container/dependency/PR consolidation and restart-policy remediation;
3. data-pipeline repair, corpus/bundle generation, immutable release promotion, and data pinning;
4. fleet releases, deployment verification, baseline refresh, and closure audit.

## 5. Workstream A: trusted builder and control plane

### 5.1 Router container and dependencies

The router receives the current reviewed Python 3.14 image index in both Dockerfile stages. The
change stays digest-pinned; no mutable base tag is allowed as the authority. Build the exact
production target, scan it with the versioned `fixable-high-critical-v1` policy, generate its SBOM,
and prove smoke/runtime hardening before publication.

The router's open dependency and action PRs are consolidated by reproducing the union of reviewed
changes on one branch. FastMCP changes require import verification against the installed package,
router unit/integration contracts, the two-backend proxy smoke, and auth regression tests. Action
constants used by release tests must move in the same commit as their workflow pins.

### 5.2 Trusted-builder control audit

The live router ruleset is active, targets only `refs/heads/main`, has no bypass actors, requires a
PR, blocks deletion and non-fast-forward updates, and intentionally uses zero approvals while the
repository has only one independent maintainer. GitHub now returns
`require_extra_approval_for_unattributed_changes: true` in the pull-request rule.

The control parser will recognize that field as an exact, typed, security-positive requirement.
TDD must first reproduce rejection of the real response shape, then accept the exact `true` value
while continuing to reject unknown fields, wrong types, `false`, extra bypass actors, relaxed
branch targeting, or removal of deletion/non-fast-forward controls. The sealed ledger is regenerated
only from successful live API probes.

The scheduled audit must use a dedicated GitHub App installation token or fine-grained token with
read-only metadata/administration access to the scoped repositories. The existing broad local
classic token must not be copied into Actions. Creating that credential is an explicit human
prerequisite; all code, environment-protection, and evidence work that does not require its value
continues independently.

### 5.3 Production monitoring

Set the canonical router repository variables `DRIFT_ENABLED=true` and
`FLEET_PROBE_ENABLED=true`. Run both workflows manually after enabling them and require successful
definition-drift and 21-backend transport/behaviour evidence. Heartbeat secrets are configured only
when the owner supplies dedicated monitor URLs; absence of a heartbeat URL must not be represented
as a configured dead-man switch.

### 5.4 Supporting repositories

- `genefoundry`: merge the reviewed website updates, protect `main` and semantic release tags with
  a solo-maintainer-safe PR rule, enable immutable releases, and validate the corrected explicit
  release target with a fresh container release.
- `genefoundry-bench`: add a minimal reproducible CI/security baseline appropriate to the private
  benchmark repository and enable Dependabot where GitHub permits it.
- `genefoundry-mcp-security-profile`: advance the router submodule from v0.6.7 to the final audited
  router revision, rerun the assessment, and record the new evidence date and release identities.

No branch rule may require an approval that the sole maintainer cannot supply. A one-independent-
approval policy remains a documented future control whose operational precondition is a second
independently controlled maintainer account.

## 6. Workstream B: fleet security and PR consolidation

### 6.1 Base-image remediation

Every backend updates both builder/prepared Python stages to the current verified digest for its
declared Python line. Scratch production assembly, non-root ownership, read-only runtime,
capability dropping, `no-new-privileges`, resource limits, expose-only networking, and the existing
data init/app separation remain unchanged.

The updated digest is not considered fixed merely because its Debian package version looks newer.
Each repository must pass its exact shared container CI policy against the produced image. Zero
fixable HIGH/CRITICAL findings is the acceptance condition; no ignore file, severity downgrade,
baseline waiver, or scanner bypass is introduced.

### 6.2 Dependency and action updates

For each repository, build the consolidated branch from fresh `origin/main` and reproduce the
reviewed union of its open Dependabot diffs:

- update CodeQL `init` and `analyze` pins atomically to one reviewed action revision;
- update all occurrences of `astral-sh/setup-uv` together while preserving an explicit uv version;
- update reusable router CI/release references together when they target the same control-plane
  release;
- update direct and locked Python dependencies through `uv`, never by hand-editing `uv.lock`;
- preserve declared version floors/ceilings and solver markers unless an explicit compatibility
  test justifies a constraint change;
- inspect FastMCP, Pydantic, Uvicorn, Orjson, NumPy, Torch, and other runtime changes at their public
  import/schema/ABI boundaries before accepting them.

Each logical concern is an atomic commit even when delivered in one PR: base image, paired CI
actions, Python dependency union, and repository-specific correctness fixes remain separately
reviewable and revertible.

No bot PR is closed merely because a replacement is planned. After the consolidated PR is pushed,
green, reviewed, and merged, superseded PRs are closed with a concise link to the replacement and
its verification evidence. A still-relevant bot PR not represented by the replacement remains open
and is processed normally.

### 6.3 Restart-policy remediation

Scan every production/NPM Compose overlay against the router reference implementation. Confirmed
restart-after-host-reboot issues in the router, AutoPVS1, ClinGen, GeneReviews, gnomAD, LitVar, and
PubTator are fixed using the fleet's canonical restart semantics. Init/data-build services remain
one-shot and must not be converted into endlessly restarting daemons. Tests render the effective
Compose model and distinguish serving services from one-shot auxiliaries.

## 7. Workstream C: data correctness and releases

Data publication is source-driven. A workflow rerun is not equivalent to a new release, and a new
release is not created when the source identity is unchanged. Every published artifact uses an
immutable tag, cryptographic digest, manifest/provenance metadata, bounded processing, and the
repository's license/redistribution rules.

| Repository | Required outcome |
| --- | --- |
| ClinVar | Replace the workflow's RFC-1123 string slice with the repository's canonical date parser; test `Sun, 23 Aug 2026 ...` -> `2026-08-23`; discard rather than promote the malformed draft; rebuild current NCBI inputs; publish and verify `bundle-2026-08-23`; update the immutable runtime pin and release evidence. If NCBI changes before the build, use the normalized date from the exact downloaded source and record that substitution. |
| GeneReviews | Snapshot the canonical NBK1116 archive and current side data; rebuild the corpus locally through the documented pipeline; prove chapter/passages counts and search fixtures; publish a new immutable data-only bundle derived from the normalized 2026-08-30 snapshot (`corpus-data-2026-08-30-r1` unless the downloaded source identifies a later date); run the unused bundle-verification workflow; update deployment pins and issue #27 only after acceptance. |
| HPO | Preserve a strict metadata response bound but raise it above the observed 69,911-byte GitHub release response with defensible headroom; test below/at/above-bound behavior; restore the weekly workflow. Do not publish a duplicate because `db-v2026-06-23` is still upstream-current. |
| MaveDB | Make scheduled publishing idempotent: when `data-2026-06-24-s0` already exists, verify its immutable asset identity and exit successfully; fail on a mismatching collision. Re-run before the 2026-09-01 schedule. No duplicate release is created while Zenodo remains unchanged. |
| ClinGen | Run a fresh data build, compare its manifest to `data-clingen-2026-07-16`, complete the repository's redistribution review, and publish only if the reviewed snapshot differs and redistribution is permitted. |
| gnomAD | Verify the live GraphQL contract against upstream v4.1.1, then update response provenance, resources, docs, and tests from 4.1.0 to 4.1.1. No local data bundle is created. |
| GenCC | Run the normal conditional refresh for the stale local cache and verify deployed diagnostics/ETag behavior. Do not invent a GitHub data release for its persistent-volume cache model. |
| HGNC, MGI, Mondo | Production data is current. Refresh ignored local developer databases when needed for local verification; do not publish redundant releases. Add/retain observable freshness evidence, particularly for Mondo's manual production refresh model. |
| Orphanet | Re-run the weekly builder and prove it continues to resolve `data-1.3.42-4.1.8-2025-03-03` idempotently. No new data release while the parsed source version is unchanged. |
| GTEx | Audit the supported v8/v10/snRNA catalog against current metadata and document whether the supported subset is intentional. No bulk artifact is created. |
| MetaDome | Preserve and visibly disclose the intentionally historical upstream dataset versions. No unavailable upstream refresh is fabricated. |
| AutoPVS1, LitVar, PanelApp, PubTator, SpliceAI Lookup, STRINGdb, UniProt, VEP | These are live-source/cache services; verify upstream availability and provenance but do not create fake data releases. |

Large downloads are streamed, size-bounded, checksum-verified before parsing, and staged outside
the serving path. Promotion is atomic. A failed build or verification leaves the previous selected
artifact intact and does not mutate an existing immutable release.

## 8. Testing and evidence model

Behavioral fixes use strict TDD:

1. add the smallest failing regression test and record the expected failure;
2. implement one root-cause fix;
3. prove focused success;
4. run the repository's complete required gate;
5. run container/data/live integration checks proportional to the changed boundary.

Dependency-only changes do not require an artificial failing unit test, but they require resolved
version inspection, import/CLI smoke, relevant schema/transport tests, full local CI, and fresh
GitHub checks. A failure is investigated with the systematic-debugging workflow before another
change is attempted.

Minimum evidence per changed backend:

- repository-native `make ci-local` passes;
- any advertised coverage, LOC, surface, README, schema, behavior, and conformance gates pass;
- production Docker target builds once and the exact image is scanned, SBOMed, and smoke-tested;
- CodeQL and dependency review pass with paired action versions;
- no caller `Authorization` header is forwarded to a backend;
- MCP remains Streamable HTTP at `/mcp`, errors retain `isError`, and research disclaimers remain;
- the PR diff contains only intended files and no secret, broad permission, or mutable image pin.

Minimum evidence for a data release:

- exact upstream URLs, timestamps/versions, sizes, and conditional metadata are recorded;
- compressed and expanded identities are verified according to the repository contract;
- schema/data invariants and representative queries pass;
- release assets, manifest, checksum set, and tag agree;
- deployment references the reviewed immutable tag/digest tuple;
- health/diagnostics report expected and actual identity after deployment.

## 9. Merge, release, and deployment policy

All changes use non-`main` branches and PRs. Never force-push shared bot branches, rewrite merged
history, mutate protected tags, or overwrite release assets. A merge requires a current base,
mergeability, reviewed diff, fresh local gate, and fresh GitHub checks. Previously green checks are
stale after a material `main` advance.

Changed application repositories receive a version bump and immutable GitHub/container release
through their existing protected workflow. Release evidence includes source revision, OCI digest,
SBOM, provenance/attestation, vulnerability result, MCP definition, and data identity where
applicable. Deployment uses only the supported digest-pinned Compose/NPM process. Backends remain
unpublished to host ports and reachable only through the router/reverse proxy.

If deployment credentials or infrastructure access are unavailable, publication still completes
and the exact deploy command/digest is handed off as an explicit external blocker; the program is
not declared complete until live revision and conformance evidence match the released artifact.

## 10. Rollback and failure handling

- Source regression: revert through a new reviewed PR; never reset protected history.
- Vulnerable or broken image: stop promotion, retain the last known-good digest, and publish a new
  corrective version after verification.
- Data build failure: retain the current selected immutable artifact and release; delete no valid
  release or local source cache.
- Malformed/partial release: leave it draft or remove only the invalid draft after exact target
  verification; never modify an accepted immutable release.
- Upstream transient: retry only bounded transient classes and preserve genuine syntax/schema
  failures. The UniProt 503 is resolved by rerunning the exact scheduled job, not weakening tests.
- GitHub API/schema change: fail closed, capture the new typed response, add a regression fixture,
  and update the accepted model deliberately.
- Licensing ambiguity: build and compare privately, but do not publish until the repository's
  redistribution review passes.

## 11. Completion criteria

Completion requires current evidence for every item below:

1. Router and all 21 backends have no unresolved superseded Dependabot PRs; every original PR is
   merged or linked to a merged verified replacement.
2. Default branches and the final release PRs have green source CI, CodeQL, dependency review,
   container CI, SBOM, conformance, and relevant data workflows.
3. Every released production image has zero fixable HIGH/CRITICAL findings under the versioned
   policy; GitHub dependency/code/secret alert APIs also report zero open alerts.
4. ClinVar and GeneReviews serve the newly verified immutable data releases.
5. HPO and MaveDB scheduled data workflows complete idempotently; Orphanet proves unchanged-source
   idempotency; ClinGen publication follows its redistribution decision.
6. gnomAD reports v4.1.1 provenance and all other data-bound/live services report honest source
   identities and freshness limitations.
7. Confirmed reboot restart-policy issues are closed with rendered-Compose and deployment evidence.
8. `DRIFT_ENABLED` and `FLEET_PROBE_ENABLED` are active and their current scheduled/manual runs are
   successful.
9. The trusted-builder parser accepts the current exact ruleset shape, the sealed ledger is fresh,
   and the control audit is green once the dedicated least-privilege credential is supplied.
10. Changed application releases are deployed through the supported mechanism; health revisions
    match release SHAs/digests; router drift is absent; 21/21 live backends pass transport and
    behavior probes.
11. The router's final `make ci-local` passes, including format, lint, LOC, mypy, unit,
    integration, conformance, discoverability, and release-control suites.
12. The security-profile assessment references the final router/fleet release identities and does
    not claim unverified controls.

The objective remains incomplete if any criterion is unsupported, skipped, stale, or represented
only by intent.
