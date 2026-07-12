# Fleet Post-Remediation Hardening — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. One
> subagent per repo. Each subagent reads: (a) this plan's **Shared Primitives** section, (b) its
> **repo task card**, (c) the design spec `docs/superpowers/specs/2026-07-12-fleet-post-remediation-hardening-design.md`,
> and (d) its GitHub issue body. Steps use `- [ ]` for tracking.

**Goal:** Close findings F-01…F-22 across the router + 21 `-link` backends + the security-profile
repo as one PR per repo — TDD-tested, Codex-gated, FF-merged — then locally build/run each backend
docker and manually exercise it via MCP in Codex and Claude.

**Architecture:** Six shared recipes (spec §4) applied per-repo; each repo gets a branch
`security/2026-07-12-post-remediation-hardening` off pristine `origin/main`. Redirect hardening uses
the httpx **request event-hook** form (spec §4 Recipe B), never disable-+-manual-loop.

**Tech Stack:** Python 3.12+, uv, FastMCP 3.x, httpx, pytest, ruff, mypy, Docker, GitHub Actions,
`gh`, `codex exec`.

## Global Constraints (apply to EVERY task)

- Branch off **pristine `origin/main`** only: `git fetch origin && git switch -c security/2026-07-12-post-remediation-hardening origin/main`. STOP + report if the branch already exists or main has unseen commits.
- **TDD**: write failing adversarial test → see it fail → minimal implementation → `make ci-local` GREEN. One logical change per commit.
- **600-LOC per module** budget (`make lint-loc`). Keep helpers small; split if a module would exceed.
- `make ci-local` must be GREEN before the repo is considered done (format-check, ruff, lint-loc, mypy, unit + integration tests).
- **No force-push. No deploy.** Do NOT push or open PRs from the subagent — leave the green branch + commits local; the orchestrator handles Codex gating + merge.
- **No token passthrough**; backends stay unpublished; Streamable-HTTP only. Research-use-only disclaimers preserved.
- **Redirect allowlists are derived from the configured base URL host(s) at client-build time — never hardcoded.** Byte caps **fail closed (raise), never truncate.**
- Do NOT touch `.github/workflows/container-security.yml` (already present fleet-wide).
- Secret-scanning is a **repo setting** (operator follow-up) — PR only documents it; never block on it.
- Router drift re-pin (F-11/F-12/F-20) is **post-deploy** — do NOT block a PR on it.

---

## Shared Primitives (read once; referenced by repo task cards)

### P-A — F-19 uv digest-pinned COPY

Replace the floating installer bootstrap. In `docker/Dockerfile`, delete the
`RUN pip install --upgrade pip uv && …` line (keep any non-uv steps that shared the RUN) and add,
in the builder stage before `uv sync`:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv
```

**genereviews special-case:** builder L28 `pip install --upgrade "pip>=26.1" uv` → uv COPY + drop uv
from pip; runtime L65 `python -m pip install --upgrade "pip>=26.1"` → pin exact (`pip==26.1`) or drop
the upgrade.

**Regression test** (`tests/test_dockerfile_bootstrap.py` or add to an existing build-hardening test):

```python
from pathlib import Path

def test_dockerfile_pins_uv_and_has_no_floating_pip_upgrade():
    text = Path("docker/Dockerfile").read_text()
    assert "pip install --upgrade" not in text, "floating pip/uv upgrade must be removed"
    assert "ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab" in text
```

### P-B — Redirect + response-cap event-hook (spec §4 Recipe B)

Add a small module `<pkg>/api/url_guard.py` (name per repo convention). Core:

```python
from urllib.parse import urlsplit
import httpx

class DisallowedURLError(Exception):
    """Raised when an outbound request/redirect targets a non-allowlisted URL. NON-RETRYABLE."""

