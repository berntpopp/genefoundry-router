# Fleet Security Modernization Design

- **Date:** 2026-07-10
- **Status:** Approved for implementation; adversarial review required before merge
- **Scope:** `genefoundry-router`, all 21 active `*-link` backends, all open fleet
  issues, and all open Dependabot pull requests
- **Boundary:** Research use only; not clinical decision support
- **Supersedes:** Remaining work in
  `docs/specs/2026-07-07-fleet-security-remediation-design.md`

## 1. Objective

Bring the router and backend fleet to a demonstrable security posture above 9/10 without
adding material request latency or reducing read-only research usability. Completion requires
code, deployment, and live-runtime evidence. A green repository alone is not sufficient.

The work covers:

- router issues #3, #31, #32, #33, #35, and #36;
- `autopvs1-link` issue #41;
- `pubtator-link` issue #85;
- `genereviews-link` issues #27, #40, and #49;
- Dependabot PRs `gencc-link` #20/#21 and `gnomad-link` #29/#30;
- the fleet-wide FastMCP Host/Origin regression discovered during this review.

## 2. Verified Current State

### 2.1 Already complete

- The July 7 remediation phases 1-4 are released: UniProt IRI validation, PII-safe
  diagnostic rings and logs, CORS credential removal, and loopback base-Compose binds.
- AutoPVS1 uses an honest identifying User-Agent, emits scrape provenance, and validates
  parsed result shape.
- PubTator confines audit exports beneath a configured base directory and caps indexing
  lists at 200 items.
- The router refuses unauthenticated public binds, does not forward caller Authorization,
  rate-limits with bounded state when configured, protects metrics, caps request bodies, and
  emits PII-minimal audit logs.

The exact evidence must accompany tracker updates: AutoPVS1 honest-UA commit `4d9d140` and
`tests/unit/test_user_agent.py`; PubTator confinement/cap commit `6b1671c`,
`tests/unit/mcp/test_mcp_service_adapters.py`, and `tests/unit/test_review_rerag_models.py`.
These commits prove the named sub-controls only. PubTator's export race and deployment profile,
and AutoPVS1's effective production logging, remain open below.

### 2.2 P0 findings discovered during this review

1. The public PubTator endpoint is directly reachable without authentication and advertises
   the full 43-tool profile, including database, external-submission, and file-export tools.
   The router is authenticated, but its public backend URL bypasses that boundary.
2. PubTator's base Compose fixes `PUBTATOR_LINK_MCP_PROFILE=full`; production overlays do
   not replace it. Existing issue comments claiming a hosted lean/readonly profile are stale.
3. AutoPVS1 production Compose sets `AUTOPVS1_LINK_ENVIRONMENT=production`, but the current
   settings model ignores that name. Effective runtime settings remain development,
   `debug=True`, and INFO logging; raw client IP and User-Agent can therefore be logged.
4. AutoPVS1 has no central egress policy. Variant identifiers can reach BGI and Ensembl, and
   clients follow redirects without validating every destination.
5. All 21 backends received a compatibility commit disabling FastMCP Host/Origin protection
   after FastMCP 3.4.3 rejected legitimate proxy Hosts. This restored availability but left
   the application-layer MCP Origin requirement unenforced.

### 2.3 Remaining planned work

- Router #31 has no typed untrusted-content contract or implementation.
- Router #36 has neither Host allowlisting nor startup/polling drift enforcement.
- Eight ingest repositories still lack complete redirect, size, atomicity, decompression, or
  integrity controls.
- Four Dependabot PRs are open and unstable.
- Three GeneReviews product/spike issues remain open; parts of #27 and #40 are already shipped
  and must not be duplicated.

## 3. Design Principles

1. **One enforcement point per boundary.** Do not stack guards with different configuration.
2. **Fail closed for authorization, egress, write profiles, and changed tool definitions.**
   Availability events such as unreachable backends remain degraded/warn-only.
3. **Keep the hot path cheap.** Exact set membership, bounded schema checks, and hashes are
   acceptable. Model-based prompt-injection scanning and bulk integrity work stay off the
   ordinary request path.
4. **Preserve old good state.** Downloads write to same-directory temporary files, validate,
   flush, and atomically replace the destination only after success.
5. **Do not overclaim.** Same-release checksums detect corruption, not publisher compromise;
   textual fencing reduces risk but cannot force an LLM to ignore hostile content.
6. **Deploy without outages.** New service credentials and fail-closed egress controls use
   staged configuration rollouts.
