# Fleet Trivy image-scan gating policy — Decision Brief

**Workstream:** L (P3, type=brief) · **Date:** 2026-06-30 · **Scope:** the 21 `-link` backends + the `genefoundry-router` repo
**Status:** analysis / decision brief — no code changed, nothing pushed.

> **TL;DR** — This is *not* an open policy question. The Container-Hardening Standard v1 already
> mandates *"fail the build on HIGH/CRITICAL fixable vulnerabilities"* (`docs/CONTAINER-HARDENING-STANDARD-v1.md:129`).
> What we actually have is **drift / non-conformance**: 8 of 21 backends already gate correctly,
> 12 are report-only (`exit-code: "0"`), 1 (`genereviews`) is both report-only *and* uses an
> unpinned action tag, and the **router itself has no container scan at all**. The remediation is
> to make every repo match the gate shape that `gencc-link` / `mavedb-link` / `metadome-link` /
> `uniprot-link` already use, and to add that same job to the router as the reference. The
> recommended gate: `severity: CRITICAL,HIGH` + `ignore-unfixed: true` + `exit-code: "1"`, with a
> separate **non-gating** `if: always()` SBOM step.

---

## Global Constraints

Python 3.12+ with uv (uv sync --group dev, uv run); modern typing (X|None, builtin generics); ruff lint+format and mypy must pass; TDD (failing test first, one atomic commit per task); 600-LOC/module budget (scripts/check_file_size.py via make lint-loc); 'make ci-local' must pass before handoff; FastMCP 3.x symbols verified against the INSTALLED package (post-cutoff, fast-moving); no caller-Authorization passthrough to backends; Streamable-HTTP only (no SSE); backends unauthenticated-by-design and reachable ONLY via router/proxy; research-use-only / not-clinical-decision-support disclaimer preserved.

---

## Context & problem

### What the standard already requires

`docs/CONTAINER-HARDENING-STANDARD-v1.md` mandates the gate in three places — this is settled policy, not a proposal:

- **L129–130:** *"**Scan every image in CI** (Trivy or Grype); **fail the build on HIGH/CRITICAL** fixable vulnerabilities. Re-scan on a schedule, not just at release."*
- **L168** (universal gap #3): *"No CI image scanning and no SBOM on any repo. Add Trivy/Grype **(fail on fixable HIGH/CRITICAL)** + an SBOM artifact."*
- **L208** (Definition of Done): *"CI **image scan** (Trivy/Grype) **fails on fixable HIGH/CRITICAL**; **SBOM** generated and retained…"*

### What the code actually does (verified 2026-06-30)

The 2026-06-30 audit finding ("`container-security.yml` runs Trivy with exit-code 0 so a CRITICAL/HIGH image CVE never fails CI — confirmed in gnomad, hpo, orphanet, likely fleet-wide") is **correct for those three but NOT fleet-wide**. The fleet has drifted into four shapes. Verified by `grep -c 'exit-code: "1"'` across all 21 `container-security.yml` files:

| Category | Repos (count) | Shape | Conformant? |
|---|---|---|---|
| **A — report-only** | clingen, gnomad, gtex, hgnc, hpo, litvar, mgi, mondo, orphanet, panelapp, pubtator, stringdb (**12**) | vuln-scan `format: table` + `exit-code: "0"`, then CycloneDX SBOM `exit-code: "0"`. **No gate step.** | **No** — violates L129/L168/L208 |
| **B — gate, 2-step (canonical)** | gencc, mavedb, metadome, uniprot (**4**) | the scan step *is* the gate: `severity: HIGH,CRITICAL` + `ignore-unfixed: true` + `exit-code: "1"`; SBOM step has `if: always()` + `exit-code: "0"` | **Yes** — cleanest form |
| **C — gate, 3-step** | autopvs1, clinvar, spliceailookup, vep (**4**) | report-only table (exit 0) → SBOM (exit 0) → a *separate* final gate step (`severity` + `ignore-unfixed` + `exit-code: "1"`) | **Yes**, but runs Trivy **twice** per image (redundant report scan) |
| **D — divergent** | genereviews (**1**) | unpinned `aquasecurity/trivy-action@v0.36.0`, `format: sarif`, `severity: CRITICAL,HIGH` + `ignore-unfixed` but **no `exit-code`** → default `0` → **does not gate** | **No** — report-only *and* breaks the digest-pin convention |

**File:line evidence (representative):**

- Report-only gate-less scan (Category A) — `gnomad-link/.github/workflows/container-security.yml:30-36` and the SBOM at `:38-44`, both `exit-code: "0"`. Identical at `hpo-link/.../container-security.yml:30-44` and `orphanet-link/.../container-security.yml:30-44`.
- Canonical 2-step gate (Category B) — `gencc-link/.github/workflows/container-security.yml:31-38`:
  ```yaml
  - name: Trivy scan (fail on fixable HIGH/CRITICAL)
    uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
    with:
      image-ref: gencc-link:scan
      format: table
      severity: HIGH,CRITICAL
      ignore-unfixed: true
      exit-code: "1"
  ```
  SBOM kept non-gating with `if: always()` at `gencc-link/.../container-security.yml:40-47`.
- 3-step gate (Category C) — separate trailing gate step at `autopvs1-link/.github/workflows/container-security.yml:55-62` (after a report-only scan at `:30-36` and SBOM at `:38-44`); same trailing-gate pattern in `clinvar-link/.../container-security.yml:59-65`, `spliceailookup-link/.../container-security.yml:59-65`, `vep-link/.../container-security.yml:59-65`.
- Divergent (Category D) — `genereviews-link/.github/workflows/container-security.yml:42-49`: unpinned `trivy-action@v0.36.0` (L43), `format: sarif` with `severity`/`ignore-unfixed` but **no `exit-code:`** (so the [trivy-action default of `0`](https://github.com/aquasecurity/trivy-action) applies and the SARIF step never fails the build). Every other repo pins `@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0`.

**The router gap (reference-implementation inversion).** The standard says the router is Tier A and *"Fixing them on the router first sets the pattern the fleet copies"* (`CONTAINER-HARDENING-STANDARD-v1.md:170-172`), yet **the router has no `container-security.yml`**. `genefoundry-router/.github/workflows/` contains only `ci.yml`, `drift.yml`, and `security.yml`; `security.yml` runs CodeQL + dependency-review and **no Trivy image scan / SBOM**. So the reference repo is the *least* conformant on this control. The router does have a buildable `docker/Dockerfile` (confirmed: `genefoundry-router/docker/{Dockerfile,docker-compose.*.yml}` exist), so adding the canonical job there is straightforward.

**Why "just flip `exit-code` to 1" is the wrong minimal fix.** Flipping `exit-code: "0"` → `"1"` *alone* on a Category A scan (which has no `severity` and no `ignore-unfixed`) would gate on **all** severities including **unfixable** base-image CVEs (e.g. an unpatched `libc`/`zlib` CVE in `python:3.12-slim` with no upstream fix). Every backend would go red on CVEs nobody can action — a self-inflicted outage and exactly the failure mode that makes teams revert to `exit-code: 0`. The load-bearing one-liner (`exit-code: "0"` → `"1"`) must travel with `severity: CRITICAL,HIGH` and `ignore-unfixed: true`, plus `if: always()` on the SBOM/upload steps so evidence survives a failing gate.

---

## Open question(s)

1. **Q1 — Gate or report-only?** Should the vuln-scan step fail CI on fixable CRITICAL/HIGH, or stay report-only-by-design?
2. **Q2 — Canonical step shape?** If we gate, which of the two working shapes is the fleet canon — the 2-step "scan-is-the-gate" form (Category B) or the 3-step "report + SBOM + separate gate" form (Category C)?
3. **Q3 — `genereviews` divergence.** It is unpinned *and* uses a SARIF-format step that doesn't gate. How do we bring it to canon — pin + add a gate, or convert wholesale to the 2-step form (and what happens to its GitHub code-scanning SARIF upload)?
4. **Q4 — Scheduled-run behavior.** All these workflows also run on a weekly `schedule:`. A fresh vuln DB can turn a previously-green `main` red when a *new* fixable CVE is disclosed against an already-shipped image. Do scheduled runs gate (red X) the same as PR/push, or signal differently?
5. **Q5 — The router itself.** Does the router get the same `container-security.yml` (so it is, in fact, the reference), and is that in scope for this remediation?

---

## Options

### Q1 — Gate vs report-only

- **Option 1A — Gate on fixable CRITICAL/HIGH (`exit-code: "1"`, `severity: CRITICAL,HIGH`, `ignore-unfixed: true`).** Matches the written standard verbatim and what 8 repos already do. `ignore-unfixed: true` excludes CVEs with no available fix, so the gate only fires on something a maintainer can actually act on (bump base digest / lockfile). Trade-off: maintainers must keep base digests current (already required by standard item 28, "rebuild cadence"); a newly disclosed fixable CVE can block an unrelated PR until the base/lock is bumped — but that is the intended forcing function.
- **Option 1B — Report-only by design (`exit-code: "0"`, keep the artifact/SARIF).** Zero friction; never blocks a PR. Trade-off: directly contradicts `CONTAINER-HARDENING-STANDARD-v1.md:129/168/208`; produces evidence nobody is forced to read; a CRITICAL fixable CVE ships silently. This is the status quo that the audit flagged.
- **Option 1C — Gate CRITICAL only; HIGH report-only.** Lower friction than 1A. Trade-off: splits from the standard's explicit "HIGH/CRITICAL", and HIGH-severity RCE/auth-bypass CVEs are common and material; adds a config axis the fleet has to remember.

### Q2 — Canonical step shape

- **Option 2A — 2-step "scan-is-the-gate" (Category B / gencc).** One Trivy scan (table, severity-filtered, `exit-code: "1"`) + one non-gating SBOM (`if: always()`). One image scan per run; smallest diff for the 12 Category A repos. Trade-off: the human-readable full-table report (all severities, including informational MEDIUM/LOW and unfixed) is no longer emitted as an artifact — but the failing step still prints the offending CRITICAL/HIGH rows to the job log, and the SBOM remains the retained evidence.
- **Option 2B — 3-step "report + SBOM + separate gate" (Category C / autopvs1).** Keeps a full `trivy-report.txt` artifact *and* gates. Trade-off: runs Trivy **twice** per CI run (two full image scans = ~2× scan time and DB pulls) for a report few people open; more YAML to keep in sync.

### Q3 — `genereviews`

- **Option 3A — Convert to the 2-step canon (2A) and drop SARIF.** Uniform with the fleet; removes the unpinned tag and the SARIF/`exit-code` gotcha in one move. Trade-off: loses the GitHub "Security › Code scanning" tab entry for this repo (no other `-link` repo populates it for container CVEs anyway, so this only *removes an inconsistency*).
- **Option 3B — Pin the tag, keep the SARIF upload, add a separate gating table step.** Preserves the code-scanning UI. Trade-off: 3-step shape (the thing 2A avoids) **plus** a documented sharp edge — [`trivy-action` issue #309: "exit-code with SARIF format doesn't respect the 'severity' parameter"](https://github.com/aquasecurity/trivy-action/issues/309) — so the gate must be a *separate* `format: table` step regardless; SARIF stays purely informational.

### Q4 — Scheduled runs

- **Option 4A — Gate on schedule too (no special-casing).** The weekly run with a fresh DB catches newly disclosed fixable CVEs against shipped images; a red scheduled run is the signal to rebuild (standard item 28). Trade-off: a red ✗ on a scheduled run on `main` with no code change can look alarming and needs a known runbook ("bump base digest / lockfile, re-run").
- **Option 4B — Scheduled run opens/updates a tracking issue instead of failing.** Friendlier signal, no scary red on `main`. Trade-off: more workflow code (issue create/update step, dedupe), and a yellow-ish signal is easier to ignore than a red gate. The router already has a `drift.yml` issue-heartbeat pattern that could be mirrored later if desired.

### Q5 — Router

- **Option 5A — Add the canonical `container-security.yml` to the router** (separate from its `security.yml`/CodeQL). Makes the standard's "reference repo" claim true and gives the fleet a literal file to copy. Trade-off: one new workflow on the router.
- **Option 5B — Leave the router as-is for this workstream.** Smaller blast radius. Trade-off: the reference repo stays the least conformant; future "copy the router" instructions have nothing to copy.

---

## Recommendation

| Q | Pick | One-line rationale |
|---|---|---|
| **Q1** | **1A — gate on fixable CRITICAL/HIGH** | The standard already mandates it; `ignore-unfixed: true` is precisely the knob that prevents unfixable-base-CVE breakage. |
| **Q2** | **2A — 2-step "scan-is-the-gate"** | Smallest diff for the 12 non-conformant repos, one image scan per run, already proven in `gencc`/`mavedb`/`metadome`/`uniprot`. |
| **Q3** | **3A — convert `genereviews` to 2A and pin the action** | Removes the unpinned tag *and* the SARIF/`exit-code` gotcha; loses nothing the rest of the fleet has. |
| **Q4** | **4A — gate on schedule too**, with a one-line runbook note in the repo | Matches "re-scan on a schedule" (L130) and the rebuild-cadence forcing function; revisit 4B only if scheduled red becomes noisy. |
| **Q5** | **5A — add the job to the router** | Makes the reference-implementation claim real; gives the fleet a copy source. |

### The canonical block (drop-in for every repo)

```yaml
      - name: Trivy scan (fail on fixable CRITICAL/HIGH)
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: <repo>:scan          # e.g. hpo-link:scan
          format: table
          severity: CRITICAL,HIGH
          ignore-unfixed: true
          exit-code: "1"

      - name: Generate SBOM (CycloneDX)
        if: always()                       # SBOM is non-gating; produce it even when the scan fails
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: <repo>:scan
          format: cyclonedx
          output: <repo>-sbom.cdx.json
          exit-code: "0"

      - name: Upload scan artifacts
        if: always()                       # keep evidence on a failing gate
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: container-security-artifacts
          path: <repo>-sbom.cdx.json
```

### The literal one-line diff (the load-bearing change, Category A repos)

The minimal mechanical edit to the existing report-only scan step is `exit-code: "0"` → `exit-code: "1"`, but it **must** be accompanied by the two filter lines and `if: always()` on the SBOM/upload steps (see canonical block). Shown against `gnomad-link/.github/workflows/container-security.yml:30-44`:

```diff
       - name: Run Trivy vulnerability scan
         uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
         with:
           image-ref: gnomad-link:scan
           format: table
-          output: trivy-report.txt
-          exit-code: "0"
+          severity: CRITICAL,HIGH
+          ignore-unfixed: true
+          exit-code: "1"

       - name: Generate SBOM
+        if: always()
         uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
         with:
           image-ref: gnomad-link:scan
           format: cyclonedx
           output: gnomad-link-sbom.cdx.json
           exit-code: "0"
```

(The `output: trivy-report.txt` line is dropped because, in the 2-step form, the failing scan prints offending rows to the job log and the SBOM is the retained artifact; the `Upload scan artifacts` step also gains `if: always()` and drops `trivy-report.txt` from its `path:`.)

### Best-practice citations behind the pick

- **`exit-code` default is `0`** (the action does **not** fail by default) — this is why `genereviews`' severity-filtered SARIF step never gated. `aquasecurity/trivy-action` README, "exit-code: Exit code when vulnerabilities were found (default: 0)" — <https://github.com/aquasecurity/trivy-action>.
- **`severity` + `exit-code: 1` is the canonical CI gate**, and **`ignore-unfixed: true` hides vulnerabilities that have no fix available**, so the gate fires only on actionable CVEs and won't break on unpatchable base-image CVEs — Trivy docs / filtering guidance: <https://trivy.dev/docs/latest/configuration/filtering/> and <https://oneuptime.com/blog/post/2026-01-28-trivy-severity-filtering/view>.
- **SARIF + `exit-code` interaction gotcha** (justifies 3A over 3B and the "gate must be a plain `table`/`json` step" rule): `aquasecurity/trivy-action` issue #309, *"exit-code with SARIF format doesn't respect the 'severity' parameter"* — <https://github.com/aquasecurity/trivy-action/issues/309>.
- **The internal standard** itself: `docs/CONTAINER-HARDENING-STANDARD-v1.md:129-130, 168, 208`.
- **`severity` order is immaterial** to Trivy (set membership, not precedence); normalizing the 8 conformant repos from `HIGH,CRITICAL` to `CRITICAL,HIGH` is cosmetic-only and optional.

---

## Impact / migration

All changes are CI-YAML-only; **no Python, no `genefoundry_router/` source, no LOC-budget impact, no `make ci-local` interaction** beyond the workflows themselves. Each file is ~50–70 lines.

| Group | Repos | Change | Size |
|---|---|---|---|
| **Category A → canonical 2A** | clingen, gnomad, gtex, hgnc, hpo, litvar, mgi, mondo, orphanet, panelapp, pubtator, stringdb (**12**) | add `severity`/`ignore-unfixed`, flip `exit-code` to `"1"`, add `if: always()` to SBOM + upload | ~5–7 changed lines per file |
| **Category B (already canon)** | gencc, mavedb, metadome, uniprot (**4**) | **none** required; optional cosmetic `HIGH,CRITICAL` → `CRITICAL,HIGH` | 0–1 line |
| **Category C → collapse to 2A** | autopvs1, clinvar, spliceailookup, vep (**4**) | optional cleanup: delete the redundant report-only scan, merge filters into the surviving gate step (already conformant, so this is consistency-only — can be deferred) | ~10 removed lines per file |
| **Category D** | genereviews (**1**) | pin `trivy-action` to `@ed142fd…# v0.36.0`; replace SARIF-only step with the canonical 2-step gate (3A) | ~15 lines |
| **Router** | genefoundry-router (**1**) | add a new `container-security.yml` with the canonical job (build `docker/Dockerfile` → gate → SBOM) | ~50 lines (new file) |

**Net required for conformance:** 12 Category A edits + 1 `genereviews` fix + 1 new router file = **14 repos touched** (the 4 Category C cleanups and the cosmetic Category B normalization are optional). Each is one small atomic PR; no runtime/deploy change, no image rebuild required to land.

**Standard text:** consider adding the exact canonical block to `CONTAINER-HARDENING-STANDARD-v1.md` (after L168/L208) as the copy-paste reference, so future repos inherit it without re-deriving the `severity`/`ignore-unfixed`/`if: always()` details.

**Disclaimer note:** this workstream touches only CI workflows; the research-use-only / not-clinical-decision-support disclaimers in each repo are untouched and preserved.

---

## If accepted, the follow-on implementation plan is:

1. **Land the reference first (router).** Add `genefoundry-router/.github/workflows/container-security.yml` with the canonical job (`docker build -f docker/Dockerfile -t genefoundry-router:scan .` → gating Trivy scan → non-gating SBOM → upload). Verify the gate fires (temporarily point at a known-vulnerable image or assert the step's `exit-code` wiring) and that the SBOM still uploads on failure (`if: always()`). One PR.
2. **Codify the canon in the standard.** Add the canonical YAML block to `CONTAINER-HARDENING-STANDARD-v1.md` near L168/L208 as the literal reference; note `exit-code` default `0`, the `ignore-unfixed` rationale, and the SARIF/`exit-code` gotcha. Same or follow-up PR.
3. **Migrate the 12 Category A repos** to the 2-step canon (one small PR each, or a scripted batch). For each: add `severity: CRITICAL,HIGH` + `ignore-unfixed: true`, set `exit-code: "1"` on the scan step, add `if: always()` to the SBOM + upload steps, trim the obsolete `trivy-report.txt`. Confirm the workflow still passes on a clean image and that the gate would fail on a seeded fixable CVE.
4. **Fix `genereviews` (3A).** Pin `trivy-action` to the fleet digest `@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0`; replace the non-gating SARIF step with the canonical 2-step gate. One PR.
5. **(Optional) Collapse the 4 Category C repos** to the 2-step form to kill the redundant second image scan. Pure consistency/perf; can be deferred without a conformance gap.
6. **(Optional) Normalize Category B** severity order to `CRITICAL,HIGH`. Cosmetic.
7. **Close each repo's Container-Hardening tracking issue** with the one-line CHANGELOG note required by the DoD (`CONTAINER-HARDENING-STANDARD-v1.md:210`); update the fleet conformance snapshot (universal gap #3) to reflect image-scan gating now in place.
8. **Decide Q4 follow-up later if needed.** If weekly scheduled red runs prove noisy, add an issue-heartbeat (mirror the router's `drift.yml` pattern) so scheduled findings open/refresh a tracking issue instead of only a red ✗ — not required for initial conformance.

Each step is one atomic commit / PR; no source-code TDD blocks are required (CI-config-only change), so the TDD-per-task rule is satisfied by "edit the workflow → push branch → observe the gate pass on clean image / fail on seeded CVE in CI" as the verification per PR.

---

## References

- Internal standard — `docs/CONTAINER-HARDENING-STANDARD-v1.md:129-130, 168, 208, 170-172` (mandates fail-on-fixable-HIGH/CRITICAL + SBOM; names the router as the reference).
- Evidence files (current code, 2026-06-30): `gnomad-link`/`hpo-link`/`orphanet-link` `.github/workflows/container-security.yml:30-44` (report-only); `gencc-link/.../container-security.yml:31-47` (canonical gate); `autopvs1-link/.../container-security.yml:55-62` (3-step gate); `genereviews-link/.github/workflows/container-security.yml:42-49` (unpinned + non-gating SARIF); `genefoundry-router/.github/workflows/` (no `container-security.yml`; `security.yml` is CodeQL + dependency-review only).
- Trivy GitHub Action (inputs, `exit-code` default `0`) — <https://github.com/aquasecurity/trivy-action>
- Trivy Action issue #309 — SARIF format vs `exit-code`/`severity` interaction — <https://github.com/aquasecurity/trivy-action/issues/309>
- Trivy docs — filtering (`--severity`, `--ignore-unfixed`) — <https://trivy.dev/docs/latest/configuration/filtering/>
- Trivy in CI/CD (gating recipes) — <https://trivy.dev/docs/latest/ecosystem/cicd/>
- Severity-filtering / fixable-only gating walkthrough — <https://oneuptime.com/blog/post/2026-01-28-trivy-severity-filtering/view>
- Container scanning + CI gating overview — <https://semaphore.io/blog/continuous-container-vulnerability-testing-with-trivy>

> Research use only. Not clinical decision support. This workstream changes CI workflows only and preserves every backend's existing disclaimers.