def build_host_allowlist(*base_urls: str) -> frozenset[str]:
    hosts = set()
    for u in base_urls:
        h = urlsplit(u).hostname
        if h:
            hosts.add(h.lower())
    return frozenset(hosts)

def make_url_guard(allowed_hosts: frozenset[str]):
    async def _guard(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https":
            raise DisallowedURLError(f"non-https scheme: {url.scheme}")
        if url.username or url.password:
            raise DisallowedURLError("userinfo not permitted")
        host = (url.host or "").lower()
        if host not in allowed_hosts:
            raise DisallowedURLError(f"host not allowlisted: {host}")
    return _guard
```

Wire into the existing `httpx.AsyncClient(...)`: keep `follow_redirects=True`, add
`event_hooks={"request": [make_url_guard(ALLOWED)]}`, `max_redirects=5`. `ALLOWED` comes from
`build_host_allowlist(<configured base url(s)>, *extra_redirect_targets)`.

**Byte cap** — replace the buffered `.json()/.text/.content` read with a capped streamed read:

```python
MAX_RESPONSE_BYTES = ...  # per-repo, from spec §4 table
async def _read_capped(client, method, url, *, max_bytes, **kw):
    async with client.stream(method, url, **kw) as resp:
        resp.raise_for_status()
        chunks, total = [], 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise DisallowedURLError(f"response exceeded {max_bytes} bytes")  # or a ResponseTooLargeError
            chunks.append(chunk)
        return b"".join(chunks)
```

**Guard-exception classification:** `DisallowedURLError` must NOT subclass the client's retryable
exception types (e.g. `httpx.TimeoutException`/`TransportError`); map it in the envelope layer to a
fixed non-retryable error code. Verify the repo's retry loop does not swallow+retry it.

**Adversarial tests** (respx/MockTransport): (1) redirect to a non-allowlisted host → raises;
(2) redirect to `http://` (downgrade) → raises; (3) `https://user:pass@allowed-host` → raises;
(4) response body over the cap → raises; (5) happy-path 200 on the allowlisted host → unchanged.

### P-C — Bound-input + fixed-enum-error (spec §4 Recipe C)

Reuse the repo's existing sanitizer/envelope (autopvs1 `mcp/untrusted_content.py` +
`mcp/error_guard.py`; mavedb `mcp/envelope.py`). Validate length + list-size + a conservative
grammar BEFORE any I/O/cache; on invalid return the fixed `invalid_input` envelope; never log raw
input or exception prose (log error class/code only).

### P-D — CI workflows (spec §4 Recipe D)

For the 6 CodeQL-absent repos (gencc, hpo, mavedb, orphanet, panelapp, spliceailookup): copy a
`security.yml` (CodeQL) template from a repo that already has one (e.g. `gnomad-link` or `clinvar-link`),
SHA-pin every action, least-privilege `permissions:`, PR trigger; add `dependency-review.yml`
(SHA-pinned, `fail-on-severity: high`). Validate with actionlint + a YAML-parse test.

Secret-scanning group (autopvs1, genereviews, litvar, pubtator, gtex, stringdb, gnomad): document
the required setting in `SECURITY.md`/README (operator runs the `gh api` PATCH). No workflow change.

### P-E — Authenticity anchor + bomb guard (spec §4 Recipe E)

Committed digest (constant/manifest) compared before decompression; expanded-size ceiling via bounded
streaming decompress; atomic write (`os.replace`); fail-closed. Tests: mismatch, truncation, bomb.

### P-F — MCP annotation/schema completion (spec §4 Recipe F)

Land the annotation/schema. Verify each hint against the real side effect. Router re-pin deferred.

---

## Repo Task Cards

Each card = one PR. All follow the Global Constraints + reference the cited primitives.

### Router (#47) — F-01, F-21 — Tier H

**Files:** `.github/workflows/fleet-probe.yml` (fix L58), new `tests/test_workflows_parse.py`, CI
`Makefile`/`ci.yml` (add actionlint), `genefoundry_router/cli.py` + `config.py` (F-21), `SECURITY.md`/docs.

