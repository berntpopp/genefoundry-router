# Cross-fleet issue remediation design

**Date:** 2026-07-15

**Status:** Approved delivery scope — implementation proceeds through reviewed PRs, release/deployment verification, and evidence-backed issue closure.

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

**Scope:** `genereviews-link` #27, #40, #49; `uniprot-link` #28; `clingen-link` #45; `litvar-link` #67; `stringdb-link` #33; `autopvs1-link` #76; `pubtator-link` #127; `hpo-link` #23; `metadome-link` #19; and `orphanet-link` #23.

## Goal

Resolve the confirmed MCP-correctness, container-security, and immutable-data defects without
regressing the GeneFoundry response-envelope, tool-surface, research-use, or container-hardening
contracts. Every change is independently releasable by repository; no router result reshaping,
unauthenticated write surface, mutable production data selection, or serving-time ML inference is
introduced.

## Delivery model

Each repository receives a short-lived branch, focused test-first commits, a PR, full local
`make ci-local`, GitHub checks, and a deployment evidence record before the matching issue is
closed. Existing local repairs are released and externally re-probed rather than reimplemented.
Issue closure requires the exact acceptance criteria below, not a passing unit suite alone.
Version labels and old tags are evidence only: for a repair already present in `main`, the release
candidate must be bound to its exact commit SHA, then to the built image digest and deployed
`/health` revision. No wave may assume that an untagged version in `pyproject.toml` is live.

The work is sequenced as follows:

1. **Release verified local repairs:** UniProt #28, STRING #33, and MetaDome's already-fixed
   `minimal` response-mode defect.
2. **Data correctness and MCP usability:** AutoPVS1 #76, remaining MetaDome #19 defects, and
   PubTator #127. PubTator uses separate commits for each independently testable defect family.
3. **Deployment and data integrity:** LitVar #67, ClinGen #45, HPO #23, Orphanet #23, and
   GeneReviews #27.
4. **GeneReviews product work:** #40 is narrowed to durable revision history, documented
   composition, and README guidance; #49 is an offline, reproducible experiment with an explicit
   no-serving-change outcome.

## Shared invariants

- Execution failures return the fleet flat envelope with `isError: true`, a closed
  `error_code`, and an actionable parameter-specific message.
- Paginated result metadata always reflects the payload actually emitted. A response budget may
  reduce a page only when `returned`, `truncated`, and the next cursor/offset are recomputed.
- Read-only public profiles never advertise an unavailable write/indexing step; every required
  workflow step is registered and reachable in that profile.
- Production reference data is selected by exact release tag and trusted digest, materialized by
  a hardened init sidecar into a versioned volume, and mounted read-only/immutable by the app.
  Serving processes have no bootstrap fallback, data-write permission, or artifact-download
  egress.
- All changes preserve research-use-only wording and never turn retrieval/annotation results into
  clinical decision support.

## Repository designs

### UniProt #28 — release the MCP boundary repair and tighten the public contract

The public v4.0.3 service is stale; local v5.0.0 already repairs registered-tool dispatch so a
serialization/dispatch failure is an internal execution error rather than false `not_found`.
Release that repair first. Remove the unsupported `dna_binding` feature enum value, so callers
receive `invalid_input` naming `feature_types` instead of a successful empty response. Add the
existing `ResponseMode` to features, variants, and diseases: compact is default, minimal retains
stable identifiers and coordinates, and standard/full preserve complete present data. Pagination
and filtering honesty survive every projection.

**Acceptance:** deployed health advertises v5.0.0+; TP53 features and variants are non-empty;
forced registered-tool boundary failures are `internal` plus `isError`; `dna_binding` is absent
from the advertised schema and rejected actionably; compact results are measurably smaller than
full without false pagination.

### STRING #33 — release the local contract-hardening repair

The v4.1.0 checkout already adds a JSON/base64 image path, removes non-working link formats,
maps in-band STRING validation errors correctly, and bounds enrichment. Release it, then add
direct regressions for category filtering, FDR ordering, supplied limits, and truthful
`total_count`/truncation.

**Acceptance:** deployed health is v4.1.0+; images are decodable base64 with media metadata;
CFTR annotations work; only working link formats are advertised; enrichment with `limit=3`
contains at most three FDR-sorted terms and truthful metadata; invalid background is actionable,
non-retryable `invalid_input` with `isError`.

### AutoPVS1 #76 — parse only the selected clinical-validity value

The parser currently applies `.text.strip()` to a table cell containing a `<select>`, merging all
option labels. If a select is present, extract and normalize exactly the selected option; retain
the static-cell path. A select with no selected option produces a documented unavailable sentinel,
never a guessed value or concatenation.

