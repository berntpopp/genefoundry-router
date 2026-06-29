# MCP Transport & Session Standard v1

- **Status:** NORMATIVE
- **Date:** 2026-06-29
- **Scope:** All GeneFoundry `-link` MCP servers and the `genefoundry` router.
- **Companion standards:**
  [Tool-Naming v1](TOOL-NAMING-STANDARD-v1.md),
  [Response-Envelope v1](RESPONSE-ENVELOPE-STANDARD-v1.md),
  [Container-Hardening v1](CONTAINER-HARDENING-STANDARD-v1.md), Logging & CLI v1.
- **Design spec (non-normative background):**
  `docs/specs/2026-06-29-mcp-transport-and-session-standard-design.md`
- **Conformance probe:** [`docs/conformance/conformance.py`](conformance/conformance.py)

Keywords MUST / SHOULD / MAY per [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119).

---

## 1. Context

A 2026-06-29 fleet-wide docker + MCP validation sweep confirmed all 21 `-link` servers build,
run, and serve MCP, but surfaced transport-surface drift:

- **Seven servers** 307-redirected `POST /mcp` (fragile for strict HTTP clients).
- **Thirteen servers** ran FastMCP's stateful + SSE default with no application need for
  statefulness; two carried both problems simultaneously.
- **serverInfo.name** values were free-form across the fleet.
- **/health** vs `/api/health` was inconsistent.

The pivotal finding: MCP transport sessions (`Mcp-Session-Id`) are a connection-layer facility
for sampling/elicitation/SSE streaming — none of the current fleet tools use these features.
Application sessions (e.g. pubtator-link research sessions) live in Postgres and are addressed
by a `session_id` tool argument; they are fully compatible with, and already implemented on, a
stateless MCP transport. This standard codifies the uniform contract.

---

## 2. Transport contract — §3 (normative)

### 2.1 Single endpoint, no redirect

Each server MUST expose the MCP endpoint at **`/mcp`**.

- `POST /mcp` (no trailing slash) MUST return `200` directly and MUST NOT `307`-redirect.
- `GET /mcp` MUST likewise not redirect. In the stateless tier a server MAY answer
  `405 Method Not Allowed`; the escape-hatch tier (§7) answers `GET /mcp` with an SSE stream.

**Implementation pattern** — bake the MCP path in and mount at root:

```python
mcp_app = mcp.http_app(path=settings.mcp_path)   # e.g. path="/mcp"
app.mount("/", mcp_app)                            # NOT app.mount("/mcp", http_app(path="/"))
```

The host app's own routes (`/health`, `/api/…`, and — on the router — auth well-known routes)
MUST be registered before the mount and keep precedence. Reference: `gtex_link/server_manager.py`.

### 2.2 Stateless + JSON

Servers MUST construct the MCP app with `stateless_http=True` and `json_response=True`.

A server MUST NOT assign an `Mcp-Session-Id` at the transport layer in the default (stateless)
tier.

### 2.3 Protocol version

Servers MUST negotiate `protocolVersion` in `initialize` and MUST honor the
`MCP-Protocol-Version` request header on subsequent requests.

- MUST return `400 Bad Request` for an unsupported version.
- SHOULD assume `2025-03-26` when the header is absent (spec backwards-compat).
- SHOULD accept current spec revisions (`2025-03-26`, `2025-06-18`, and later) by
  echo/negotiation.

### 2.4 Accept handling

Clients send `Accept: application/json, text/event-stream`. Servers MUST tolerate this and, in
the stateless tier, MUST answer `Content-Type: application/json`.

### 2.5 Security

Servers MUST validate the `Origin` header (DNS-rebinding protection). In containers, binding
`0.0.0.0` is permitted because exposure is mediated by the reverse proxy; this MUST be
documented (ties to Container-Hardening v1). The router MUST NOT forward the caller's
`Authorization` header to backends.

---

## 3. serverInfo contract — §4 (normative)

