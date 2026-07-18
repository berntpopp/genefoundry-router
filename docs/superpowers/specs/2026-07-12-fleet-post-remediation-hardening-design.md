# Fleet Post-Remediation Hardening — Design (2026-07-12)

- **Status:** DRAFT — awaiting operator approval (Phase 0 gate)
> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

- **Author:** Claude (Superpowers brainstorming)
- **Scope:** `genefoundry-router` + 21 `-link` backends + `genefoundry-mcp-security-profile`
- **Source of truth:** 23 consolidated GitHub issues (findings F-01…F-22), pulled verbatim
- **Boundary:** Research use only; not clinical decision support. Mirror backend disclaimers.
- **Non-goals:** No deployment. No router drift re-pin (post-deploy). No secret-scanning
  repo-setting mutation (operator-owned). No force-push. No unrelated refactoring.

---

## 1. Objective

Close the 22 findings from the 2026-07-12 post-remediation source review across the fleet as
**one PR per repository** (23 PRs), each gated by a Codex `gpt-5.5 xhigh` adversarial review,
merged fast-forward to `main`, with the security-profile submodule bumped **last** to the final
router SHA. Findings are Low/Medium (no Critical/High); none is a request-reachable exploit on a
correctly-deployed (proxied, non-published-backend) fleet. This is defense-in-depth + supply-chain
hardening + one broken-CI fix (F-01).

The work is dominated by **six shared recipes** (§4) applied across many repos. Capturing them once
is the point of this spec: implementers copy the recipe, adapt to each repo's client/Dockerfile
shape, and prove closure with an adversarial test. The integration test is the contract.

---

## 2. Findings → Repo → PR map

One PR per repo. Branch: `security/2026-07-12-post-remediation-hardening` off **pristine
`origin/main`** (fetch first). Tier = the repo's hardest finding (see §5).

| Repo (GitHub slug) | Issue | Findings | Tier | Recipes | One-line scope |
|---|---|---|---|---|---|
| genefoundry-router | #47 | F-01, F-21 | **H** | — | Fix invalid `fleet-probe.yml` YAML + actionlint + YAML-parse regression test; fail-closed prod metrics-token & rate-limit |
| hpo-link | #17 | F-02, F-18, F-19 | **H** | A, D(add) | Validate upstream release tag grammar → `env:` + quote + SHA-pin actions; add CodeQL+dep-review; uv COPY pin |
| autopvs1-link | #61 | F-03, F-18, F-19 | **H** | A, C, D(setting) | Port MCP redaction/fixed-error to REST routes + bound identifiers; secret-scanning setting (op); uv COPY pin |
| metadome-link | #9 | F-04, F-10, F-11, F-19 | **H** | A, B, F | Loopback-default bind + loud public override; redirect/response-cap; `request_tolerance_landscape` annotation; uv COPY pin |
| genereviews-link | #92 | F-05, F-06, F-13, F-18, F-19 | **H** | A, B, D(setting), E | Corpus ingest ceilings; bundle authenticity anchor + bounded download; strict PG identifier grammar; secret-scanning setting (op); uv COPY pin (2 sites) |
| litvar-link | #49 | F-07, F-12, F-18, F-19 | **H** | A, B, D(setting), F | Validated redirects + response cap; complete ToolAnnotations+output_schema; secret-scanning setting (op); uv COPY pin |
| uniprot-link | #16 | F-08, F-17, F-19 | **H** | A, B | SPARQL limit/graph-form policy + streamed response cap (shared with F-17 redirect); uv COPY pin |
| mavedb-link | #19 | F-09, F-18, F-19 | **M** | A, C, D(add) | Bound HGVS input + fixed errors before I/O/cache; add CodeQL+dep-review; uv COPY pin |
| pubtator-link | #110 | F-14, F-15, F-18, F-19 | **M** | A, D(setting), E | Require prod DB secret (no fallback); digest-pin pgvector + all prod images + regression check; secret-scanning setting (op); uv COPY pin |
| clingen-link | #35 | F-16, F-19 | **M** | A, E | Verify committed `.zst` digest + expanded-size ceiling + atomic write before DB use; uv COPY pin |
| panelapp-link | #13 | F-17, F-18, F-19 | **M** | A, B, D(add) | Validate redirect/`next` pagination URLs + cap pages/rows/bytes; add CodeQL+dep-review; uv COPY pin |
| gtex-link | #62 | F-17, F-18, F-19 | **M** | A, B, D(setting) | Redirect/response cap; secret-scanning setting (op); uv COPY pin |
| spliceailookup-link | #15 | F-17, F-18, F-19 | **M** | A, B, D(add) | Redirect/response cap (preserve long prediction timeouts); add CodeQL+dep-review; uv COPY pin |
| stringdb-link | #22 | F-17, F-18, F-19 | **M** | A, B, D(setting) | Redirect/response cap; secret-scanning setting (op); uv COPY pin |
| vep-link | #14 | F-17, F-19, F-20 | **M** | A, B, F | Redirect/response cap (GRCh37+GRCh38 allowlist, keep retry/chunk); add `destructiveHint=false`; uv COPY pin |
| gencc-link | #28 | F-18, F-19 | **B** | A, D(add) | Add CodeQL+dep-review; uv COPY pin |
| orphanet-link | #13 | F-18, F-19 | **B** | A, D(add) | Add CodeQL+dep-review (container scan/SBOM already present); uv COPY pin |
| gnomad-link | #36 | F-18, F-19 | **B** | A, D(setting) | Secret-scanning setting (op) + document; uv COPY pin |
| clinvar-link | #18 | F-19 | **B** | A | uv COPY pin + reproducible-bootstrap regression check |
| hgnc-link | #15 | F-19 | **B** | A | uv COPY pin + regression check |
| mgi-link | #15 | F-19 | **B** | A | uv COPY pin + regression check |
| mondo-link | #14 | F-19 | **B** | A | uv COPY pin + regression check |
| genefoundry-mcp-security-profile | #1 | F-22 | **R** | — | Bump router submodule gitlink to FINAL router SHA; align report/README/gitlink. **MERGE LAST.** |