7. **One source of truth.** Shared write-tool inventories, response contracts, dependency
   floors, and versions must be defined once and tested against runtime exposure.

## 4. Delivery Waves

### Wave 0: dependency and baseline hygiene

- Refresh the four Dependabot PRs against current `main`.
- Resolve their shared Ruff formatting failure, update stale dependency targets to current
  compatible releases, run each full local CI gate, adversarially review, and merge.
- Establish FastMCP `>=3.4.4,<4` as the fleet floor. Version 3.4.3 introduced the guard but
  broke proxy compatibility; 3.4.4 keeps the guard and makes protection explicit.
- The 3.4.4 API was verified on 2026-07-10 in an isolated `uv` environment, independently of
  this repository's pre-upgrade 3.4.2 lock. `FastMCP.http_app` accepts
  `host_origin_protection: bool | "auto" | None`, `allowed_hosts: list[str] | None`, and
  `allowed_origins: list[str] | None`. This isolated import/signature check is the prerequisite
  for the red dependency-upgrade test; the old lock is not evidence against the target API.
- Preserve full-SHA GitHub Action pins and review dependency changelogs for breaking or
  security-relevant behavior.

The refresh cutoff is 2026-07-10 23:59 UTC. Accepted targets are setup-uv 8.3.2, FastMCP
3.4.4, Uvicorn 0.51.0, Ruff 0.15.21, mypy 2.2.0, FastAPI 0.139.0, and mkdocstrings 1.0.4 where
each package is present. A PR may be superseded and closed rather than merged only when a newer
replacement PR contains the same dependency class and links the superseded PR.

### Wave 1: P0 deployment boundaries

Land PubTator readonly/auth controls and AutoPVS1 production/egress controls before lower-risk
hardening. These findings are request-reachable or data-transfer relevant today.

### Wave 2: fleet Host/Origin protection and router drift

Replace the emergency disabled-guard state with explicit trusted hosts/origins on all backends,
then add the router's single outer guard and drift tripwire.

### Wave 3: untrusted-content contract

Amend the response-envelope standard, implement a literature reference backend, make router
hint rewriting fence-aware, and add conformance coverage.

### Wave 4: ingest and supply-chain hardening

Apply the eight-repository redirect, cap, atomicity, decompression, archive, and checksum plan.

### Wave 5: remaining product issues

Complete the GeneReviews bundle automation, tracker residuals, and bounded annotation spike;
close stale discoverability tracking with current evidence.

### Wave 6: release and runtime verification

Apply SemVer bumps and changelogs per repository after merged behavior changes, update locks,
run release gates, deploy in dependency order, and verify public/private attack surfaces.

## 5. PubTator Write Boundary

### 5.1 Canonical capability inventory

Define one source-controlled `WRITE_TOOLS` set. Derive the readonly profile and router write
authorization from it. Tests must prove:

- every state-changing tool is in `WRITE_TOOLS`;
- every tool registered as read-only remains available in the readonly profile;
- annotations are checked for inventory drift but are never the sole runtime policy source.

The write set includes database/index writes, external submissions, and file exports. Existing
read-only annotation retrieval and topic-map tools must remain exposed to preserve usability.

### 5.2 Hosted default

Production and NPM Compose overlays explicitly set `PUBTATOR_LINK_MCP_PROFILE=readonly`, reset
published ports, and retain the hardened application container. A merged-Compose regression
test asserts the exact readonly tool inventory.

### 5.3 Write-enabled deployments

Write-capable profiles require a backend service credential and router user authorization:

1. `PUBTATOR_LINK_MCP_SERVICE_TOKEN` protects the entire `/mcp` transport using constant-time
   bearer comparison; `/health` remains unauthenticated.
2. The router registry can name a `service_token_env` per backend. The router constructs a
   backend-only Authorization header while `forward_incoming_headers=False` remains invariant.
3. A canonical router authorization rule requires a dedicated `pubtator:write` scope for tools
   in `WRITE_TOOLS`; ordinary authenticated users remain read-only.
4. A non-loopback write deployment without a service token refuses startup. Loopback-only
   development requires an explicit, logged exception.

Rollout order prevents outage: deploy router header support, configure the token so the old
backend ignores it, deploy the backend requirement, verify direct `401` and router success, then
remove the temporary compatibility path. Token rotation uses current+next acceptance briefly;
caller OAuth tokens are never reused.

### 5.4 Export race hardening

