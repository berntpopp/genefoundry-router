# Fleet Security, Pull-Request, and Data Remediation Design

> Historical record — this document records the approved design as of 2026-08-30. It supersedes
> the operational inventory in the July 2026 fleet-remediation documents. Current truth is
> established from GitHub, upstream source metadata, immutable releases, deployed health
> responses, and repository tests.

**Status:** Architecture and end-to-end execution approved in chat on 2026-08-30; adversarial
review corrections incorporated before implementation planning.

**Audit timestamp:** 2026-08-30, Europe/Berlin.

## 1. Goal

Return the GeneFoundry router and all 21 configured `*-link` backends to a fully reviewed,
mergeable, releaseable, and data-current state. The program must:

- remediate all fixable HIGH/CRITICAL container vulnerabilities under the versioned
  `fixable-high-critical-v1` policy without waivers;
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
truth: `genefoundry`, `genefoundry-bench`, and `genefoundry-mcp-security-profile`. The deployment
control repository `strato_v6_docker_npm` is also in scope because exact-digest deployment and
rollback cannot be made truthful without repairing its fail-open mutable-checkout path.

The program owner explicitly authorized source changes, GitHub-setting changes, replacement PRs,
closure of superseded bot PRs after replacement merge, immutable data/application publication,
supported production deployment, rollback, and evidence-backed comments/closure of the scoped
tracking issues on 2026-08-30. That authorization is scoped to the repositories and outcomes in
this document, including the deployment controller above; it does not authorize importing the
broad local PAT, publishing data without redistribution rights, overwriting immutable evidence,
force-pushing protected history, or deleting anything other than the exact audited malformed
ClinVar draft after its release database ID and contents are independently recorded.

Before modifying any repository, the executor must read its current `AGENTS.md` and any linked
repository-specific instructions in full. A local clone is accepted only when its `origin` matches
the scoped GitHub repository. Existing user changes are never overwritten. A central execution
ledger records repository, canonical origin, absolute task worktree, branch, owner, fetched base
SHA, PR, and state. Every mutation starts in a clean dedicated task worktree at exact current
`origin/main`; primary user checkouts and unrelated dirty worktrees are never used, and no physical
worktree or branch has two concurrent writers.

## 3. Reconciled starting evidence

At the audit timestamp:

- the router had 10 and the 21 backends had 165 open Dependabot PRs, for 175 program-wide;
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
    build/verify; publish/pin/deploy only when source and rights evidence permit it
                         |
                         v
Fleet closure
    app/data releases -> deploy backends -> router baseline/final release -> deploy router
                      -> strict live probes
                      -> security-profile assessment last
