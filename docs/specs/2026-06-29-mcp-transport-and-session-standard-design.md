# MCP Transport & Session Standard v1 — Design

- **Status:** DRAFT (design approved; pending spec review → implementation plan)
- **Date:** 2026-06-29
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Scope:** All GeneFoundry `-link` MCP servers and the `genefoundry` router.
- **Companion standards:** [Tool-Naming v1](../TOOL-NAMING-STANDARD-v1.md),
  [Response-Envelope v1](../RESPONSE-ENVELOPE-STANDARD-v1.md),
  [Container-Hardening v1](../CONTAINER-HARDENING-STANDARD-v1.md), Logging & CLI v1.
- **Deliverable of the implementation plan:** `docs/MCP-TRANSPORT-STANDARD-v1.md` (the
  normative standard) + a shared conformance probe + per-repo migration PRs.

## 1. Context & motivation

A 2026-06-29 fleet-wide docker + MCP validation (rebuild → run → MCP handshake → tool
call, via curl and codex) confirmed all 21 `-link` servers build, run, and serve MCP, but
surfaced **transport-surface drift** that the ad-hoc fixes that day only partially closed:

1. **Endpoint path split.** 7 servers (autopvs1, clingen, clinvar, gnomad, spliceailookup,
   vep, litvar) served the MCP app at `/mcp/` and **307-redirected `POST /mcp`** — fragile
   for strict clients (a 307 on POST is not re-sent by every HTTP stack) — while the rest
   served `/mcp` directly. Root cause: mounting the MCP ASGI app *at* the path
   (`app.mount("/mcp", http_app(path="/"))`) makes Starlette's `Mount` add the redirect,
   versus the correct `app.mount("/", http_app(path="/mcp"))`. (Patched per-repo that day;
   this standard codifies it.)
2. **Transport-mode split (the deeper issue).** ~13 servers run FastMCP's **stateful + SSE
   default** (no `stateless_http`/`json_response` overrides — gencc, genereviews, gtex, hgnc,
   hpo, mavedb, metadome, mgi, mondo, orphanet, panelapp, stringdb, uniprot), while the rest
   run **stateless + JSON** (`http_app(path=mcp_path, stateless_http=True, json_response=True)`).
   The stateful set is stateful only because it is the library default — **none of those tools
   use the features statefulness provides.** Two of them (genereviews, stringdb) *also* still
   serve `http_app(path="/")` mounted at `mcp_path`, so they carry **both** the 307 and the
   stateful default; the router (`genefoundry`) shares the same redirect-prone construction.
3. **serverInfo drift.** Free-form names (`"AutoPVS1 Link" v1.3.0`, `"GeneReview Link Tool"
   v3.2.4`, `"StringDB-Link Server" v3.3.1`) vs the canonical `<name>-link`. (Patched.)
4. **Health-path split.** `/health` vs `/api/health` across the fleet.

### The pivotal finding: MCP-transport sessions ≠ application sessions

The MCP Streamable-HTTP spec's **session** mechanism (the `Mcp-Session-Id` header, optionally
assigned in the `InitializeResult`) is a **connection-layer** facility that exists for
**server-initiated messages, sampling, elicitation, and SSE streaming**. Using it forces
**session affinity** — every subsequent request must reach the same server replica.

That is a different thing from **application sessions** — e.g. pubtator-link's *research
sessions* (`stage_research_session`, `index_review_evidence`, …). Those are **persisted in
Postgres** and addressed by a `session_id` **tool argument**. Decisively, **pubtator-link's
transport is already `stateless_http=True, json_response=True`** (`server_manager.py:240-241`):
it runs **stateless at the MCP layer** and still offers rich, expandable sessions — because
those live in the database, not the connection.

**Therefore:** the fleet can standardize on a **stateless transport** without giving up
session features. Stateful application behaviour is an **app-layer pattern**, fully
compatible with — and already implemented on — a stateless MCP transport.

## 2. Goals / non-goals