`serverInfo.name` MUST be `"<namespace>-link"` (lowercase, hyphenated; e.g. `"gtex-link"`,
`"autopvs1-link"`). `serverInfo.version` MUST be the package's semantic version. Free-form
display names (e.g. `"GeneReview Link Tool"`, `"StringDB-Link Server"`) are prohibited.

**Router exception.** The router's `serverInfo.name` MUST be `"genefoundry"` (the federation's
published identity at the edge), not `genefoundry-link`. The `<namespace>-link` rule applies
to backends only. The conformance probe enforces the two cases under separate profiles (§8).

---

## 4. Health & ops — §5 (normative)

Each **backend** MUST expose **`GET /health`** returning `200` with a JSON body containing at
least:

```json
{ "status": "ok", "version": "1.2.3", "transport": "stateless-http" }
```

`/health` is the container `HEALTHCHECK` target and the router liveness probe. A server that
also serves a REST API MAY additionally expose `/api/health` as an alias, but `/health` is the
canonical contract.

**Router profile.** The router's `GET /health` returns its existing aggregate shape:

```json
{
  "status": "ok",
  "service": "genefoundry-router",
  "backends": { "total": 21, "enabled": 21, "namespaces": [...], "reachable": 21 }
}
```

`status` is the field shared by both profiles. The aggregate shape is normative for the router;
`{status, version, transport}` is normative for backends. The probe asserts each against its own
profile.

---

## 5. Application-Session pattern — §6 (normative for stateful features)

Servers that need stateful, multi-call workflows (the pubtator-link research-session class)
MUST implement them at the **application/service layer**, not via transport sessions:

- **Persistence.** Session state MUST live in a durable store (DB), not in process memory, so
  any replica can serve any request.
- **Addressing.** A session MUST be addressed by an explicit `session_id` **tool argument**
  (and returned by the create/stage tool). It MUST NOT depend on `Mcp-Session-Id`.
- **Lifecycle.** Provide explicit `create/stage → get/status → list → (expire/delete)` tools;
  document TTL/expiry and idempotency of staging.
- **Isolation.** Session reads/writes MUST be scoped to their `session_id` (and any tenant key).
- **Reference implementation.** `pubtator-link`'s `ResearchSessionService` and the
  `stage_research_session` / `get_research_session_status` / `list_research_sessions` tools.

This pattern is the supported way to provide session features and composes with stateless transport.

---

## 6. Escape hatch — §7 (rare, opt-in)

A server MAY use the **stateful tier** (FastMCP default: `Mcp-Session-Id` + SSE) **only** when
it genuinely requires MCP **sampling**, **elicitation**, **server-initiated
requests/notifications**, or **progress streaming** for long-running tools. Such a server:

- MUST document the specific feature and justification in its README and capabilities;
- MUST still serve the single `/mcp` endpoint with no trailing-slash redirect, the serverInfo
  and `/health` contracts above;
- SHOULD localize statefulness to the tools that need it; and
- is exempt from the "no `Mcp-Session-Id`" rule in §2.2 only.

**Default for every server is the stateless tier. No current fleet tool qualifies.**

---

## 7. Conformance — §8 (normative)

A shared conformance probe (`docs/conformance/conformance.py`, vendored into each repo's
`tests/conformance/`) runs in **two profiles**.

### 7.1 Backend profile

Asserts against an unauthenticated server:

| Check | MUST / SHOULD |
|-------|--------------|
| `POST /mcp` initialize (no trailing slash) → `200` | MUST |
| `Content-Type: application/json` on initialize response | MUST |
| No `Location`/redirect on `POST /mcp` | MUST |
| No `Mcp-Session-Id` header (stateless tier) | MUST |
| `GET /mcp` does not `307`-redirect | MUST |
| `tools/list` returns ≥ 1 tool | MUST |
| `serverInfo.name` equals the expected `<ns>-link` name (exact match; server MUST conform to `^[a-z0-9]+(-[a-z0-9]+)*-link$`) | MUST |
| Follow-up request with unsupported `MCP-Protocol-Version` → `400` | MUST |
| `GET /health` → `200` with `{status, version, transport}` | MUST |
| Cross-`Origin` request rejected | MUST |