**Finding severity roll-up:** Medium = F-01, F-02, F-03, F-04, F-05, F-06, F-07, F-08, F-10.
Low = F-09, F-11, F-12, F-13, F-14, F-15, F-16, F-17, F-18, F-19, F-20, F-21, F-22.

---

## 3. F-18 is heterogeneous — verified inventory (do NOT duplicate CodeQL)

Confirmed by workflow-file sweep on 2026-07-12 (not by issue text alone):

- **CodeQL-ABSENT → add SHA-pinned CodeQL + `dependency-review.yml` (6 repos):**
  `gencc-link, hpo-link, mavedb-link, orphanet-link, panelapp-link, spliceailookup-link`.
  All six lack both `security.yml` CodeQL and dependency-review today. Template = copy an
  existing repo's `security.yml` (15 repos already have one) + add least-privilege permissions,
  SHA-pinned actions, PR trigger, blocking high-severity dependency policy.
- **CodeQL PRESENT, secret-scanning REPO SETTING → `gh api` PATCH + document (7 repos with an
  F-18 finding):** `autopvs1-link, genereviews-link, litvar-link, pubtator-link, gtex-link,
  stringdb-link, gnomad-link`. This is a **repository setting, verified via `gh api`, not a diff**,
  and is an **operator follow-up** (see §7). The PR only adds/updates the SECURITY.md/README
  documentation of the required setting.
- All 21 backends already have `container-security.yml` (Trivy scan + SBOM) — do not touch.

`gh api` recipe (operator-run):
```bash
gh api -X PATCH repos/berntpopp/<repo> \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
# verify:
gh api repos/berntpopp/<repo> --jq '.security_and_analysis'
```

---

## 4. Shared recipes (captured once)

### Recipe A — F-19 uv digest-pinned COPY (all 21 backends)

Replace the floating installer bootstrap with the **exact digest already pinned in the router's
own `docker/Dockerfile:7`**:

```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.8.7@sha256:1e26f9a868360eeb32500a35e05787ffff3402f01a8dc8168ef6aee44aef0aab /uv /usr/local/bin/uv
```

- Delete the `RUN pip install --upgrade pip uv && …` line (keep any non-uv steps on that RUN).
- **genereviews-link is a special case (2 sites):** builder line 28 (`pip install --upgrade "pip>=26.1" uv`) → replace with the uv COPY + drop uv from pip; runtime line 65 (`python -m pip install --upgrade "pip>=26.1"`) has a *floating lower-bound* pip → pin to an exact `pip==<version>` or remove the unbounded upgrade.
- **Regression test (every repo):** a test asserting the Dockerfile contains no
  `pip install --upgrade` and contains the exact `ghcr.io/astral-sh/uv:…@sha256:…` COPY line.