Eliminate caller-selected nested export paths. Generate the leaf filename server-side beneath a
dedicated writable export directory and create it with exclusive/no-follow semantics. This closes
the remaining parent-symlink time-of-check/time-of-use gap without affecting ordinary reads.

## 6. AutoPVS1 Data-Transfer Boundary

### 6.1 Settings and logging

Make `AUTOPVS1_LINK_ENVIRONMENT=production` authoritative through an explicit alias or a
consistent prefixed settings model. A clean-process test must prove production implies
`debug=False` and WARNING logging. Independently classify `client_ip` and `user_agent` as
sensitive so a future preset regression cannot expose them.

### 6.2 Central egress policy

All outbound AutoPVS1 and Ensembl requests use one exact-origin policy:

- default origin set is empty and denies external egress;
- production research configuration explicitly lists the current BGI and Ensembl HTTPS origins;
- patient/on-prem configuration omits public origins or points only to an institutional service;
- origins reject userinfo, query, fragment, non-HTTPS schemes, lookalikes, and wildcard domains;
- redirects are manual, bounded, and validate every `Location` before the next request;
- HTTPS downgrade is always rejected;
- health probes and variant recoding use the same policy.

Denied calls return a structured `external_egress_disabled` response and emit metadata-only audit
events. Static origin lookup is O(1) and does not add network round trips.

The hospital deployment additionally requires a network-level egress proxy/firewall policy.
Application checks constrain intended code paths; they do not contain a compromised process.

## 7. Host and Origin Protection

### 7.1 Backends

Each backend upgrades to FastMCP 3.4.4+ and removes the emergency global disable. Its MCP app uses
strict protection with explicit allowed service/public hosts and browser origins. Defaults include
FastMCP's loopback hosts; deployment configuration adds exact public and internal proxy/service
Hosts. Backends do not accept `*` in production.

Tests run through the real assembled application and cover:

- valid proxy/service Host;
- loopback development Host;
- invalid Host returns 421;
- missing Origin for non-browser clients succeeds;
- present invalid and `null` Origin return 403;
- browser same-origin behavior;
- GET, POST, and DELETE transport methods where supported.

### 7.2 Router

The router keeps a single outer transport-security middleware so `/mcp`, `/health`, `/metrics`,
and auth discovery routes share one boundary. It replaces the current Origin-only middleware and
does not also enable FastMCP's inner guard.

`GF_ALLOWED_HOSTS` is compatibility-optional in development: empty disables Host validation. A
non-loopback production deployment requires a non-empty list and refuses startup without one.
The list contains exact Host values, includes loopback for health checks, and includes the hostname
from the configured public base URL. Invalid Host returns 421 without echoing attacker input.
Origin semantics remain:
missing is accepted for non-browser clients; a present value must be allowed.

## 8. Tool-Definition Drift

### 8.1 Baseline

Ship a reviewed runtime baseline in the wheel and production image. Capture it at the identical
post-normalization catalog stage used by startup/polling checks. Fingerprints cover name,
description, input schema, output schema, execution annotations, and other security-relevant tool
metadata with deterministic canonical JSON.

### 8.2 Policy

Reuse the catalog already harvested by `relist`; do not perform a second network sweep.

- `changed`: production fails startup because it is the strongest tool-poisoning/rug-pull signal.
- `added`: quarantine the unreviewed tool and expose degraded status; do not prevent the remaining
  reviewed catalog from starting.
- `removed`: warn and expose degraded status; do not prevent startup.
- unreachable backend: availability warning, excluded from definition comparison.
- partial catalog from a reachable backend: removal warning, never silently re-pinned.
- missing/invalid baseline: production fails configuration; development warns.

The comparison first scopes the reviewed baseline to configured, enabled backends. It then excludes
fully unreachable namespaces from both sides. A reachable partial catalog is not excluded.

Run after startup harvest and every polling relist. A polling change or addition is disabled before
list/search/call exposure; unaffected reviewed tools remain available. Production configuration uses
a non-zero poll interval. Metrics, structured logs, and health detail expose drift without recording
tool inputs or outputs. Baseline refresh is always an explicit reviewed operation.

## 9. Untrusted Content Contract

### 9.1 Standard amendment

Publish Response-Envelope Standard v1.1 rather than silently changing frozen v1. The amendment
adds a typed untrusted-text object carrying:

- normalized text in a dedicated structural field;
- source/backend/tool identifiers and retrieval timestamp;
- upstream accession/URL/version and available content hash;
- sanitation/normalization transformations;
- trust classification and research-use limitation.