### 7.2 Router profile

Same transport / no-redirect / protocol-version contract, plus:

| Check | MUST / SHOULD |
|-------|--------------|
| `serverInfo.name` is exactly `"genefoundry"` | MUST |
| Unauthenticated MCP call → `401` + `WWW-Authenticate` (when auth enabled) | MUST |
| `GET /health` → `200` with at least `{status}` + federation/back-end summary | MUST |

### 7.3 Probe command

```bash
# backend
python -m genefoundry_router.conformance http://127.0.0.1:8000 \
    --name gtex-link --tier stateless

# router (with auth)
python -m genefoundry_router.conformance http://127.0.0.1:8000 \
    --name genefoundry --tier stateless --require-auth

# via make (router repo)
make conformance MCP_URL=http://127.0.0.1:8000 NAME=gtex-link TIER=stateless
```

Exit codes: `0` = conformant, `1` = non-conformant, `2` = transport error.

### 7.4 Per-repo Definition of Done checklist

- [ ] Contract met (correct profile — backend or router)
- [ ] Probe green in CI (`conformance.yml` workflow present and passing)
- [ ] README documents transport mode and (if escape hatch used) justification
- [ ] `servers.yaml`/`.env` URL targets `/mcp` (no trailing slash)
- [ ] `/health` returns `{status, version, transport}` (backend) or aggregate (router)
- [ ] Vendored files present in `tests/conformance/`: `conformance.py` (byte-identical copy), `__init__.py` (empty, required for relative import `from .conformance import run_probe`), `test_transport_v1.py`

---

## 8. Adoption table — §9

**Status: ADOPTED fleet-wide (2026-06-29).** All 21 `-link` backends and the `genefoundry`
router merged the migration; each backend's merged `main` passed the live conformance probe in
CI (the `conformance.yml` gate builds + runs the container and probes it), and the router passed
its in-process router-profile probe. The probe now gates every future PR against regressions.

Migration buckets (verified against live `main`, 2026-06-29):

| Repo | Migration | Status |
|------|-----------|--------|
| autopvs1-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| clingen-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| clinvar-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| gnomad-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| litvar-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| pubtator-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| spliceailookup-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| vep-link | Probe only (already stateless+JSON) | adopted (2026-06-29) |
| gencc-link | Transport mode only (`stateless_http=True, json_response=True`) | adopted (2026-06-29) |
| gtex-link | Transport mode only | adopted (2026-06-29) |
| hgnc-link | Transport mode only | adopted (2026-06-29) |
| hpo-link | Transport mode only | adopted (2026-06-29) |
| mavedb-link | Transport mode only | adopted (2026-06-29) |
| metadome-link | Transport mode only | adopted (2026-06-29) |
| mgi-link | Transport mode only | adopted (2026-06-29) |
| mondo-link | Transport mode only | adopted (2026-06-29) |
| orphanet-link | Transport mode only | adopted (2026-06-29) |
| panelapp-link | Transport mode only | adopted (2026-06-29) |
| uniprot-link | Transport mode only | adopted (2026-06-29) |
| genereviews-link | Path + mode (full gtex-pattern fix) | adopted (2026-06-29) |
| stringdb-link | Path + mode (full gtex-pattern fix) | adopted (2026-06-29) |
| genefoundry (router) | Path + mode + router profile | adopted (2026-06-29) |

**Sequencing:** publish this doc + probe → migrate transport-mode-only batch (lowest risk) →
migrate path+mode repos (genereviews, stringdb) → migrate router → fleet-wide probe sweep.

---

## 9. References

- MCP Streamable-HTTP transport spec — https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
- FastMCP HTTP deployment (`stateless_http`, `json_response`, session manager) — https://gofastmcp.com/deployment/http
- Fleet validation memory: `fleet-docker-mcp-validation` (2026-06-29)
- Design spec: `docs/specs/2026-06-29-mcp-transport-and-session-standard-design.md`
- Conformance probe: [`docs/conformance/conformance.py`](conformance/conformance.py)