```

Workstreams B and C run concurrently only across disjoint repositories. Within one repository,
one writer owns one worktree and branch until an explicit ledgered ownership transfer. The
per-repository sequence is `mechanical security commits -> data source/workflow repair when needed
-> source-repair PR merge -> immutable data publication/no-op -> separate data-pin/version PR ->
application release`. A pin that is unknowable before source repair merges is never promised in
the pre-publication PR. The router workstream lands first whenever a backend depends on a new
trusted reusable-workflow release.

Separate implementation plans will cover:

1. router, GitHub controls, monitoring, and supporting repositories;
2. fleet container/dependency/PR consolidation and restart-policy remediation;
3. data-pipeline repair, corpus/bundle generation, immutable release promotion, and data pinning;
4. fleet releases, deployment verification, strict closure probes, baseline/final router refresh,
   supporting-repository evidence, security-profile refresh, and closure audit.

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
branch targeting, or removal of deletion/non-fast-forward controls. While the documented
independent-maintainer count is one, the exact approval count is zero; accepting one is not an
exact match. A transition to one approval is a separate reviewed control change after evidence of
a second independently controlled maintainer. The sealed ledger is regenerated only from
successful live API probes.

The scheduled audit uses a dedicated GitHub App, not a standing installation token or the local
classic PAT. The protected `control-audit` environment stores only the App ID and private key;
each run uses a full-SHA-pinned token-mint action to create a short-lived installation token scoped
to the exact router plus 21 backend repositories with `Metadata: read` and `Administration: read`.
The token is masked, expires at the App-defined short lifetime, and is never persisted to artifacts
or git credentials. App installation/repository changes, key rotation, and revocation are recorded
in the control ledger. Creating the App and supplying those two secrets is an explicit external
prerequisite; all code, environment-protection, and evidence work that does not require them
continues independently. A fine-grained PAT is not an implicit fallback.

### 5.3 Production monitoring

Set the canonical router repository variables `DRIFT_ENABLED=true` and
`FLEET_PROBE_ENABLED=true`. Run both workflows manually after enabling them and require successful
definition-drift and the monitoring workflow's exact configured 21-backend transport evidence.
Schema-derived behaviour is enforced separately by the strict closure workflow in Section 8;
warning-tolerant monitoring is not closure evidence. Heartbeat secrets are configured only when the
owner supplies dedicated monitor URLs; absence of a heartbeat URL must not be represented as a
configured dead-man switch.

### 5.4 Supporting repositories

- `genefoundry`: merge the reviewed website updates, protect `main` and semantic release tags with
  a solo-maintainer-safe PR rule, enable immutable releases, and validate the corrected explicit
  release target with a fresh container release. Tag protection blocks update/deletion; it does not
  block tag creation unless the exact release actor has a verified narrow bypass.
- `genefoundry-bench`: add a minimal reproducible CI/security baseline appropriate to the private
  benchmark repository and enable Dependabot where GitHub permits it.
- `genefoundry-mcp-security-profile`: preparatory review may start here, but the gitlink advance and
  assessment occur last in fleet closure, after final router/fleet release and deployment evidence.

No branch rule may require an approval that the sole maintainer cannot supply. A one-independent-
approval policy remains a documented future control whose operational precondition is a second
independently controlled maintainer account.

The public `genefoundry` release is held to the same supply-chain boundary: actions and base images
are immutable-pinned, the exact production artifact is scanned/SBOMed/attested, runtime is
non-root/read-only/expose-only, and release/deployment identity is recorded. `genefoundry-bench`
gets exact native CI commands, private-data/secret boundaries, applicable Dependabot ecosystems,
required default-branch checks, and successful run evidence; it does not acquire a fictitious
public release contract.

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

“Reviewed union” is an immutable cutover inventory per repository: original PR URL/number and head
SHA, dependency/action identity, old and new declared/resolved version, affected files, replacement
commit, and disposition. Re-query immediately before replacement merge and again before closure.
A new or changed PR is incorporated and reverified or remains explicitly open; it is never silently
treated as covered. The merged replacement SHA and current checks are recorded in both each closed
PR comment and the machine-readable closure ledger.

### 6.3 Restart-policy remediation

Scan every production/NPM Compose overlay against the router reference implementation. Confirmed
restart-after-host-reboot issues in the router, AutoPVS1, ClinGen, GeneReviews, gnomAD, LitVar, and
PubTator are fixed using the fleet's canonical restart semantics: long-lived application/database
services require `restart: unless-stopped`; init/data-build services require `restart: "no"` and
must not be converted into daemons. Workstream A changes the central standard, default
`ComposePolicy`, router reference overlay, and reusable-workflow tests first. Backend tests render
the effective Compose model and distinguish serving services from one-shot auxiliaries.

## 7. Workstream C: data correctness and releases

Data publication is source-driven. A workflow rerun is not equivalent to a new release, and a new
release is not created when the source identity is unchanged. Every published artifact uses an
immutable tag, cryptographic digest, manifest/provenance metadata, bounded processing, and the
repository's license/redistribution rules.

Source identity distinguishes an upstream-authenticated checksum, an independently approved prior
digest, and a capture-only digest when upstream publishes no checksum. A capture-only digest proves
which bytes were processed, not prior authenticity; its compensating controls are TLS to the
allowlisted canonical host, recorded response metadata, bounded streaming, archive/path validation,
schema/count invariants, representative fixtures, and independent artifact review. It is never
described as “checksum-verified before parsing.” Local computation provenance additionally records
code SHA, resolved lock digest, model name/revision/file digests, CUDA/Torch/runtime and hardware
identity, deterministic parameters, counts, fixtures, and builder identity. Attesting later
packaging does not retroactively attest the local computation.

| Repository | Required outcome |
| --- | --- |
| ClinVar | Replace the workflow's RFC-1123 string slice with the repository's canonical date parser; test `Sun, 23 Aug 2026 ...` -> `2026-08-23`. Record raw `Last-Modified`, normalized date, source URL, ETag, source digest, and retrieval time separately. If `bundle-YYYY-MM-DD` is absent, publish after verification; if it exists with identical source and asset identities, verify and no-op; if it exists with a different identity, fail closed until a repository-wide `bundle-YYYY-MM-DD-rN` extension is implemented/tested. Inspect the exact malformed draft ID and delete only that invalid draft; never promote it. Rebuild current NCBI inputs, publish the deterministic tag from the exact source (expected `bundle-2026-08-23` at audit time), and update the immutable runtime pin/evidence. |
| GeneReviews | First change `bundle publish-local` to default to no upload. Snapshot the canonical NBK1116 archive and current side data, then rebuild and verify locally with every upload/publish path disabled. Corpus identity is the exact listing relpath, upstream `last_updated`, tarball capture digest, and side-data digest set; `retrieved_at` is not the corpus version. Record model/runtime/build identities and prove chapter/passage/embedding counts plus representative search fixtures. Repair the data-only publisher/verifier under TDD: full-digest-pin PostgreSQL/pgvector, migrate before restore, consume actual checksum/manifest assets, bounded allowlisted downloads, attestation/internal-manifest checks, search fixtures, and a credential-free sealed handoff to the privileged publisher. Publication is prohibited until a dated written redistribution determination names the responsible reviewer/rights authority, applicable terms/version, permitted asset/use, required attribution, and durable evidence reference. If affirmative, select `corpus-data-{normalized-upstream-date}-rN` by checking all existing identities and publish only the verified data-only artifact; if denied/ambiguous, publish nothing (including drafts), retain the selected artifact, record the blocker, and leave the program incomplete. |
| HPO | Set the GitHub release-metadata bound to 128 KiB (131,072 bytes); test 131,071, 131,072, and 131,073-byte responses plus `Content-Length` preflight; restore the weekly workflow. Do not publish a duplicate because `db-v2026-06-23` is still upstream-current. |
| MaveDB | Make scheduled publishing idempotent: when `data-2026-06-24-s0` already exists, verify its immutable asset identity and exit successfully; fail on a mismatching collision. Re-run before the 2026-09-01 schedule. No duplicate release is created while Zenodo remains unchanged. |
| ClinGen | Run a fresh data build, compare its manifest to `data-clingen-2026-07-16`, complete the repository's redistribution review, and publish only if the reviewed snapshot differs and redistribution is permitted. |
| gnomAD | Verify the live GraphQL contract against upstream v4.1.1, then update response provenance, resources, docs, and tests from 4.1.0 to 4.1.1. No local data bundle is created. |
| GenCC | Run the normal conditional refresh for the stale local cache and verify deployed diagnostics/ETag behavior. Do not invent a GitHub data release for its persistent-volume cache model. |
| HGNC, MGI, Mondo | Production data is current. Refresh ignored local developer databases when needed for local verification; do not publish redundant releases. Add/retain observable freshness evidence, particularly for Mondo's manual production refresh model. |
| Orphanet | Re-run the weekly builder and prove it continues to resolve `data-1.3.42-4.1.8-2025-03-03` idempotently. No new data release while the parsed source version is unchanged. |
| GTEx | Compare the advertised upstream dataset IDs to the literal supported set `{gtex_v8, gtex_v10, gtex_v10_sn_rna_seq}`; every excluded current ID gets a documented compatibility reason and fixture/query result. No bulk artifact is created. |
| MetaDome | Preserve and visibly disclose the intentionally historical upstream dataset versions. No unavailable upstream refresh is fabricated. |
| AutoPVS1, LitVar, PanelApp, PubTator, SpliceAI Lookup, STRINGdb, UniProt, VEP | These are live-source/cache services; record canonical upstream URL, HTTP result/time, advertised source/version fields (or explicit `not_available`), a representative query, and the repository-defined bounded transient classification. Do not create fake data releases. |

Large downloads are streamed, size-bounded, given the strongest available integrity classification
above, and staged outside the serving path. Promotion is atomic. A failed build or verification
leaves the previous selected artifact intact and does not mutate an existing immutable release.

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
- MCP remains Streamable HTTP at `/mcp`, errors retain `isError`, and research disclaimers remain;
- the PR diff contains only intended files and no secret, broad permission, or mutable image pin.

The no-token-passthrough invariant is final router proxy integration evidence: a test supplies a
caller `Authorization` header, proves it is stripped, and proves only the exact backend-specific
configured credential is injected for that backend. Backend tests instead prove inbound client
credentials are never reused as upstream-service credentials.

Minimum evidence for a data release:

- exact upstream URLs, timestamps/versions, sizes, and conditional metadata are recorded;
- compressed and expanded identities are verified according to the repository contract;
- schema/data invariants and representative queries pass;
- release assets, manifest, checksum set, and tag agree;
- deployment references the reviewed immutable tag/digest tuple;
- health/diagnostics report expected and actual identity after deployment.

Scheduled drift/fleet monitoring may retain availability-as-warning behavior, but a green scheduled
conclusion is not closure evidence. The closure job/command fails on every nonzero probe, records an
exact 21/21 summary, and runs both MCP Transport Standard v1 and the schema-derived
`docs/conformance/behaviour.py` probes for every configured backend.

## 9. Merge, release, and deployment policy

All changes use non-`main` branches and PRs. Never force-push shared bot branches, rewrite merged
history, mutate protected tags, or overwrite release assets. A merge requires a current base,
mergeability, reviewed diff, fresh local gate, and fresh GitHub checks. Previously green checks are
stale after a material `main` advance.

Changed application repositories receive a version bump and immutable GitHub/container release
through their existing protected workflow. Release evidence includes source revision, OCI digest,
SBOM, provenance/attestation, vulnerability result, MCP definition, and data identity where
applicable. The release workflow builds the production OCI artifact once; that exact digest is the
object scanned, SBOMed, smoke-tested, attested, and published. PR CI remains separate pre-merge
evidence. Deployment uses only the supported digest-pinned Compose/NPM process. Backends remain
unpublished to host ports and reachable only through the router/reverse proxy.

The deployment controller is repaired under TDD before promotion: pinning consumes an exact
supplied application manifest rather than `latest`; its lock and frozen release ledger bind
manifest digest plus image/source/Compose/data/current/target/prior tuples; remote checkout or
rendered-Compose drift fails before container operations; deploy uses registry pull plus
`up --no-build`; and rollback restores and verifies the exact prior application/data/Compose tuple.
The existing fail-open `git pull` continuation and build-oriented `deploy` path are prohibited for
fleet services.

Before promoting each service, a deployment record names the responsible operator and supported
stack/host path, current and target image/data tuples, prior release manifest, volume/schema
compatibility, backup requirement, exact deploy command, ordered health/revision/MCP/behavior
checks, bounded observation window, and exact rollback command restoring both prior image and prior
data pin. Promotion does not start without a verified prior tuple and rollback path. The central
mutation ledger also records the authority, required credential, approval source, reversibility,
verification, and rollback for GitHub variables/rulesets/environments, PR closure, draft deletion,
release publication, and production deployment. The 2026-08-30 owner authorization supplies the
approval source for in-scope operations; rights evidence and dedicated secrets remain separate
preconditions.

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

1. Router and all 21 backends have no unresolved superseded Dependabot PRs; the immutable cutover
   inventory maps every original PR/head to a merged verified replacement or an explicit still-open
   disposition, and a fresh closure query finds no unrecorded change.
2. Default branches and the final release PRs have green source CI, CodeQL, dependency review,
   container CI, SBOM, conformance, and relevant data workflows.
3. The current selected production image for all 22 applications and every new image produced by
   this program has zero fixable HIGH/CRITICAL findings under the versioned policy; historical
   immutable releases remain untouched. GitHub dependency/code/secret alert APIs report zero open
   alerts at the recorded closure timestamp.
4. ClinVar serves its newly verified immutable data release. GeneReviews has a fully verified local
   current corpus and repaired publisher/verifier; it serves a new immutable data release only when
   the dated redistribution determination is affirmative. A denied/ambiguous determination blocks
   completion and results in no publication, including no draft.
5. HPO and MaveDB scheduled data workflows complete idempotently; Orphanet proves unchanged-source
   idempotency; ClinGen publication follows its redistribution decision.
6. gnomAD reports v4.1.1 provenance and all other data-bound/live services report honest source
   identities and freshness limitations.
7. Confirmed reboot restart-policy issues are closed with rendered-Compose and deployment evidence.
8. `DRIFT_ENABLED` and `FLEET_PROBE_ENABLED` are active; current scheduled/manual monitoring runs
   are successful, and a separate strict closure run fails on any nonzero result and records 21/21
   transport plus 21/21 schema-derived behavior success.
9. The trusted-builder parser accepts the current exact ruleset shape, the sealed ledger is fresh,
   and the control audit is green once the dedicated least-privilege credential is supplied.
10. Changed application releases are deployed through the supported mechanism using completed
    deployment/rollback records; health revisions match release SHAs/digests; router drift is
    absent; 21/21 live backends pass strict transport and behavior probes.
11. The router's final `make ci-local` passes, including format, lint, LOC, mypy, unit,
    integration, conformance, discoverability, and release-control suites.
12. `genefoundry` has immutable pinned build inputs, current green supply-chain evidence, protected
    main/tags, immutable release identity, and deployment evidence; `genefoundry-bench` has its
    exact private-data-safe CI/security baseline and successful default-branch runs.
13. Satisfied tracked issues are closed from accepted evidence, then the security-profile
    assessment is the final source/control/deployment mutation; it references final router/fleet
    release, deployment, and controller identities and does not claim unverified controls. The
    only later repository-state exception is the detached closure-evidence tag/release described
    below; it cannot change source, controls, selected application/data releases, or deployment.
14. A machine-readable closure manifest is authored outside router source after that final
    repository mutation so it cannot invalidate the final router release. It records repository/default SHA and timestamp; original and
    replacement PR mapping; required-check matrix with run IDs; action/reusable-workflow SHAs;
    image/platform/scanner/version/database/verdict, SBOM and attestation digests; upstream/data and
    licensing identities; deployed tuples; transport/behavior summaries; GitHub alert query times;
    deployment-controller revision/ledger/preflight/rollback; and explicit `not_applicable`
    reasons. Evidence is at most 24 hours old at closure and is
    invalidated by any relevant source/workflow/base-image/scanner-policy/upstream/deployment change.
    Structural/freshness validation accepts an honest `blocked` record only in explicit blocked
    mode; strict completion remains nonzero. After the profile merge, every change-sensitive
    evidence class is rerun; a substantive result change restarts assessment, while an identical
    rerun may supersede only run/query timestamps. The external manifest/checksum are sealed as a
    planned detached immutable evidence tag/release targeting the final router SHA, never by a
    post-release source PR. That modeled seal is the sole post-assessment repository mutation; a
    final detached receipt proves its asset hashes and that all application/source/control/
    deployment identities remained unchanged.

The objective remains incomplete if any criterion is unsupported, skipped, stale, or represented
only by intent.