**Acceptance:** fixtures prove a middle selected option, unselected neighbours, legacy static
text, and missing selection; an MCP-path test proves the normalized value reaches the response;
the CFTR audit call contains only `No Reported Evidence` (or the documented unavailable value).

### MetaDome #19 — distinguish local residue data from homologous meta-domain data

Never label cross-gene meta-domain aggregates as per-residue gnomAD or ClinVar counts. Per-position
counts contain only verified local evidence: local ClinVar records may provide counts, and absent
residue-level gnomAD data is explicit unavailable/null rather than zero. Homologous aggregates may
remain only under a separately named, provenance-scoped meta-domain block. Construct pages within
the response budget and recompute pagination from emitted rows; the generic character guard must
not silently remove paginated rows. The locally fixed `minimal` projection is released and
re-probed rather than changed again.

**Acceptance:** cross-gene/domainless/ClinVar-contradiction fixtures cannot produce misleading
per-position values; TP53 Pro72Arg is not reported as confidently zero; oversized first and
follow-up pages have no gaps or duplicates and metadata equals list lengths; deployed `minimal`
contains stable essential data.

### PubTator #127 — exact evidence, reachable read-only workflows, and bounded payloads

Split this issue into independent commits:

1. Normalize query and ClinVar HGVS/protein expressions, bind classifications to variation IDs,
   and expose only exact/equivalent hits as authoritative; unmatched broad-search records are
   explicit candidates, never equal peer evidence.
2. Select the relation edge endpoint opposite the queried entity, reject malformed/nonincident
   edges, and do not promise permanently absent metadata.
3. Normalize PMCIDs once, map bare upstream identifiers to canonical IDs, count actual meaningful
   documents, and distinguish invalid input, no full text, and upstream failure.
4. Add stable opaque cursor pagination and compact default session summaries; retrieve detail only
   through the status surface.
5. Retain the already-shipped v7.1.0 readonly write boundary and repair only the remaining static
   `next_tools`/workflow-profile mismatch: define a contiguous read-only workflow ending in public
   retrieval and suppress every unavailable indexing command. Full indexing remains available only
   to an authenticated configured profile; do not re-expose writes publicly.
6. Treat source-preflight as a deployment/live-contract investigation first. Add audit-PMID
   contract coverage and only alter semantics when current live evidence proves a source defect.

**Acceptance:** BRCA1 Cys61Gly never surfaces the unrelated benign Val191Ile as peer evidence;
relations chain to the other entity; unavailable/invalid PMCIDs return correct classified errors;
session pages are bounded and gap-free; every readonly workflow step is registered, contiguous,
and reachable; exact audit-PMID preflight behaviour is documented by a live-safe contract test.

### LitVar #67 — harden every supported effective Compose profile

This is a proposed follow-up, not a description of LitVar v6.0.0 production.
That release hardened the actual Strato base+NPM composition with read-only root,
safe `/tmp`, dropped capabilities, no-new-privileges, and init; PID limits and a
canonical base+prod+NPM profile remain to be implemented.

Move the mandatory hardening block into the base/NPM effective deployment path: read-only root,
bounded `noexec,nosuid` tmpfs, `cap_drop: ALL`, `no-new-privileges`, init, and an effective PID
limit. Retain only necessary production overrides. Test rendered base+NPM, base+prod, and
base+prod+NPM configurations separately; resolve the manifest/documentation disagreement about
which profile is canonical.

**Acceptance:** all supported rendered profiles carry every mandatory control; an ephemeral
container has `ReadonlyRootfs`, `CapDrop=ALL`, and `NoNewPrivileges`, cannot write root but can
write `/tmp`, serves health/MCP, and publishes no NPM host port.

### ClinGen #45 — enforce truthful previous-known-good release lineage

Keep data transformation credential-free. Before the publish job creates a draft, query the latest
published `data-clingen-*` release, read its manifest via the pinned GitHub CLI, normalize its
artifact digest, and compare it with the handoff's `previous_known_good_digest`. First publication
is explicit; mismatches fail before release mutation and direct the maintainer to re-pin source
lineage. Drafts/prereleases and the candidate tag are excluded.

**Acceptance:** build permissions remain `contents: read`; no-prior-release and matched-lineage
fixtures publish; stale lineage fails before draft or upload; the next real release manifest names
the immediately previous published digest.

### HPO #23 and Orphanet #23 — make immutable data declarations operative