Use Unicode NFC, strip disallowed control and zero-width formatting characters with an explicit
allowlist for legitimate whitespace, cap text bytes/depth/counts, and preserve scientific symbols.
Do not regex-delete instruction-like prose. Inline delimiters, if mirrored for humans, use escaped
content and a per-response nonce; the structured field is canonical.

The standard sets default ceilings of 2 MiB per text object, 128 untrusted objects, nesting depth
8, and 8 MiB total untrusted text per tool result. Backends with measured legitimate outputs above
these limits document a narrower tool-specific exception and retain a hard ceiling.

Fencing is defense-in-depth, not model isolation. Hosts must still authorize subsequent calls
against user intent and prevent tainted content from combining private reads with external writes.

### 9.2 Reference implementation and router behavior

Implement the contract in one literature backend, preferably PubTator, then add shared conformance
tests for every free-text-returning backend. The router validates/stamps conformance metadata but
does not recursively sanitize biomedical prose.

`docs/conformance/untrusted-text-inventory.yml` is the completeness source. It enumerates each
backend/tool, the JSON pointers containing externally sourced free text, provenance fields, byte
limits, compatibility behavior, and test vector. CI compares the inventory with the live normalized
catalog so new text-returning tools cannot bypass review. Standard v1 consumers retain their
existing `data` fields for one compatibility release; the v1.1 typed object is additive during that
window, and the mirrored model-facing `content[]` uses only the fenced representation. Removal or
reshaping of the legacy field requires the backend's next major version.

`NamespaceHintMiddleware` must treat the untrusted-text object as opaque. A regression test places
a bare tool name inside a hostile abstract and proves the router does not rewrite it into a valid
namespaced command. Trusted envelope navigation hints may still be rewritten.

## 10. Ingest and Artifact Hardening

### 10.1 Shared downloader contract

All eight repositories use the same behavioral contract without introducing a shared runtime
package:

1. Validate the initial URL before sending.
2. Disable automatic redirects; validate HTTPS, host, port, userinfo, and every `Location` before
   sending the next request; reject downgrade; cap at five hops unless live evidence requires less.
3. Treat `Content-Length` and release metadata as prechecks only; count actual streamed bytes.
4. Use generous configurable limits derived from current measured artifacts plus operational
   headroom. Limits protect resources rather than constrain ordinary dataset growth.
5. Write to a same-directory temp file, hash/count while streaming, flush/close, validate, then
   `os.replace`. Broad `finally` cleanup preserves the prior good artifact.
6. Bound decompressed output while producing it. Never whole-buffer large archives or payloads.
7. Treat HTTPX read timeouts as per-chunk inactivity; add a configurable total monotonic deadline.

Allowed redirect chains are exact and evidence-based. GitHub release flows may traverse
`github.com` and `release-assets.githubusercontent.com`; PURL-backed sources include only the
verified PURL/GitHub chain. Fixed direct APIs use redirects disabled.

### 10.2 Per-repository scope

| Repository | Required controls |
|---|---|
| ClinVar | Atomic/capped NCBI sources; bounded gzip and zstd; strict 64-hex digest; bounded API/sidecar bodies; GitHub hop validation. Missing sidecar already fails closed. |
| GenCC | Fixed endpoint, redirects off, atomic stream, 128 MiB-class configurable cap. |
| HPO | Validated PURL/GitHub chain, atomic sources, bounded manifest/zstd/SQLite, strict manifest fields and digest. |
| HGNC | Redirects off for fixed REST/GCS paths, atomic download, capped JSON before parsing. |
| MGI | Redirects off for fixed MouseMine/report endpoints, atomic capped reports. |
| Mondo | Validated PURL/GitHub chain for OBO and direct raw-host policy for SSSOM; atomic capped files. |
| Orphanet | Atomic/capped XML; streamed GitHub gzip; bounded decompression; strict digest; validate database before replace. |
| MaveDB | Redirects off for API; bounded atomic Zenodo and GitHub downloads; mandatory digest; bounded zstd; tar/ZIP entry/path/count/member/total/duplicate protections. |

Same-release checksums are documented as corruption detection. Publisher authenticity remains a
separate signed-attestation/key-management project and cannot be claimed complete here.

## 11. GeneReviews Issues

### 11.1 Issue #27: corpus bundles