- Digest freshness: use the router's current pin verbatim so the whole fleet shares one anchor.

### Recipe B — F-07 / F-10 / F-17 redirect + response-cap

**Canonical approach = keep `follow_redirects=True` + a validating request event-hook** (NOT
disable-+-manual-loop). A deep per-repo investigation (2026-07-12, 9 read-only agents, evidence in
§9) rejected the manual-loop form: it **breaks real downloads** (genereviews' GitHub-Release bundle
302s cross-host to a CDN; stringdb's generic host 302s to the versioned host) and its manual loop
must re-implement httpx's 301/302/303→GET+drop-body vs 307/308 method-switch — a silent correctness
landmine for the **POST** endpoints (metadome, stringdb, uniprot SPARQL, vep). Keeping httpx's
redirect machinery and only *validating* each hop is functionally safe and minimal.

Per client:
1. Keep `follow_redirects=True`; add an httpx **request** `event_hook` that fires on every hop
   (incl. auto-followed redirects) and validates each outgoing `request.url`:
   `scheme == "https"` ∧ host ∈ **exact** allowlist (no suffix/substring) ∧ **no userinfo**.
   Raise on violation. Set `max_redirects` (3–5).
2. **Derive the allowlist from the configured base URL host(s) at client-build time — NEVER
   hardcode.** Every backend's base URL is operator-overridable; a hardcoded literal breaks the
   override (and stringdb's periodic `version-N-N` bump). Seed with the documented default host(s).
3. **Byte cap must FAIL CLOSED (raise), never silently truncate** — a truncated JSON/turtle/TSV/PNG
   body is unparseable or corrupt. Enforce by streaming (`client.stream` + `iter_bytes` running
   total) and aborting past the cap *before* decode; a `Content-Length` pre-check is a cheap first
   guard but must not be trusted alone (chunked/gzip). This means refactoring buffered
   `.json()/.text/.content` reads to a capped streamed read — the main non-trivial code change
   (notably stringdb, vep, gtex).
4. **Classify the guard exception as non-retryable** and route it through the envelope — several
   clients' retry loops otherwise retry a validation/cap failure 3× or surface it as `internal_error`.
5. **Adversarial tests:** cross-host redirect, non-HTTPS hop, `user:pass@host` userinfo, redirect
   loop, oversized response → all fail closed; happy-path (no redirect) behavior unchanged.

**Per-repo parameters (verified against live upstream + code, 2026-07-12):**

| Repo | Redirects in normal op? | Exact host allowlist (derive from config) | Byte cap (fail-closed) | Repo-specific caveat |
|---|---|---|---|---|
| metadome | No | `stuart.radboudumc.nl` | **≥64 MB** or exempt the JSON path | titin-scale `/result/` landscapes are MB→tens-of-MB; 2 POST endpoints |
| litvar | No (same-host https at most) | `www.ncbi.nlm.nih.gov` | ~25 MB (configurable) | prior entrypoint fix `a1bd540` is unrelated to redirects → no regression risk |
| uniprot | No | `sparql.uniprot.org` | **~32 MiB, ABOVE the existing 8 MiB text fence — never ≤8 MiB** | SPARQL is POST; cap must error, not truncate; no REST/`Link` pagination in use |
| panelapp | No | `panelapp.genomicsengland.co.uk`, `panelapp-aus.org` | 50 MB + **pages≤100, rows≤100k** | AU host is `panelapp-aus.org` (not agha.umccr); `next` is a JSON field → validate **same-origin, normalize scheme→https (don't reject)**; verify live `next` before exact-host reject |
| gtex | No (only a 307→http downgrade, correctly rejected) | `gtexportal.org` | 16 MB (never 2 MB) | GET-only; observed 1.73 MB legit multi-gene payload |
| spliceai | No | `spliceai-37/38-…a.run.app`, `pangolin-37/38-…a.run.app`, `rest.ensembl.org`, `grch37.rest.ensembl.org` | 16 MB (bytes, not time) | upstream is **Cloud Run + Ensembl, NOT broadinstitute.org**; keep 90 s timeout + service soft-deadlines |
| stringdb | **Yes** if generic host configured (→ versioned) | `version-12-0.string-db.org` + `string-db.org` | 32 MiB | POST API — a manual loop would drop the form body on 302; `version-N` bump changes the host |
| vep | No | `rest.ensembl.org`, `grch37.rest.ensembl.org` | ~50 MB per chunk (decoded bytes) | keep 429/Retry-After + `_post_chunked` intact; cap-exceed maps to non-retryable |

- **uniprot** — this response cap is **shared with F-08** (one cap, one PR); F-08's SPARQL
  LIMIT/graph-form clamp policy is separate logic in the same PR.
- **panelapp** — the DRF `next` URL is validated **inside the pagination loop** (not the redirect
  hook), same-origin, with the page/row ceilings as fail-loud guards.
- **genereviews** — although not in the table above, its **download** client
  (`ingest/github_release.py`) also uses this event-hook; its cross-host CDN allowlist
  (`release-assets.githubusercontent.com`) and stream-to-file caps are detailed in **Recipe E** — it
  is the one *required* cross-host redirect in the fleet.

### Recipe C — F-03 / F-09 bound-input + fixed-enum-error (autopvs1 REST, mavedb HGVS)

Reuse the **existing MCP redaction/envelope helpers**; do not invent new sanitizers:

- autopvs1: `autopvs1_link/mcp/untrusted_content.py` (`sanitize_message`, `sanitize_error_details`)
  + `mcp/error_guard.py` (fixed `invalid_input` envelope). F-03 = **port this MCP-path policy to the
  legacy REST routes** (`api/routes/variant.py`, `api/routes/gene.py`).
- mavedb: `mavedb_link/mcp/envelope.py` (`_safe_message`, `build_arg_error_envelope`,
  `classify_exception`). F-09 = add bound validation upstream of the envelope.

Policy:
1. **Bound + validate before any I/O or cache use:** length limit, list-size limit, and a
   conservative HGVS/variant/gene grammar. Reject oversize/malformed **before** upstream calls,
   logging, or cache insertion.
2. **Fixed caller-visible errors:** return a fixed enum error message (`invalid_input`, etc.); place
   only *separately validated* identifiers in structured fields. Sanitize/truncate is **not** enough
   — prose passes through it (prior fleet lesson); the message must be fixed/enumerated.
3. **Never log raw genomic input or exception prose:** log error class/code only.
4. **Tests:** genomic identifiers, upstream bodies, and exception prose never reach logs/responses;
   oversize/malformed inputs fail before upstream calls/cache insertion.

### Recipe D — F-18 CI workflows (add) + secret-scanning (setting)

- **Add group (6 repos, §3):** new `security.yml` (CodeQL, SHA-pinned actions, least-privilege
  `permissions:`, PR trigger) + `dependency-review.yml` (SHA-pinned, blocking high-severity). Copy
  the shape from an existing 15-repo `security.yml`; pin every action to a commit SHA.
- **Setting group (7 repos, §3):** repo setting via `gh api` (operator, §7) + document required
  settings in SECURITY.md/README (the only in-PR change).

### Recipe E — F-06 / F-16 authenticity + decompression-bomb guard (genereviews bundle, clingen `.zst`, pubtator images)

**Authenticity is not integrity.** A checksum fetched from the same (possibly redirected) host does
NOT close these — the digest must be anchored **independently, committed in-repo** (constant or
manifest) or a signature.

- **clingen F-16 (in-repo `.zst`):** verify the **committed SHA-256** before decompression; enforce
  an **expanded-size ceiling** (bounded streaming decompress, abort past MAX); write atomically
  (temp + `os.replace`); fail closed on mismatch/truncation/bomb. Tests: mismatch, truncation,
  decompression bomb.
- **genereviews F-06 (remote bundle) — HAS A REQUIRED CROSS-HOST REDIRECT:** the GitHub-Release
  download 302s from `github.com` to **`release-assets.githubusercontent.com`** (GitHub's current
  asset CDN — *not* `objects.githubusercontent.com`, verified live 2026-07-12). The download client's
  allowlist MUST include it (plus defensive `objects.githubusercontent.com`,
  `github-releases.githubusercontent.com`), or the bundle bootstrap breaks with `HTTPStatusError` on
  the 302. Use Recipe B (event-hook) on the download client; `api.github.com` allowlisted only for the
  release-resolve client. Replace `timeout=None` with `httpx.Timeout(connect=30, read=60, write=30,
  pool=30)` — httpx read-timeout is *between-reads*, so this aborts a stalled socket **without**
  capping a legitimate ~10-min large download (do NOT impose a total-transfer deadline). Download
  caps: bundle 2 GiB, `.sha256` 1 MiB, sidedata 64 MiB. Anchor authenticity in a **committed
  digest**, not one downloaded from `BUNDLE_URL`'s host.
- **genereviews F-05 (corpus ingest):** connect/read/total-*per-read* deadlines via `httpx.Timeout`
  (no `timeout=None`); compressed **and expanded** byte ceilings (NCBI corpus tarball is ~613 MB and
  growing → cap ~4 GiB, fail-closed, on the streamed read); member-count limit; bounded per-worker
  memory (stream members, don't read whole compressed members into RAM). NCBI hosts
  (`ftp.ncbi.nlm.nih.gov`) don't redirect — leave `follow_redirects=False` there.
- **pubtator F-15:** digest-pin `pgvector/pgvector:0.8.4-pg18-trixie` (and every prod image); add a
  regression check covering all production images. **F-14:** production Compose requires the DB
  secret with **no predictable fallback**; document rotation.

### Recipe F — MCP annotation/schema completion (metadome F-11, litvar F-12, vep F-20)

Land the annotation/schema code now; the **router drift re-pin is a POST-DEPLOY follow-up** (the
baseline is captured from *live* servers) — do **not** block the PR on a re-pin it cannot do yet.

**Verify each hint against the real side effect** before stamping it:
- **metadome F-11** — `request_tolerance_landscape` POSTs `/submit_visualization/` →
  `readOnlyHint=false`, `destructiveHint=false`, `idempotentHint=true`. **`idempotentHint=true` is
  correct here** because it dedupes by `transcript_id`; do not assume this for other tools.
- **litvar F-12** — all 6 tools get shared read-only/non-destructive `ToolAnnotations`; the 4 tools
  missing `output_schema` (`gene.py`, `literature.py`, `metadata.py`, `rsid.py`) get one.
- **vep F-20** — add the missing `destructiveHint=false` to the shared read-only annotation.

---

## 5. Effort tiers (M/H/B/R)

Assigned to guide Phase-2 subagent depth/model. A repo's tier = its hardest finding.

| Tier | Meaning | Depth | Repos |
|---|---|---|---|
| **H** — High | Substantive security logic (input validation, redirect allowlisting, SPARQL policy, resource ceilings, REST redaction, fail-closed config, hostile-tag shell hardening). | Careful adversarial TDD; highest-effort review. | router, hpo, autopvs1, metadome, genereviews, litvar, uniprot |
| **M** — Medium | Moderate logic (HGVS bound, integrity verify, digest-pin + regression check, prod-secret enforcement, redirect/response cap on simpler clients, annotation completion). | Real adversarial tests; standard review. | mavedb, pubtator, clingen, panelapp, gtex, spliceai, stringdb, vep |
| **B** — Boilerplate | Templated CI workflow additions + Recipe A uv COPY. Mechanical but needs a regression test. | Copy template + regression test. | gencc, orphanet, gnomad, clinvar, hgnc, mgi, mondo |
| **R** — Rote/repo-op | Submodule gitlink bump; (secret-scanning `gh api` settings are operator-run, §7). | Verified via clone/API, not code diff. | genefoundry-mcp-security-profile |

---

## 6. Non-obvious requirements (traps a naive executor gets wrong)

1. **F-01 FIRST.** `fleet-probe.yml:58` is the *only* YAML syntax failure in 110 fleet workflow
   files (`run: echo "::warning::fleet-probe: …"` — the `fleet-probe: ` colon-space breaks the plain
   scalar). Fix via block scalar (`run: |`) or full-quote. Add **actionlint** + a **YAML-parse
   regression test over ALL `.github/workflows/*.yml`** that fails on the original line-58 syntax.
2. **F-18 is partly done** — verified 15/21 already have CodeQL; add only to the 6 (§3). Never
   duplicate CodeQL. Secret-scanning is a **repo setting** (`gh api`, operator), not a diff.
3. **F-06/F-16 are AUTHENTICITY not integrity** — anchor the digest in a committed/independent pin
   or signature; "download the checksum from the same host and compare" does NOT close it. Add
   expanded-size ceilings / decompression-bomb guards.
4. **F-11/F-12/F-20 change tool fingerprints** — land the code, but the router drift re-pin is a
   **post-deploy follow-up** (baseline is from live servers). Do NOT block the PR on a re-pin it
   can't do yet; note as deferred.
5. **Verify each MCP hint against the actual side effect** before stamping (metadome F-11
   `idempotentHint=true` is correct — dedupes by `transcript_id`; don't assume for others).
6. **One PR per repo** — uniprot F-08+F-17 share one response cap = one PR.
7. **No token passthrough / edge-auth-only** invariants unchanged; backends stay unpublished.

---

## 7. Sequencing, guardrails, deferred items

**Phase order (per this spec's parent task):**
- **P2 — Implementation:** one subagent per repo, TDD. Branch off **pristine `origin/main`**
  (`git fetch` first). Failing adversarial test → see it fail → minimal implementation →
  `make ci-local` GREEN.
- **P3 — Codex gate per PR:** `codex exec -s read-only -m gpt-5.5 -c model_reasoning_effort=xhigh
  -C <repo> "<adversarially verify closure, no bypass/leak/regression; check shared-recipe repos for
  missed hops/surfaces>" < /dev/null` (FOREGROUND, always `< /dev/null`). Merge bar = findings
  genuinely closed, no reachable bypass, ci-local green. On FIX: remediate and re-gate; never merge
  on unresolved FIX.
- **P4 — Merge order:** **router (#47) first** → all backends → **security-profile #1 (F-22) LAST**
  (bump submodule gitlink to the FINAL router SHA; align report/README/gitlink). FF-merge on
  green + Codex-SHIP; close each issue.

**Guardrails (hard):** branch/rebase only off pristine `origin/main`; never `-D` an unmerged branch
without recording tip SHA + rationale; `git worktree remove` WITHOUT `--force` (report dirty ones);
STOP + report on red ci-local / merge conflict / unseen upstream commits; **no force-push**; **NO
DEPLOY**.

**Operator follow-ups (out of scope for these PRs):**
- Redeploy all changed backends + router (operator owns redeploy).
- **F-18 secret-scanning `gh api` settings** for the 7 setting-group repos (§3).
- **F-11/F-12/F-20 router drift re-pin** once the changed backends are live (baseline is captured
  from live servers).

---

## 8. Design resolutions & assumptions (veto at approval)

1. **Recipes are copied per-repo, not a shared library.** The `-link` repos have no shared Python
   package; per-repo copy matches the fleet's container-hardening precedent ("copy it, don't
   reinvent"). Each repo adapts the recipe to its own client/Dockerfile; the integration test is the
   contract.
2. **Redirect enforcement = keep `follow_redirects=True` + a validating request event-hook**
   (fleet-wide), *revised* from the original disable-+-manual-loop after a deep per-repo check (§4
   table, §9, 9 read-only agents, 2026-07-12): the manual-loop form breaks genereviews' cross-host
   CDN bundle redirect and stringdb's generic→versioned redirect, and mishandles POST method/body on
   redirect. Allowlists are **derived from each repo's configured base URL** (never hardcoded); byte
   caps **fail closed, never truncate**, sized per-repo (§4 table).
3. **Router F-21 fail-closed extends the existing `is_insecure_public_bind` pattern:** an
   authenticated **non-loopback** bind requires `GF_RATE_LIMIT_RPM > 0` and (when `/metrics` is
   exposed) `GF_METRICS_TOKEN`; `GF_ALLOW_INSECURE=true` remains the documented local/dev escape
   hatch. `should_warn_no_rate_limit` graduates from warn to fail-closed for production/reachable
   binds.
4. **Branch name:** `security/2026-07-12-post-remediation-hardening` in every repo.
5. **Secret-scanning enablement is documented in-PR but executed by the operator** (repo setting).
6. **This spec is committed only on operator approval** (not auto-committed).

---

## 9. Redirect/response-cap investigation evidence (2026-07-12)

Nine read-only agents each audited one client's redirect + cap behavior against live upstreams and
source. Verdicts and load-bearing facts:

- **metadome** — SAFE w/ (B). Single client `api/client.py:142` `follow_redirects=True`; host
  `stuart.radboudumc.nl`; 2 POST endpoints; no redirect in normal op (paths avoid Django
  APPEND_SLASH 301). Cap ≥64 MB or exempt JSON (titin landscapes). Guard exception must not subclass
  `httpx.TimeoutException`/`TransportError` (retry loop at `client.py:163`).
- **litvar** — SAFE w/ (B). `api/client.py:91` `follow_redirects=True`; host `www.ncbi.nlm.nih.gov`
  (already https). Prior entrypoint fix `a1bd540` = path-encoding/id-resolution, **not** redirects
  (`git show a1bd540 -- client.py` has no redirect change) → no regression. Cap ~25 MB.
- **uniprot** — SAFE w/ (B). SPARQL-only, single POST client `api/client.py:103`
  `follow_redirects=True`; host `sparql.uniprot.org` (QLever, no async-job/`Link` pagination). An
  8 MiB untrusted-text fence already exists (`untrusted_content.py:71-73`) → HTTP cap must be **above**
  it (~32 MiB) and error-on-exceed.
- **panelapp** — SAFE w/ adjustments. `api/client.py:60` `follow_redirects=True`; hosts
  `panelapp.genomicsengland.co.uk` + **`panelapp-aus.org`** (brief's `agha.umccr.org` is stale). DRF
  `next` is a JSON field fetched by `_list_paginated` (`client.py:135`), not a redirect → validate
  same-origin + **normalize scheme→https, don't reject** (proxy may emit http); all fixtures have
  `next: null` so confirm live before exact-host reject. Caps must be fail-loud (search filters the
  full list).
- **gtex** — SAFE. `api/client.py:174` `follow_redirects=True`; host `gtexportal.org`; GET-only; base
  URL **not** env-overridable (nested-model delimiter gap). Only 3xx is a trailing-slash 307→**http**
  downgrade (correctly rejected by https-only). Observed 1.73 MB payload → cap 16 MB, never 2 MB.
- **spliceai** — SAFE w/ adjustments. `api/base_client.py:85` `follow_redirects=True`; upstream is
  **Cloud Run** (`spliceai-37/38-…a.run.app`, `pangolin-37/38-…a.run.app`) **+ Ensembl**
  (`rest.ensembl.org`, `grch37.rest.ensembl.org`) — **not broadinstitute.org**. No redirects in a
  50-entry live capture. Timeout `httpx.Timeout(90)` + soft-deadlines (55 s/30 s) must stay; cap on
  bytes not time (16 MB).
- **stringdb** — RISK unless allowlist includes the versioned host. `api/client.py:168`
  `follow_redirects=True`; production pins `version-12-0.string-db.org`; generic `string-db.org`
  **302s to the versioned host** (documented stable-address behavior). **POST** API → manual loop
  would drop the form body. Cap 32 MiB (8 MiB text fence + multi-MB images). Requires stream refactor
  of `_make_request`/`get_network_image`.
- **vep** — SAFE w/ adjustments. `api/base_client.py:82` `follow_redirects=True`; hosts
  `rest.ensembl.org` + `grch37.rest.ensembl.org` (both env-overridable → derive allowlist). No
  redirects for REST paths. Retry (429/Retry-After) + `_post_chunked` are independent of redirect
  handling; cap ~50 MB/chunk on decoded bytes (gzip), mapped non-retryable.
- **genereviews (download/ingest)** — RISK under naive (A). Download client
  `ingest/github_release.py:51` `follow_redirects=True`, `timeout=None`. GitHub-Release asset 302s
  `github.com` → **`release-assets.githubusercontent.com`** (live-verified; ~96 MB bundle). Bare
  `follow_redirects=False` raises `HTTPStatusError` on the 302 (httpx `raise_for_status()` raises on
  3xx) → bootstrap aborts. NCBI corpus client `corpus/archive.py:80` (~613 MB tarball) doesn't
  redirect. Caps: bundle 2 GiB / sha256 1 MiB / NCBI 4 GiB; per-read `httpx.Timeout`, not a total
  deadline. F-06 authenticity still needs an out-of-band committed digest (redirect/cap hardening
  does not close it).

**Cross-cutting conclusions folded into Recipe B / Resolution #2:** approach (B) fleet-wide;
allowlist derived from config; caps fail-closed and generously sized; guard exceptions non-retryable;
byte cap requires a streaming refactor in several clients.
```