Both services currently declare exact external artifacts but bootstrap mutable/latest-or-fallback
data in the application container. Introduce explicit production immutable-data settings for exact
tag, compressed/expanded hashes, expected data/schema identity, and no fallback. A hardened
`*-data-init` service downloads/verifies/materializes to `reference/<digest>/` under a lock,
fsyncs, and atomically selects `current`. The app depends on successful completion, mounts the
selection read-only, opens SQLite with `mode=ro&immutable=1`, and disables bootstrap/lifespan/
stdio materialization and related egress. Runtime cache, if any, stays separate. Update the
release declaration to `immutable-bundle` and declare the init auxiliary role.

**Acceptance:** `latest`, missing pins, digest/schema/identity mismatch, partial materialization,
and source-build fallback are rejected; rendered Compose proves hardened init/app separation;
app startup makes no download; container validation and immutable-bundle smoke pass. HPO's release
selection must use the full pinned tag, and Orphanet must stop trusting an artifact-provided
checksum sidecar as its trust root.

### GeneReviews #27 — align corpus verification and operations with the data-only release model

The data-only release sidecar is already the correct production architecture. Replace the obsolete
verification workflow with one that uses a pinned tag/digest and `SHA256SUMS`, applies migrations
before data-only restore, validates archive contents, restores unprivileged, builds required
indexes, and proves the specified BRCA1 RRF search. Remove obsolete in-server `BUNDLE_URL=latest`
claims and document recurring reviewed promotion from source bundle through attestation and atomic
`container-release.json` update. The server never downloads or restores corpus data.

**Acceptance:** a fresh production-equivalent volume restores the pinned artifact and returns the
required BRCA1 result; schema-bearing/mismatched/unpinned artifacts fail; docs match runtime; a
subsequent corpus promotion has a reproducible reviewed procedure.

### GeneReviews #40 — durable revisions and composition before new tools

Token estimates are already delivered. Keep `markdown_table` removed: duplicating fenced source
text violates the ratified response contract; document client rendering from structured cells.
Add an immutable chapter-revision ledger and per-section content hashes/deltas, with a first-seen
baseline and no fake delta after a failed ingest. Provide bounded historical retrieval. Add README
guidance for normalized `get_abstract` and categorized `get_links`. First publish a
`search_passages_batch` variant-context recipe; add a retrieval-only wrapper only if the composed
path demonstrably fails the documented workflow, preserving Tool-Surface Budget.

**Acceptance:** unchanged ingest records no revision; changed sections yield precise ordered
deltas; baseline/failed-ingest semantics are tested; README guidance is test-covered; the issue
records markdown-table's deliberate supersession and token estimate completion.

### GeneReviews #49 — reproducible offline hybrid-annotation evaluation

This is not a serving change. The probe requires an exact corpus release/digest (or a
checksum-locked gold passage fixture), six explicit gold passages, category-specific required
anchors, fixed dependency/model revisions, deterministic sampling, raw and resolved spans, and
JSONL evidence with environment metadata. Evaluate per-category normalized-anchor recall,
per-case conjunctions, false-positive samples, and latency over a checksum-pinned benchmark;
record both its raw-line count and semantic query count, rather than hard-coding the disputed
299/300 cardinality. HFE is a non-regression guard; CFTR and GRIN2B are the improvement targets.

**Acceptance:** a CPU command produces deterministic evidence for every valid query in the
checksum-pinned benchmark, six gold passages, and 50 seeded samples, recording raw-line and
semantic-query counts; missing corpus/gold data is rejected; overlapping spans and HGVS parsing
are deterministic; output states whether CFTR/GRIN2B improve without HFE regression; no
production dependency, schema, or retrieval-ranker change is introduced.

## Non-goals

- Do not close an issue because an old branch or unit test exists; deployment/release evidence is
  required where the public endpoint or artifact is implicated.
- Do not introduce aliases for breaking leaf-tool renames, weaken data integrity to preserve a
  fallback, or make PubTator indexing writable on the unauthenticated public profile.
- Do not turn the #49 experiment into an online annotation dependency without a subsequent,
  separately approved product design.

## Verification and closure audit

Each PR runs the repository's `make ci-local`; affected container/data work also runs the rendered
Compose and local smoke commands named in its implementation plan. After merge, query the exact
main SHA's GitHub checks, tag only that verified SHA, deploy the immutable/released artifact, and
rerun the issue's public MCP or container acceptance probe. Record the SHA, tag, version/image or
data digest, commands, and output summary in the issue before closure. The router's behaviour and
surface gates remain the fleet-wide regression backstop; they complement, not replace, the
issue-specific tests above.