**Goals**
- One uniform, spec-compliant HTTP transport contract across all `-link` servers + the router.
- Eliminate the `/mcp` vs `/mcp/` redirect and the stateful/stateless split.
- Make **stateless + JSON** the canon (scale-out, router-friendly, predictable for clients).
- Document the **canonical application-session pattern** so session features (pubtator-style)
  are first-class and expandable.
- Provide an **automated conformance probe** and a per-repo Definition of Done.

**Non-goals**
- Re-specifying tool naming, response envelopes, logging, or container hardening (own standards).
- Mandating auth schemes (the router owns auth; the no-token-passthrough rule is reaffirmed).
- Removing application sessions or DB-backed state (encouraged, just not at the transport layer).

## 3. Transport contract (normative)

Keywords MUST / SHOULD / MAY per RFC 2119.

1. **Single endpoint, no redirect.** Each server MUST expose the MCP endpoint at **`/mcp`**.
   `POST /mcp` (no trailing slash) MUST return `200` directly and MUST NOT `307`-redirect;
   `GET /mcp` MUST likewise not redirect. In the stateless tier a server MAY answer `GET /mcp`
   with `405 Method Not Allowed` (it offers no SSE stream to open); the escape-hatch tier (§7)
   answers `GET /mcp` with an SSE stream. Implementation: build the MCP ASGI app with the path
   baked in (`mcp.http_app(path=settings.mcp_path)`) and mount it at root
   (`app.mount("/", mcp_app)`); the host app's own routes (`/health`, `/api/…`, and — on the
   router — the auth well-known routes) are registered before the mount and keep precedence.
   (Reference: `gtex_link/server_manager.py`, which already uses this mount pattern.)
2. **Stateless + JSON.** Servers MUST construct the MCP app with `stateless_http=True` and
   `json_response=True`. A server MUST NOT assign an `Mcp-Session-Id` at the transport layer
   in the default (stateless) tier.
3. **Protocol version.** Servers MUST negotiate `protocolVersion` in `initialize` and MUST
   honor the `MCP-Protocol-Version` request header on subsequent requests; MUST return
   `400 Bad Request` for an unsupported version; SHOULD assume `2025-03-26` when the header is
   absent (spec backwards-compat). Servers SHOULD accept current spec revisions
   (`2025-03-26`, `2025-06-18`, and later) by echo/negotiation.
4. **Accept handling.** Clients send `Accept: application/json, text/event-stream`; servers
   MUST tolerate this and, in the stateless tier, MUST answer `Content-Type: application/json`.
5. **Security.** Servers MUST validate the `Origin` header (DNS-rebinding protection). In
   containers, binding `0.0.0.0` is permitted because exposure is mediated by the reverse
   proxy; this MUST be documented (ties to Container-Hardening v1). The router MUST NOT
   forward the caller's `Authorization` header to backends (reaffirmed).

## 4. serverInfo contract (normative)

`serverInfo.name` MUST be `"<namespace>-link"` (lowercase, hyphenated; e.g. `gtex-link`,
`autopvs1-link`). `serverInfo.version` MUST be the package's semantic version. Free-form
display names are prohibited.