A real corpus release, checksum, restore path, Docker default, and verification workflow already
exist: release `corpus-2026-05-12-r1` contains the bundle and `.sha256` asset;
`.env.docker.example` and `tests/test_docker_compose_config.py` preserve `BUNDLE_URL=latest`; and
`.github/workflows/verify-corpus-bundle.yml` exercises restore validation. The residual is recurring
producer automation, provenance completeness, documentation, and a recorded successful run:

- replace the disabled build stub with a manual/monthly bounded workflow;
- populate app SHA/version, migration versions, corpus source checksums, model revision, and counts;
- build, validate, publish, then invoke restore verification;
- preserve a manual dry-run and concurrency lock;
- smoke-test the BRCA1 query after restore;
- document build, publication, pinned/latest restore, fallback ingest, and verification paths;
- keep secrets minimal and Actions pinned to full SHAs.

Close #27 after a real workflow run and fresh-volume restore prove the acceptance criteria.

### 11.2 Issue #40: residual product polish

Markdown tables, token estimates, and `get_abstract`/`get_links` value-add descriptions are already
shipped in route/tool descriptions. Add the tracker-requested README sentence explaining their
caching, normalization, structured-error, and cross-reference value before implementing the
remaining two features:

- `revision_history`: store versioned chapter/section hashes and metadata at ingest, compute bounded
  section-level added/changed/removed summaries, and expose additive history in chapter metadata;
- `get_variant_context`: compose existing chapter resolution and passage retrieval with a bounded,
  documented variant/founder/hotspot/modifier query expansion; no new model on the hot path.

Tests cover first ingest, unchanged reingest, changed/removed sections, deterministic ordering,
unknown variants, token/result limits, provenance, and unchanged existing response fields.

### 11.3 Issue #49: annotation probe

Deliver the bounded CPU-only script and report described in the existing issue plan. Heavy model
dependencies remain ephemeral `uv run --with` inputs, outputs live outside Git, model revisions and
licenses are recorded, sampling is deterministic, and no runtime/schema/ranking behavior changes
until measured coverage justifies a separate design.

The spike closes after the reproducible script, deterministic JSONL, and summary report publish
per-category recall and the three marquee-anchor outcomes, even if results are negative. Production
adoption always requires a new issue and approved design with explicit quality/latency thresholds.

## 12. Tracker and Issue Hygiene

- Correct stale #32/#41 comments: honest UA/provenance are complete; production env/logging and
  enforceable egress are not.
- Correct stale #33/#85 comments: path jail/caps are complete; live anonymous full profile and
  missing write authorization are the real residuals.
- Close router #3 with evidence that the tools exist and the original failure was client deferred-
  tool sequencing. Open a new focused issue only if a reproducible current discoverability defect
  remains.
- Close umbrella #35 only after all eight repository PRs merge and live/runtime checks pass.

The #35 closure comment also records evidence for completed phases 1-4 and StringDB hardening/error
masking: implementing PR, release/version, guard test, and live conformance result for each theme.

### 12.1 Issue closure evidence matrix

Every row requires linked implementing PRs, the named test commands, merged default-branch SHA,
version/release or immutable image digest, issue status comment, and the additional evidence below.

| Issue | Closure evidence beyond green CI |
|---|---|
| router #3 | Reproduce current discovery flow; show tools exist and deferred-tool sequencing caused the historical failure; close as stale or open a new focused reproducer. |
| router #31 | Standard v1.1, inventory, PubTator reference release, CI contract, and router opaque-subtree regression; fleet adoption remains a scored release gate. |
| router #32 / AutoPVS1 #41 | Effective production logging proof; application and network egress tests; deployment classified as public research or patient/on-prem; approved origin register; DPO/operator decision to disable, self-host, or authorize BGI transfer. Keep open if governance evidence is absent. |
| router #33 / PubTator #85 | Corrected tracker evidence for shipped cap/jail; no-follow export creation; readonly public catalog; direct backend 401; router service token; writer-scope deny/allow; merged Compose and live catalog proof. |
| router #35 | Phase 1-4/StringDB evidence plus all eight Phase 5 PRs/releases, first production refresh sizes, and umbrella live conformance. |
| router #36 | Unified outer Host/Origin tests through proxy/health/auth routes; packaged baseline; startup and polling drift tests; production Host list and non-zero poll; live clean/degraded/enforced probes. |
| GeneReviews #27 | Published workflow SHA, successful build run, immutable release assets/checksum/provenance, verification run, fresh-volume Docker restore, BRCA1 smoke query, and operator docs. |
| GeneReviews #40 | README note, revision-history and variant-context PRs, additive-schema tests, release, and issue checklist update for all five original items. |
| GeneReviews #49 | Reproducible pinned-model script, licenses, deterministic JSONL, per-category/anchor report attached to the issue, and explicit no-production-change conclusion. |