- [ ] **F-01 fix:** change `fleet-probe.yml:58` from `run: echo "::warning::fleet-probe: …"` to a block scalar (`run: |` + indented echo) or fully single-quote the command so YAML parses.
- [ ] **F-01 regression test** `tests/test_workflows_parse.py`:
```python
from pathlib import Path
import yaml
def test_all_workflows_parse():
    for f in Path(".github/workflows").glob("*.yml"):
        yaml.safe_load(f.read_text())  # must not raise
```
  Run it against the pre-fix file first to see it FAIL, then fix, then PASS.
- [ ] **actionlint:** add `actionlint` to `make ci-local` (download SHA-pinned binary in CI or `uvx`/pre-commit); ensure it runs over `.github/workflows/*.yml`.
- [ ] **F-21:** in `cli.py`, graduate `should_warn_no_rate_limit` from warn to **fail-closed** for an authenticated non-loopback bind: refuse startup unless `GF_RATE_LIMIT_RPM > 0`, mirroring `is_insecure_public_bind`; require `GF_METRICS_TOKEN` when `/metrics` is exposed on a non-loopback bind. `GF_ALLOW_INSECURE=true` remains the documented dev override. Tests: (a) auth non-loopback + rpm=0 + no override → exits nonzero; (b) + `GF_ALLOW_INSECURE=true` → warns, starts; (c) loopback → starts; (d) non-loopback + no metrics token → refuses `/metrics` exposure or fails closed per chosen semantics.
- [ ] Document secure defaults + dev overrides in `SECURITY.md`. `make ci-local` GREEN.

### hpo-link (#17) — F-02, F-18(add CodeQL), F-19 — Tier H

- [ ] **F-02:** in `build-data.yml`, validate the upstream `tag_name` against a strict closed grammar (e.g. `^[A-Za-z0-9._-]{1,64}$` / date form) BEFORE writing `GITHUB_OUTPUT`; pass via `env:` (not `${{ }}` inside `run:`); quote every use; SHA-pin all actions. Hostile-tag regression test (a workflow-lint/unit test asserting an injected `; rm -rf` tag is rejected). Downloader validation in `hpo_link/ingest/downloader.py`.
- [ ] **F-18:** add SHA-pinned `security.yml` (CodeQL) + `dependency-review.yml` (P-D).
- [ ] **F-19:** P-A (Dockerfile L28). `make ci-local` GREEN + actionlint.

### autopvs1-link (#61) — F-03, F-18(setting), F-19 — Tier H

- [ ] **F-03:** apply P-C to the legacy REST routes `api/routes/variant.py` (L66,129,155-170) and `api/routes/gene.py` (L93,105-106): bound/validate variant/HGVS/gene identifiers before I/O; return fixed caller-safe errors; log error class/code only — reuse `mcp/untrusted_content.py`+`mcp/error_guard.py`. Tests assert genomic ids, upstream bodies, exception prose never reach logs/responses; oversize/hostile ids rejected pre-call.
- [ ] **F-18:** document secret-scanning setting (operator). **F-19:** P-A (L26). `make ci-local` GREEN.

### metadome-link (#9) — F-04, F-10, F-11, F-19 — Tier H

- [ ] **F-04:** `config.py:100-112` default bind → `127.0.0.1`; non-loopback requires an explicit, loudly-logged override. Test: default is loopback; public bind without override refused/warned loudly.
- [ ] **F-10:** P-B on `api/client.py` (allowlist from `config.metadome.base_url` → `stuart.radboudumc.nl`; cap **≥64 MB** or exempt JSON path; guard exception non-retryable re: retry loop at `client.py:163`).
- [ ] **F-11:** P-F — `mcp/tools/landscape.py` `request_tolerance_landscape` → `readOnlyHint=false, destructiveHint=false, idempotentHint=true` (verified: dedupes by `transcript_id`). Note router re-pin deferred.
- [ ] **F-19:** P-A (L32). `make ci-local` GREEN.