**Router exception.** The router is the one server whose `serverInfo.name` MUST be
`"genefoundry"` (the federation's published identity at the edge), not `genefoundry-link`.
The `<namespace>-link` rule applies to backends only; the conformance probe enforces the two
cases under separate profiles (§8).

## 5. Health & ops (normative)

Each **backend** MUST expose **`GET /health`** returning `200` with a JSON body containing at
least `{status, version, transport}`. `/health` is the container `HEALTHCHECK` target and the
router liveness probe. A server that also serves a REST API MAY additionally expose
`/api/health` as an alias, but `/health` is the canonical contract.

**Router profile.** The router's `GET /health` returns its existing **aggregate** shape
(`{status, service, backends:{total, enabled, namespaces, reachable}}`, see
`observability.py:register_health`) — it reports federation/back-end reachability rather than a
single `{version, transport}`. `status` is the field shared by both profiles; the aggregate
shape is normative for the router and the `{status, version, transport}` shape is normative for
backends. The probe asserts each against its own profile.

## 6. Application-Session pattern (normative for stateful features)

Servers that need stateful, multi-call workflows (the pubtator-link research-session class)
MUST implement them at the **application/service layer**, not via transport sessions:

- **Persistence.** Session state MUST live in a durable store (DB), not in process memory,
  so any replica can serve any request.
- **Addressing.** A session MUST be addressed by an explicit `session_id` **tool argument**
  (and returned by the create/stage tool). It MUST NOT depend on `Mcp-Session-Id`.
- **Lifecycle.** Provide explicit `create/stage → get/status → list → (expire/delete)` tools;
  document TTL/expiry and idempotency of staging.
- **Isolation.** Session reads/writes MUST be scoped to their `session_id` (and any tenant key).
- **Reference implementation.** `pubtator-link`'s `ResearchSessionService` + the
  `stage_research_session` / `get_research_session_status` / `list_research_sessions` tools.

This pattern is the supported way to "expand session stuff" and composes with stateless transport.

## 7. Escape hatch — transport statefulness (rare, opt-in)

A server MAY use the **stateful tier** (FastMCP default: `Mcp-Session-Id` + SSE) **only** when
it genuinely requires MCP **sampling**, **elicitation**, **server-initiated requests/notifications**,
or **progress streaming** for long-running tools. Such a server:
- MUST document the specific feature and justification in its README and capabilities;
- MUST still serve the single `/mcp` endpoint with no trailing-slash redirect, the serverInfo
  and `/health` contracts above;
- SHOULD localize statefulness to the tools that need it; and
- is exempt from the "no `Mcp-Session-Id`" rule in §3.2 only.
Default for every server is the stateless tier. No current fleet tool qualifies.

## 8. Conformance — Definition of Done + automated probe

A shared **conformance probe** (a pytest module reusable across repos, plus a router-side
`make conformance` check against a live URL) runs in **two profiles** — a **backend profile**
(unauthenticated, one per `-link` server) and a **router profile** (the federation edge, which
may enforce auth and has its own identity/health shape).

**Backend profile** asserts, against an unauthenticated server:
- `POST /mcp` `initialize` (no trailing slash) → `200`, `Content-Type: application/json`,
  **no** `Location`/redirect and **no** `Mcp-Session-Id` (stateless tier);
- `GET /mcp` does **not** `307`-redirect (a stateless-tier `405` is acceptable);
- `tools/list` returns ≥ 1 tool; `serverInfo.name` matches `^[a-z0-9]+(-[a-z0-9]+)*-link$`;
- after a **successful** `initialize`, a follow-up `tools/list` carrying an **unsupported**
  `MCP-Protocol-Version` header → `400`. (The version header governs *subsequent* requests, not
  `initialize` itself — `initialize` negotiates the version in its body, so probing it for `400`
  is wrong.)
- `GET /health` → `200` with `{status, version, transport}`;
- a cross-`Origin` request is rejected (Origin validation present).

**Router profile** asserts the same transport / no-redirect / Origin / protocol-version contract,
but for the edge:
- `serverInfo.name` is exactly `genefoundry` (not `*-link`);
- when auth is enabled, an **unauthenticated** MCP call → `401` with a `WWW-Authenticate`
  challenge (the probe either supplies a token before the transport asserts, or asserts the
  `401` contract directly — see `tests/integration/test_auth_contract.py`);
- `GET /health` → `200` with at least `{status}` and the federation/back-end summary.

Per-repo **DoD checklist**: contract met (correct profile), probe green in CI, README documents
transport mode + (if any) the escape-hatch justification, `servers.yaml`/`.env` URL targets `/mcp`.

## 9. Adoption / rollout

State below is **verified against live `main`** (2026-06-29 sweep of every `-link`
`server_manager.py`). Caveat: several local working copies were 1 commit behind their remote
`main` at sweep time, so a stale checkout or a not-yet-rebuilt container can still show the old
behaviour — conformance is judged against remote `main` and re-verified by the probe, never by a
local checkout.

- **Transport-conformant today** (stateless + JSON + `mount("/")`, no redirect):
  **autopvs1, clingen, clinvar, gnomad, litvar, pubtator, spliceailookup, vep**. Action: add
  the conformance probe; no `server_manager` change. (The 2026-06-29 endpoint-path PRs for the
  first six landed on `main`; pubtator and litvar were already stateless.)
- **Migrate — transport-mode only** (already `mount("/")` with the path baked in; just add
  `stateless_http=True, json_response=True`): **gencc, gtex, hgnc, hpo, mavedb, metadome, mgi,
  mondo, orphanet, panelapp, uniprot**. One small `server_manager` change + the probe, **one PR
  per repo** (the proven 2026-06-29 pattern), CI-gated. Lowest-risk batch. (gtex is the
  mount-pattern reference yet is itself in this batch — its *mount* is canonical, its *mode* is
  not. uniprot's earlier host-port fix is independent of this transport migration.)
- **Migrate — path *and* mode** (still `http_app(path="/")` mounted at `mcp_path` → carries
  **both** the 307 and the stateful default): **genereviews, stringdb**. These get the full
  gtex-pattern fix (bake the path, `mount("/")`) **and** `stateless_http`/`json_response`.
  Flagged by the spec's codex review as missing from the earlier working set; the live sweep
  confirms it.
- **Router** (`genefoundry`): currently `http_app(path="/")` mounted at `GF_MCP_PATH`
  (`server.py:91,127`) → it 307s on `POST /mcp` exactly like the path-split backends, so it is
  **in scope, not exempt** (correcting the earlier "no config change" note). Migrate with the
  same gtex pattern — bake `path=settings.GF_MCP_PATH`, `mount("/")`; `/health`, `/metrics`, and
  the auth well-known routes are already registered before the mount and keep precedence.
  Evaluate `stateless_http`/`json_response` for the proxy edge (it uses no
  sampling/elicitation, so **stateless is the target**, but the composed lifespan + proxy mounts
  must be validated under the plan) and gate on the **router conformance profile** (§8):
  `serverInfo.name=genefoundry`, the aggregate `/health`, and the `401` auth contract.
- **serverInfo canonicalization** (autopvs1, genereviews, stringdb → `<ns>-link`): verify via
  the §8 regex check rather than asserting it landed — the probe is the gate.
- **Health-path alignment:** standardize `/health`; add `/api/health` alias only where a REST
  API already exposes it.
- **Sequencing:** publish `docs/MCP-TRANSPORT-STANDARD-v1.md` + the probe first; then migrate in
  batches — the transport-mode-only set first (lowest risk), then the two path+mode repos
  (genereviews, stringdb), then the router — with the probe gating each PR; close with a
  fleet-wide probe sweep.

## 10. Risks & mitigations

- **A tool actually needs streaming/sampling.** → The escape hatch (§7) keeps a first-class,
  documented path; audit tools during migration before flipping a server to stateless.
- **Client expecting SSE.** Spec requires clients to support JSON responses; stateless+JSON is
  spec-compliant and removes SSE/redirect edge cases (historically a source of connector bugs).
- **Behavioural side effect of root-mount.** Mounting MCP at `/` makes unmatched paths 404 via
  the MCP app (e.g. wrong-method on a GET-only route → 404 not 405). Acceptable and uniform
  across the fleet; called out in the conformance notes.

## 11. References

- MCP Streamable-HTTP transport spec — https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- FastMCP HTTP deployment (`stateless_http`, `json_response`, session manager) — https://gofastmcp.com/deployment/http
- Fleet validation memory: `fleet-docker-mcp-validation` (2026-06-29).