The four Dependabot PRs use a separate merge matrix recording exact refreshed target, lock/action
diff review, FastMCP Host/Origin behavior test where applicable, `make ci-local`, required GitHub
checks, merge or supersession SHA, and subsequent package-version bump.

## 13. Versioning, Pull Requests, and Releases

Every behavior-changing repository receives:

1. a focused branch and PR with issue links;
2. failing test before implementation and full `make ci-local` after;
3. Claude Code adversarial review plus local code review;
4. resolved must-fix findings and green required checks;
5. merge to `main`;
6. a separate SemVer bump/changelog PR after the functional PRs for that repository;
7. lock refresh, version-chain tests, and release/deployment verification.

PATCH applies to compatible security fixes. Response-envelope v1.1 is additive; any backend that
removes/reshapes existing fields instead requires the standard's breaking-change version policy.

## 14. Security Acceptance Rubric

Score each dimension 0, 0.5, or 1 from current evidence. Completion requires at least 9.5/10, no
zero, and no open P0/P1 finding:

1. Edge authentication, audience/scope validation, and no token passthrough.
2. Host/Origin/CORS/DNS-rebinding protections through the live proxy.
3. Capability minimization and write-tool authorization.
4. Untrusted output structure, provenance, bounded schemas, and fence-aware routing.
5. SSRF/redirect/egress controls and third-country transfer policy.
6. Artifact size/decompression/archive integrity and atomicity.
7. PII-safe logging, error masking, retention, and auditability.
8. Container/network hardening, SBOM, scanning, and deployment by digest.
9. Dependency currency, pinned CI actions, version consistency, and green gates.
10. Runtime evidence: external attack-surface scan, conformance, threat-model tests, and rollback.

Repository configuration can prove dimensions 1-9 partially. Dimension 10 requires live deployment
evidence. The final report lists every command, endpoint probe, PR, release, and residual exception.

## 15. Performance and Usability Budget

- Host/Origin, service-token, scope, and origin-allowlist checks are in-memory comparisons with no
  external calls.
- Drift hashes only the already harvested catalog at startup/poll intervals.
- Untrusted-content normalization is linear in bounded returned text and happens once at the
  originating backend; the router does not deep-copy arbitrary payloads.
- Bulk caps, hashes, signatures, and decompression checks run only in ingest/build paths.
- Readonly PubTator retains every genuinely read-only tool; the write connector remains available
  through a separately authorized path.
- GeneReviews model inference remains offline; query-time retrieval stays deterministic.

No PR may add a request-path network hop or model inference without an explicit benchmark and
separate design approval.

## 16. Rollout and Rollback

Order deployment by dependency: router backend-token support, PubTator readonly/token, AutoPVS1
explicit egress config, backend FastMCP guards, router guard/drift, then the independent ingest and
product changes. Verify each stage before advancing.

Every change is an atomic PR and merge commit or squash commit. Rollback uses `git revert` and the
previous immutable image digest. Credential rotation keeps current+next only during the transition.
Baselines and checksums are never automatically re-pinned during rollback.

## 17. Authoritative References

- MCP Streamable HTTP transport and Origin requirement:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
- MCP authorization and token-passthrough prohibition:
  https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- MCP tools and untrusted annotations:
  https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- FastMCP 3.4.3 security release:
  https://github.com/PrefectHQ/fastmcp/releases/tag/v3.4.3
- FastMCP 3.4.4 compatibility release:
  https://github.com/PrefectHQ/fastmcp/releases/tag/v3.4.4
- OWASP prompt injection guidance:
  https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html
- GDPR Articles 5, 9, 25, and 44-46:
  https://eur-lex.europa.eu/eli/reg/2016/679/oj
- HTTPX redirects, streaming, and timeout semantics:
  https://www.python-httpx.org/quickstart/ and
  https://www.python-httpx.org/advanced/timeouts/
- Python archive extraction warnings:
  https://docs.python.org/3/library/tarfile.html#extraction-filters and
  https://docs.python.org/3/library/zipfile.html
- OWASP logging guidance:
  https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
- GitHub Actions secure use and artifact attestations:
  https://docs.github.com/en/actions/reference/security/secure-use and
  https://docs.github.com/actions/security-for-github-actions/using-artifact-attestations/establishing-provenance-for-builds
