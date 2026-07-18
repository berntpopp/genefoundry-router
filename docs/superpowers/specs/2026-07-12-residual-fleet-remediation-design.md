# Residual Fleet Remediation Design

- **Status:** Approved by task direction; ready for implementation planning
- **Date:** 2026-07-12
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Scope:** `genefoundry-router` and the 12 affected `*-link` repositories
- **Source issues:** R-01 through R-08 in the supplied issue brief
- **Boundary:** Research use only; not clinical decision support. No deployment or secret-setting
  changes are part of this work.

## Goal

Close every residual finding from the issue brief with behavior-level regression coverage, while
keeping every backend independently releasable and avoiding a new cross-repository runtime package.
The router remains a thin gateway and does not forward caller authorization to backends.

## Evidence and scope

The review confirms all 17 issues remain valid on the current revisions. They divide into five
delivery tracks:

| Track | Issues | Repositories | Result |
| --- | --- | --- | --- |
| Router deployment and drift | R-02, R-08 | router | Reviewed release baseline and explicit reachability policy |
| HTTP policy v1 | R-05 | GTEx, LitVar, MetaDome, PanelApp, SpliceAI, StringDB, UniProt, VEP | Full-origin, redirect, decoded-byte, fixed-error conformance |
| Backend behavior | R-01, R-03, R-04 | GeneReviews, UniProt, MaveDB | Bounded work and identifier-private failures |
| Supply chain | R-06 | GeneReviews, Orphanet | Immutable action pins and recursive pin checks |
| CI isolation | R-07 | MGI, UniProt | Network-hermetic and xdist-stable units |

R-01 and R-05 both touch GeneReviews' download path; R-03 and R-05 both touch UniProt's HTTP
client. Their implementation is sequenced inside those repositories, but the other tracks can run
in parallel without shared files.

## Architecture

### 1. Router: reviewed release baseline and explicit reachability

The router will make two currently implicit assumptions explicit.

1. A baseline snapshot is accepted only when it was captured from a declared release candidate
   fleet. The snapshot command records the candidate revision/identity manifest alongside the
   normalized MCP definitions; a source-controlled integration test recreates the release fixture
   and compares its full tool definitions with the packaged baseline. It must assert the corrected
   LitVar schemas/annotations, VEP read-only annotations, and the MetaDome action annotation.
   Snapshots must fail instead of retaining stale entries whenever a required release candidate is
   unreachable. A live production re-pin remains an operator deployment step, never an automatic
   CI rewrite.
2. Listener address is not a reachability classification. Add an explicit deployment mode
   (`development` or `production`), defaulting safely for the packaged production Compose
   configuration. In production, authenticated operation requires a positive rate limit and a
   metrics token even when the process listens on loopback. A separately named development-only
   override permits local loopback use, emits a warning, and is rejected by production Compose.
   Documentation describes proxy publication and trusted-forwarding assumptions.

### 2. Canonical outbound HTTP policy v1

The router repository owns a versioned, source-controlled recipe and conformance fixture rather
than a shared runtime wheel. A new package would create an independent release, registry, lockfile,
and rollback lifecycle before solving the present policy drift; vendored tests make policy adoption
visible in each repository's existing CI.

The canonical predicate accepts an outbound URL only when it is HTTPS, contains no syntactic
userinfo, and its normalized `(hostname, effective_port)` exactly matches a configured allowed
origin. Omitted port and `:443` are equivalent; a non-443 port is allowed only when the configured
origin explicitly contains it. Every `httpx` request hook, including redirect hops, evaluates this
predicate. Clients retain `follow_redirects=True` and a bounded redirect count so httpx preserves
POST redirect semantics.

Responses are read by a decoded-byte accumulator and fail before parsing when the configured cap
is crossed. `Content-Length` remains a preflight optimization, not the authority. Exceptions and
MCP envelopes use fixed messages without URL, hostname, scheme, port, or userinfo. Policy failures
are non-retryable.

Each affected backend vendors the same HTTP policy version marker and conformance suite, adapting
only its configured origins, cap, and documented service semantics:

| Backend | Preserve |
| --- | --- |
| GTEx | GET behavior and known HTTP downgrade rejection |
| LitVar | NCBI origin and authoritative streamed cap |
| MetaDome | POST status/envelope mapping |
| PanelApp | UK/AU origins, normalized pagination `next`, page/row bounds |
| SpliceAI | multi-upstream set and long prediction deadline |
| StringDB | generic/versioned origin redirect and POST semantics |
| UniProt | POST SPARQL execution and 32 MiB cap |
| VEP | GRCh37/GRCh38 origins, chunks and retry behavior |

Router CI maintains an adoption ledger (repository, policy version, conformance-file hash) and
fails when any listed R-05 backend is missing or stale. This is a source-only verification of
adoption; it does not probe external services.

### 3. GeneReviews: whole-operation resource ceilings

`download_guard` gains a monotonic total deadline independent of connect/read timeouts. Every
download caller supplies an intentional operation deadline appropriate to its artifact. The archive
reader accounts for declared and actual bytes for each regular member before suffix filtering:
ignored members consume the same cumulative decompression budget as retained NXML. It fails closed
on declared mismatch, individual cap, cumulative cap, or deadline exhaustion, while retaining only
valid NXML content.

Tests use slow-drip streams, ignored members, mixed ignored/NXML inputs, declared-size mismatch,
and highly compressible data. Existing valid corpus ingestion remains covered.

### 4. UniProt: bound query work

The default SPARQL policy admits bounded `SELECT` and `ASK` only. It rejects `CONSTRUCT`,
`DESCRIBE`, and real `SERVICE` tokens found in code, including nested groups; lexer handling must
ignore comments, literals, and IRIs. An operation-wide cancellation deadline surrounds execution
and retries, covering time to first byte. The existing response-byte cap remains an independent
boundary. Tool documentation and capabilities no longer promise graph or federated queries by
default.

### 5. MaveDB: fixed identifier-private errors

Resolution exceptions and envelopes retain stable code/retryability but use fixed identifier-free
messages for not-found, ambiguous, and resolution-failure paths. Normal/error logs contain class
and code only. Tests use distinctive valid HGVS values and assert their absence from responses,
raised strings, and captured logs.

### 6. Immutable GitHub Actions

GeneReviews and Orphanet replace every third-party `uses:` tag with the audited 40-hex SHA plus a
readable version comment. A recursive policy test scans workflow and composite-action YAML;
external actions must be full SHA pins while local `./` actions remain valid. Orphanet adds a
`github-actions` Dependabot configuration so updates remain reviewable. YAML/actionlint remains a
separate validation layer.

### 7. Hermetic and parallel-safe tests

MGI unit tests patch the downloader at the builder lookup site (or inject it) and install a
unit-only network-deny fixture. Live-download coverage is explicitly integration-marked with a
bounded timeout. UniProt test fixtures snapshot and restore process-global logger handlers,
filters, levels, and propagation; assertions retain the stricter fixed-message behavior. The
affected UniProt suite runs repeatedly under two xdist workers.

## Error handling and security invariants

- No caller `Authorization` header is forwarded to a backend.
- Every policy limit fails closed; truncating a body is not an acceptable success mode.
- Caller-supplied URLs, SPARQL, HGVS, hostnames, and userinfo never appear in policy errors or
  ordinary logs unless explicitly approved as a structured field (none is needed here).
- Backends remain Streamable-HTTP services reachable only through the router/reverse proxy.
- No deployment, live baseline rewrite, or GitHub repository-setting mutation is automated.

## Verification design

Each code change follows a red-green TDD cycle. Every affected repository runs its own required
`make ci-local`; router verification additionally runs the offline release-candidate baseline
integration test and the fleet HTTP-policy adoption check. Before handoff, a completion ledger maps
each R-01…R-08 acceptance criterion to its test, source behavior, and fresh command output.

## Non-goals

- Publishing a shared HTTP-policy package.
- Deploying any backend/router image or re-pinning a baseline from uncontrolled live endpoints.
- Altering backend authentication boundaries, response envelope semantics, or unrelated refactors.