### genereviews-link (#92) — F-05, F-06, F-13, F-18(setting), F-19 — Tier H

- [ ] **F-05:** `corpus/archive.py` — replace `timeout=None` with `httpx.Timeout(connect=30,read=60,write=30,pool=30)`; compressed + expanded byte ceilings (NCBI tarball ~613 MB → cap ~4 GiB, fail-closed on streamed read); member-count limit; bounded per-worker memory in `corpus/parallel.py` (don't read whole compressed members into RAM).
- [ ] **F-06:** P-B on the download client `ingest/github_release.py` — allowlist `github.com` + **`release-assets.githubusercontent.com`** (+ defensive `objects.githubusercontent.com`, `github-releases.githubusercontent.com`); `api.github.com` for the resolve client; per-read `httpx.Timeout` (not total); caps bundle 2 GiB / sha256 1 MiB. Anchor bundle authenticity in a **committed digest** (config/repo constant), compared post-download; NOT the same-host `.sha256`. NCBI clients keep `follow_redirects=False`.
- [ ] **F-13:** validate `--schema` against a strict PostgreSQL identifier grammar; safe identifier quoting for dynamic SQL.
- [ ] **F-18:** document secret-scanning. **F-19:** P-A (L28 + L65). `make ci-local` GREEN.

### litvar-link (#49) — F-07, F-12, F-18(setting), F-19 — Tier H

- [ ] **F-07:** P-B on `api/client.py` (allowlist from `config.base_url` → `www.ncbi.nlm.nih.gov`; cap ~25 MB configurable; guard non-retryable re: `except Exception` at `client.py:144`).
- [ ] **F-12:** P-F — add shared read-only/non-destructive `ToolAnnotations` to all 6 tools; add `output_schema` to `gene.py`, `literature.py`, `metadata.py`, `rsid.py`. Router re-pin deferred.
- [ ] **F-18:** document secret-scanning. **F-19:** P-A (L26). `make ci-local` GREEN.

### uniprot-link (#16) — F-08, F-17, F-19 — Tier H (one PR)

- [ ] **F-08:** in `services/queries/validation.py:160-172` clamp explicit SELECT LIMIT structurally (don't be fooled by LIMIT-like comment/literal text); reject or strictly bound CONSTRUCT/DESCRIBE. Tests: huge explicit LIMIT and LIMIT-in-comment/literal cannot bypass; graph-returning forms bounded/rejected.
- [ ] **F-17 (shared cap):** P-B on `api/client.py` (allowlist from `config.base_url` → `sparql.uniprot.org`; SPARQL is POST; byte cap **~32 MiB, ABOVE the 8 MiB text fence, error-on-exceed never truncate**).
- [ ] **F-19:** P-A (L26). `make ci-local` GREEN.

### mavedb-link (#19) — F-09, F-18(add CodeQL), F-19 — Tier M

- [ ] **F-09:** P-C in `mcp/tools/resolvers.py:169-188` — bound `get_hgvs_validation.variant` length/list/HGVS grammar before forward/cache/echo; fixed caller errors via `mcp/envelope.py`; only validated identifiers in structured fields.
- [ ] **F-18:** add SHA-pinned CodeQL + dependency-review (P-D). **F-19:** P-A (L26). `make ci-local` GREEN.

### pubtator-link (#110) — F-14, F-15, F-18(setting), F-19 — Tier M

- [ ] **F-14:** `docker/docker-compose.prod.yml` requires the DB secret with NO fallback (remove predictable `pubtator_link` default in the prod path); document rotation. Test/check: prod compose config fails when secret absent.
- [ ] **F-15:** digest-pin `pgvector/pgvector:0.8.4-pg18-trixie` (and every prod image) by `@sha256:`; add a regression check enumerating prod images require a digest.
- [ ] **F-18:** document secret-scanning. **F-19:** P-A (L34). `make ci-local` GREEN.

### clingen-link (#35) — F-16, F-19 — Tier M

- [ ] **F-16:** P-E in `store/db.py:92` — verify committed SHA-256 of the shipped `.zst` before decompress; expanded-size ceiling; atomic write. Tests: checksum mismatch/truncation/decompression-bomb fail closed.
- [ ] **F-19:** P-A (L29). `make ci-local` GREEN.

### panelapp-link (#13) — F-17, F-18(add CodeQL), F-19 — Tier M

- [ ] **F-17:** P-B on `api/client.py` (allowlist from `config` → `panelapp.genomicsengland.co.uk`, `panelapp-aus.org`); validate the DRF `next` URL **inside `_list_paginated`** same-origin, **normalize scheme→https (don't reject)**; caps pages≤100, rows≤100k, bytes 50 MB — all fail-loud (raise `DownloadError`). Verify a live `next` host before shipping exact-host reject.
- [ ] **F-18:** add CodeQL + dependency-review (P-D). **F-19:** P-A (L26). `make ci-local` GREEN.

### gtex-link (#62) — F-17, F-18(setting), F-19 — Tier M

- [ ] **F-17:** P-B on `api/client.py:168-174` (allowlist `gtexportal.org`; cap 16 MB, never 2 MB; GET-only; the 307→http downgrade is correctly rejected by https-only).
- [ ] **F-18:** document secret-scanning. **F-19:** P-A (L26). `make ci-local` GREEN.

### spliceailookup-link (#15) — F-17, F-18(add CodeQL), F-19 — Tier M

- [ ] **F-17:** P-B on `api/base_client.py:79-85` (allowlist derived from resolved config: the 4 Cloud Run hosts `spliceai-37/38-…a.run.app`, `pangolin-37/38-…a.run.app` + `rest.ensembl.org` + `grch37.rest.ensembl.org` — NOT broadinstitute.org; cap 16 MB on BYTES; keep `httpx.Timeout(90)` + soft deadlines untouched).
- [ ] **F-18:** add CodeQL + dependency-review (P-D). **F-19:** P-A (L26). `make ci-local` GREEN.

### stringdb-link (#22) — F-17, F-18(setting), F-19 — Tier M

- [ ] **F-17:** P-B on `api/client.py:153-169,268-278` — allowlist MUST include `version-12-0.string-db.org` (derive from `config.base_url`) + `string-db.org`; POST API → event-hook (not manual loop); cap 32 MiB; stream-refactor `_make_request` + `get_network_image`.
- [ ] **F-18:** document secret-scanning. **F-19:** P-A (L34). `make ci-local` GREEN.

### vep-link (#14) — F-17, F-20, F-19 — Tier M

- [ ] **F-17:** P-B on `api/base_client.py:73-83,126-147` — allowlist derived from `VEP_GRCH38_URL`+`VEP_GRCH37_URL` (`rest.ensembl.org`, `grch37.rest.ensembl.org`); cap ~50 MB/chunk on DECODED bytes; keep 429/Retry-After + `_post_chunked` intact; cap-exceed maps non-retryable.
- [ ] **F-20:** P-F — add `destructiveHint=false` to the shared read-only annotation (`mcp/annotations.py:12-16`); all tools expose the complete annotation. Router re-pin deferred.
- [ ] **F-19:** P-A (L28). `make ci-local` GREEN.

### gencc-link (#28) — F-18(add CodeQL), F-19 — Tier B

- [ ] **F-18:** add SHA-pinned CodeQL + dependency-review (P-D). **F-19:** P-A (L26). `make ci-local` GREEN + actionlint.

### orphanet-link (#13) — F-18(add CodeQL), F-19 — Tier B

- [ ] **F-18:** add CodeQL + dependency-review (P-D; container scan/SBOM already present). **F-19:** P-A (L28). `make ci-local` GREEN.

### gnomad-link (#36) — F-18(setting), F-19 — Tier B

- [ ] **F-18:** document secret-scanning setting (operator). **F-19:** P-A (L28). `make ci-local` GREEN.

### clinvar-link (#18) — F-19 — Tier B

- [ ] **F-19:** P-A (L34) + regression test. `make ci-local` GREEN.

### hgnc-link (#15) — F-19 — Tier B

- [ ] **F-19:** P-A (L28) + regression test. `make ci-local` GREEN.

### mgi-link (#15) — F-19 — Tier B

- [ ] **F-19:** P-A (L26) + regression test. `make ci-local` GREEN.

### mondo-link (#14) — F-19 — Tier B

- [ ] **F-19:** P-A (L28) + regression test. `make ci-local` GREEN.

### genefoundry-mcp-security-profile (#1) — F-22 — Tier R — **LAST**

- [ ] After the router PR is merged, bump the `genefoundry-router` submodule gitlink to the **final router main SHA**; align the report revision table + README supersession notice + gitlink to that SHA. Verify `git submodule update --init --recursive` reproduces the audited source. No unrelated changes.

---

## Phase 3 — Codex adversarial gate (per branch, before merge)

For each repo's green branch:
```bash
codex exec -s read-only -m gpt-5.5 -c model_reasoning_effort=xhigh -C /home/bernt-popp/development/<repo> \
  "Adversarially verify branch security/2026-07-12-post-remediation-hardening closes its findings \
   (<list F-nn>) with no reachable bypass/leak/regression. Check the shared-recipe surfaces \
   (redirect hops incl. the configured base host + any CDN/versioned redirect target, byte-cap \
   fail-closed sizing, fixed-error/no-prose-leak, uv digest pin, CI least-privilege). Return SHIP or \
   FIX with specifics." < /dev/null
```
Merge bar = findings genuinely closed, no reachable bypass, `make ci-local` green. On FIX: remediate,
re-run ci-local, re-gate. Never merge on an unresolved FIX.

## Phase 4 — Merge order

router (#47) → all backends → security-profile (#1) LAST (submodule → final router SHA). FF-merge to
`main` on green + Codex-SHIP; close each issue. No force-push.

## Phase 5 — Local docker manual test in Codex + Claude (per changed backend)

For each `-link` repo (after merge):
1. `make docker-build` (or `docker build -f docker/Dockerfile`), then run the container exposing `/mcp`
   on a local port (loopback), using the repo's compose/dev instructions.
2. **MCP smoke (Claude side):** issue a real biomedical question via JSON-RPC `tools/call` over
   `POST /mcp` (curl) and validate a sensible, cited answer. Each repo gets a canonical question
   (e.g. gtex: "median TP53 expression across tissues"; litvar: "variants for BRCA1"; genereviews:
   "search NF1"; vep: "consequence of a known HGVS"; uniprot: "protein for gene TP53"; etc.).
3. **MCP smoke (Codex side):** `codex exec` driving the same MCP call against the container as an
   independent cross-check.
4. Record pass/fail per repo. A repo is DONE only when its docker serves `/mcp`, tools list, and the
   canonical question returns a valid answer in BOTH clients.
5. `docker stop`/cleanup. Note the deferred router drift re-pin + secret-scanning settings as operator
   follow-ups.

---

## Self-Review

- **Spec coverage:** F-01…F-22 each map to a repo task card ✓ (router F-01/F-21; F-18 split add/setting per §3; F-19 all 21; F-22 last).
- **No placeholders:** recipe code is in Shared Primitives; per-repo caps/hosts are concrete (§4 table).
- **Type consistency:** `DisallowedURLError`, `build_host_allowlist`, `make_url_guard`, `_read_capped` names used consistently; adapt module names to repo convention.
