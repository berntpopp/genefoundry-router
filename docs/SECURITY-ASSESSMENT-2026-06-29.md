# GeneFoundry Router & `-link` Fleet — Security Assessment & Hardening Roadmap

- **Date:** 2026-06-29
- **Author:** security review (router + full fleet audit + Anthropic/Google/MCP-spec/GDPR research)
- **Audience:** Charité informatics / information-security & data-protection (DSB)
- **Scope:** `genefoundry-router` (the gateway) + all 21 registered `-link` backends federated behind it
- **Status:** Draft for infosec review. Research use only; not clinical decision support.

> **One-paragraph summary.** The GeneFoundry fleet is a set of read-only MCP servers over **public**
> genomics reference databases, federated by one gateway. A full source audit of the router and all
> 21 backends found **no malicious code, no data exfiltration, no telemetry/phone-home, no hidden
> network destinations, and no dangerous dynamic-code execution** — the "Schweinereien" concern is
> not borne out by the code. The router is, by MCP standards, **above-average secure by design** (no
> token passthrough, audience-bound auth wired, Origin validation, a hardened container). The real,
> legitimate risks are **operational and data-protection**, not malice: (1) the public endpoint
> ships **unauthenticated by default**; (2) "read-only" does **not** mean "safe" in an agentic
> context — returned text and tool descriptions are injection surfaces; and (3) the **queries** a
> clinician types (a patient's variant + phenotype) are **GDPR Art. 9 health/genetic data** even
> though the *answers* are public. The recommended path is **self-hosting the whole stack on-prem /
> in the EU at Charité**, which simultaneously dissolves the "external server" fear and resolves the
> data-protection problem. This document gives the evidence, the threat model, and a prioritized
> roadmap.

---

## 1. What this is, and where the trust boundaries are

```
 clinician / researcher
        │  prompt (MAY contain patient variant+phenotype = Art. 9 data)
        ▼
   LLM host (Claude / Claude Code / ChatGPT)         ← holds OTHER tools too (trifecta risk)
        │  Streamable HTTP + (today) NO auth
        ▼
   genefoundry-router  ── edge auth, namespacing, tool-search, NO token passthrough
        │  router's OWN connection (caller token never forwarded)
        ▼
   21 × <name>-link backends  ── read-only, unauthenticated by design, behind the proxy
        │  fixed upstream host per backend (never built from a tool argument)
        ▼
   public reference APIs / bundled DBs (gnomAD, ClinVar, Ensembl, NCBI, HPO, …)
```

Two facts drive everything below:
1. **The data returned is public; the prompt may not be.** The regulated asset is the *query*, not the answer.
2. **The trust boundary is the router.** Backends are intentionally open and must be reachable **only** through the router/reverse proxy — never exposed directly.

---

## 2. The "Schweinereien" question, answered with evidence

We audited the router (first-hand) and **all 21 backends** (gnomad, gtex, hgnc, mgi, uniprot, clingen,
gencc, litvar, stringdb, autopvs1, spliceai, genereviews, pubtator, clinvar, vep, panelapp, mondo,
mavedb, hpo, metadome, orphanet) with parallel reviewers. Method: locate entrypoints/tools/config/
Docker, grep for danger patterns (`eval`/`exec`/`os.system`/`subprocess`/`shell=True`/`pickle`/unsafe
`yaml.load`/`__import__`/base64 blobs), and trace egress, secrets, SQL, and returned-text handling.

**Verdict: CLEAN across the entire fleet.** Specifically:

| Concern | Finding |
|---|---|
| Backdoors / hidden exfiltration / phone-home | **None.** No telemetry export; "telemetry" modules are in-process timers/Prometheus counters only. |
| Dynamic / obfuscated code execution | **None** in any serving path. `base64` is only opaque pagination cursors (decoded via `json.loads`, never `pickle`/`eval`). |
| `subprocess` use | Only **build-time/operator tooling** (`gh`, `pg_dump`/`pg_restore`, benchmark CLIs) with argument lists, never `shell=True`, never in the request path. |
| SSRF (server fetching attacker-chosen hosts) | **Not possible** — every upstream base URL is a fixed config constant; user input enters only as validated query params or percent-encoded single path segments. The one user-URL feature (pubtator `curated_urls`) is gated through a strong `SafeUrlFetcher` (scheme+host allowlist, private-IP rejection, redirect re-validation, byte caps). |
| SQL / FTS injection | **None** — uniformly parameterized (`?` / asyncpg `$1`); FTS tokens quoted/escaped; the few f-string SQL sites interpolate only fixed column/table names. |
| Committed secrets | **None** — `.env*` in git are placeholders; real `.env` is git-ignored; no secrets baked into images. |
| Supply chain | Mainstream deps, version-bounded, `uv.lock` frozen, no git/URL/typosquat deps, no post-install hooks. |

This is the single most important message for infosec: **the code is conservative and was clearly
threat-modeled** (SSRF allowlists, parameterized SQL, `defusedxml`, hardened tar extraction, masked
errors, and — in pubtator — CodeQL + Trivy + dependency-review in CI). The residual risks below are
posture and data-protection, *not* malice.

---

## 3. The key correction: "read-only" ≠ "safe"

Your colleagues' instinct ("it only reads data, so what could go wrong?") is the one thing the
research literature unanimously contradicts. Even a benign, read-only MCP server participates in real
attack classes, because in an agentic setup the model treats **tool descriptions and returned text as
input it may act on**:

- **The "lethal trifecta"** (Willison; Anthropic): a session that simultaneously has (1) access to
  private data, (2) exposure to attacker-controllable content, and (3) any outbound channel can be
  turned into an exfiltration path **with no code vulnerability**. A read-only literature server is a
  perfect *source of untrusted content* (leg 2). Anthropic states plainly: *"Tool output is an attack
  surface even when the tool is trusted."* ([how-we-contain-claude](https://www.anthropic.com/engineering/how-we-contain-claude))
- **Tool poisoning / "line jumping"**: instructions hidden in a tool *description* are read by the
  model during planning — the tool need not even be called. ([Invariant Labs](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks))
- **No vendor security-audits the servers you connect.** Anthropic: *"Anthropic … does not
  security-audit or manage any MCP server."* Listing ≠ safety. ([Claude Code security](https://code.claude.com/docs/en/security))

For THIS fleet the realized risk is **bounded** (read-only, public data, no write paths on most
backends, no secrets to steal), but the defenses still matter — and two backend specifics raise it
(§5). The durable control, per Anthropic and Google alike, is a **deterministic environmental
boundary** (auth + network egress allowlist + isolation), not a model-layer classifier that is "never
100%."

---

## 4. Threat model → controls (mapped to authoritative guidance)

| Threat (applies to read-only?) | Control | Status here | Source |
|---|---|---|---|
| Indirect prompt injection via returned text — **yes, primary risk** | Treat returned text as data; wrap/fence with provenance; host isolation | Partial — advisory note only, no content fencing (§5) | [OWASP LLM01](https://genai.owasp.org/llm-top-10/), [Anthropic](https://www.anthropic.com/engineering/how-we-contain-claude) |
| Tool poisoning / rug-pull (desc changes after approval) — **yes** | Pin tool definitions by hash; alert on drift | **Gap** — `snapshot_fleet.py` captures the manifest but no drift gate yet | [Invariant](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks), [OWASP MCP Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html) |
| Confused deputy / token passthrough — gateway anti-pattern | Never forward caller token; per-backend credential | **Done** (R1.6 invariant, enforced in `auth.py`/`composition.py`) | [MCP auth spec](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization) |
| Unauthenticated endpoint / excessive exposure | Edge auth; audience-bound tokens | **Wired but OFF by default** (§5 P0) | MCP auth spec |
| DNS rebinding (Streamable HTTP) | Validate `Origin`; bind loopback | **Done** (`security.py`) | [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports) |
| SSRF to internal services | Block private IPs; fixed hosts | **Done** (fixed hosts; pubtator `SafeUrlFetcher`) | MCP best practices |
| Resource exhaustion / DoS (LLM10 unbounded consumption) | Inbound rate-limit; body/size caps; input caps | **Gap** at router + most backends | [OWASP LLM10](https://genai.owasp.org/llm-top-10/) |
| Sensitive info disclosure (PHI) (LLM02) | No PII in logs; egress filter; data minimization | **Gap** — some backends log query path/params (§5) | [OWASP LLM02](https://genai.owasp.org/llm-top-10/) |
| Supply chain (LLM03) | Pinned/scanned images + deps; SBOM | Partial — deps locked; **no digest pin / no image scan** (most) | [OWASP LLM03](https://genai.owasp.org/llm-top-10/) |

Google's agent-security model says the same in different words: **well-defined human controllers**,
**limited agent powers (least privilege)**, **observable/auditable actions**, and a **hybrid
defense** (deterministic policy engine + reasoning-based guards) because reasoning-based defenses
"cannot provide absolute guarantees." ([Google](https://research.google/pubs/an-introduction-to-googles-approach-for-secure-ai-agents/), [SAIF](https://blog.google/innovation-and-ai/technology/safety-security/introducing-googles-secure-ai-framework/))

---

## 5. Current posture — strengths and gaps

### 5.1 Strengths (lead with these in the infosec conversation)
- **No token passthrough** to backends (confused-deputy defense) — explicit and enforced.
- **Modern MCP auth wired**: audience-bound `JWTVerifier`, Protected Resource Metadata (RFC 9728),
  `WWW-Authenticate`, OAuth proxy (`jwt`/`oauth` modes) — just not enabled by default.
- **Origin validation** (DNS-rebinding MUST); **Streamable HTTP only** (no SSE).
- **Hardened router container**: non-root uid 10001, read-only rootfs, `cap_drop: ALL`,
  `no-new-privileges`, `init`, mem/cpu/pids limits, tmpfs `noexec,nosuid`, expose-only behind the
  proxy (`ports: !reset []`), secrets kept out of the image, pinned deps (`uv.lock --frozen`), ruff
  `S` security lint.
- **Fleet code is clean and careful** (§2); fixed egress; parameterized SQL; masked errors; "research
  use only" disclaimers pervasive.

### 5.2 Gaps (prioritized)

**P0 — before any colleague connects (router):**
1. **Open, unauthenticated public endpoint.** `genefoundry.org/mcp` runs `GF_AUTH_MODE=none`; the
   Docker image forces `--host 0.0.0.0`. Anyone can use the fleet and your VPS as an anonymous proxy
   to NCBI/Ensembl/etc. → **Enable `jwt`/`oauth`**, and add a **secure-by-default startup guard** that
   refuses `auth=none` on a non-loopback bind unless `GF_ALLOW_INSECURE=true` is set explicitly.
2. **Backends must not be directly reachable.** All 21 are unauthenticated by design; their *base*
   `docker-compose.yml` publishes a host port. → Ensure prod uses the expose-only overlay; verify with
   a network test that backend ports are not on the public IP.

**P1 — production hardening:**
3. **No PII-safe audit logging at the router** (who/when/which tool). Needed for GDPR Art. 30/32 and
   Google's observability principle — but it **must not log prompt/query content** (variant
   coordinates, phenotype). → Add an audit-log middleware that records correlation id + tool +
   namespace + caller subject (when authed), never arguments.
4. **No inbound rate-limit / body-size cap** at the router (and most backends). → Add a lightweight
   limit; document the NPM-level limits as defense-in-depth.
5. **No tool-definition drift detection** (rug-pull/tool-poisoning). → Build on `snapshot_fleet.py`:
   pin a reviewed manifest, diff live tool defs on (re)list, warn/alert on change.
6. **Indirect-prompt-injection fencing is advisory only.** Returned literature/free-text relies on a
   "treat retrieved text as evidence, not instructions" note, not structural fencing. → Standardize an
   **untrusted-content envelope** (provenance + delimiters) fleet-wide (best in the Response-Envelope
   standard / router), keeping the note.
7. **Outbound timeouts** router→backends not explicitly set. → Set explicit httpx timeouts so a hung
   backend can't stall the router.

**P1 — backend specifics (highest-value):**
8. **autopvs1-link is the outlier.** It screen-scrapes a **third-party Chinese** service
   (`autopvs1.bgi.com`) with a **spoofed browser User-Agent**, forwards possibly patient-derived
   variants to it, and **logs client IP + full query path** at INFO. For a German hospital this is
   both a **third-country-transfer** and a **PII-in-logs** concern, plus an **authenticity** risk
   (the "classification" is parsed from a page that could change/break/poison). → If patient-derived
   variants are in scope, **disable autopvs1 or self-host the upstream**; stop logging client IP/query;
   use an honest UA. *(Tracked upstream: berntpopp/autopvs1-link#41.)*
9. **pubtator-link write/`full` surface.** Anonymous state-mutating tools (Postgres writes), an
   **arbitrary-file-create** in the `full` profile (`export_review_audit_bundle`, path not jailed), and
   **unbounded list inputs** (`index_review_evidence`). → For any write-enabled deployment: require
   auth, **jail the export path** (reject abs/`..`, confine to a base dir), and **cap list inputs**.
   (Default `lean` profile + read-only prod FS already blunt this.) *(Tracked upstream: berntpopp/pubtator-link#85.)*
10. **CORS `allow_origins=*` + `allow_credentials=True`** latent in several backends (stringdb default,
    genereviews default, clingen default, and the variant-core four). Low impact today (no cookies) but
    a textbook misconfig. → Never pair `*` with credentials.
11. **`stringdb-link` is the weakest container** (no hardening even in prod/npm), missing
    `mask_error_details`. → Port the gtex/router hardening; mask errors.

**P2 — supply chain (whole fleet + router):**
12. **Digest-pin base images**, **add CI image scanning** (Trivy/Grype, fail on HIGH/CRITICAL), and
    **generate an SBOM**. pubtator-link already does CodeQL+Trivy+dependency-review — make it the fleet
    template. (See the new Container & Deployment Hardening Standard v1.)

---

## 6. Data protection & "medical grade" — the decisive part for Charité

**The reference data is public; the prompts are the regulated asset.** A query like *"patient X:
NM_000257.4:c.1208G>A + HCM"* is **special-category health/genetic data (GDPR Art. 9(1))**, prohibited
to process unless an Art. 9(2) exception **and** an Art. 6 basis both apply. Genomic data is **not
safely anonymisable** — GA4GH: *"researchers should never assume that genomic data are anonymous"*;
re-identification is possible from ~25 variants. So a variant+phenotype string is, at best,
*pseudonymised* personal data — still in scope. ([Art. 9](https://gdpr-info.eu/art-9-gdpr/),
[GA4GH](https://www.ga4gh.org/news_item/can-genomic-data-be-anonymised/), [Recital 26](https://gdpr-info.eu/recitals/no-26/))

### 6.1 Hosting topology — the gating decision (recommendation)

You asked me to recommend. **Recommendation: self-host the whole stack (LLM inference + router +
`-link` backends) on-prem or in an EU data centre under Charité's control (Topology 1).** Keep the
public `genefoundry.org` instance for public/non-patient research and external collaborators.

| GDPR / German obligation | Topology 1 — self/EU-hosted (recommended) | Topology 2 — external LLM/SaaS |
|---|---|---|
| Art. 9 health/genetic data | stays inside the controller's systems → easiest to justify | a third party now processes Art. 9 data → far higher scrutiny |
| Art. 28 processor / **AVV** | often none needed (internal) | **mandatory AVV** with each external processor |
| Art. 44–49 international transfer | **not triggered** | needs adequacy/SCCs + transfer-impact assessment |
| US CLOUD Act residual risk | none | persists even on EU servers if vendor is US-controlled — a frequent DSB rejection ground |
| §203 StGB medical confidentiality | preserved (internal) | needs §203(3) safeguards; criminal-liability risk |
| §393 SGB V + **BSI C5** | if cloud: EU + C5-attested | same C5 + harder with non-EU SaaS |
| Approvability by Uniklinik DSB | **high** | low–medium; needs identifier-stripping + heavy compensating controls |

Self-hosting also **directly answers the "external server" fear**: nothing leaves the hospital network.
If an external LLM is ever unavoidable, route only de-identified queries (gateway PII-egress filter),
EU-hosted, under an AVV, with no US control — and treat it as the high-risk path.
([German cloud-health baseline](https://hashtagpraxis.com/2025/06/gesundheitsdaten-in-der-cloud-datenschutz-rechtliche-vorgaben-und-cloud-loesungen-fuer-therapiepraxen/),
[§203/cloud](https://www.datenschutz-notizen.de/cloud-computing-im-gesundheitswesen-vereinbar-mit-der-aerztlichen-schweigepflicht-5738749/),
[BSI C5](https://www.pwc.de/en/risk-regulatory/cloud-usage-in-healthcare-with-bsi-c5.html))

### 6.2 Staying "research use only" (out of medical-device regulation)

Software becomes **Medical Device Software** under **MDR 2017/745** when it has an *individual medical
intended purpose* (diagnosis/treatment for a specific patient); that can then also make it **high-risk
AI** under **AI Act Annex III §5(a)**. The fleet's read-only/reference-only design + "not clinical
decision support" disclaimers are what keep it out of that regime — **protect them**: position it as a
research/literature/annotation assistant, keep clinical interpretation a **human act in the validated
clinical pipeline**, and put a written **intended-purpose statement** on file that says any move to
clinical decision support re-opens the MDR/AI-Act assessment. As a pure research tool the AI Act is
light-touch (Art. 2(6) research exemption; otherwise Art. 50 "tell users it's AI"). ([MDCG 2019-11](https://health.ec.europa.eu/system/files/2020-09/md_mdcg_2019_11_guidance_en_0.pdf),
[AI Act Art. 2](https://artificialintelligenceact.eu/article/2/), [Art. 50](https://artificialintelligenceact.eu/article/50/))

### 6.3 Frameworks to make approval defensible
Map technical controls to **Google SAIF** + the **agent-security principles**; map management controls
to **ISO/IEC 27001** (ISMS + Statement of Applicability) and **ISO/IEC 42001** (AI management system) /
**NIST AI RMF**. HIPAA only matters if US partners process PHI (then a BAA is required) — for a
German-only deployment GDPR/BDSG governs.

---

## 7. Infosec / DSB approval checklist

**Organizational / legal**
- [ ] Signed **intended-purpose statement** ("research/annotation over public reference data; not CDS; not a medical device").
- [ ] **DPIA (Datenschutz-Folgenabschätzung)** completed + accepted by the DSB before go-live (large-scale Art. 9).
- [ ] **Art. 30 record of processing** + an end-to-end **data-flow diagram** (what leaves the boundary, to where).
- [ ] **Legal basis** documented: Art. 6 + Art. 9(2)(h)/(j) and/or **explicit consent (9(2)(a))**.
- [ ] **AVV (Art. 28)** with every external processor; **no third-country transfer** (or adequacy/SCCs + TIA).
- [ ] **BSI C5** verified for any cloud component touching health data; **§203 StGB** safeguards in place.
- [ ] **Supply-chain review** (LLM provider + router + each backend's `uv.lock`; SBOM; model-weight provenance).
- [ ] ISMS aligned to **ISO 27001**; AI governance to **ISO 42001 / NIST AI RMF**; incident-response + breach-notification runbook (Art. 33/34).

**Technical**
- [ ] **On-prem / EU-hosted** topology for LLM + router + backends.
- [ ] **Edge auth** on the router (`jwt`/`oauth`); **no token passthrough** (already enforced); scoped per-backend credentials.
- [ ] Backends **expose-only behind the proxy**, never on the public IP.
- [ ] **Egress allowlist** + (if any external call) a **PII-egress filter** so patient identifiers never leave the boundary.
- [ ] **Audit logging with NO prompt PII**; encryption in transit + at rest; tested restore/DR; periodic pen-test.
- [ ] **Rate limiting / input caps**; **tool-definition drift detection**; **prompt-injection defense-in-depth** (treat retrieved text as data + deterministic guardrails ± reasoning-based scanning).
- [ ] **Container hardening** per `CONTAINER-HARDENING-STANDARD-v1.md` (digest-pin, image scan, SBOM, read-only, cap-drop).
- [ ] In-product **disclaimers + AI-interaction notice** (Art. 50; "research use only / not CDS").

---

## 8. Prioritized roadmap (sequenced)

1. **P0 router:** enable auth + secure-by-default guard; confirm backends are not publicly reachable. *(guard: started — §9)*
2. **P1 router:** PII-safe audit logging → rate-limit/body cap → outbound timeouts → tool-definition drift gate.
3. **P1 backends:** autopvs1 third-country/PII/authenticity decision; pubtator write/`full` auth + path-jail + input caps; CORS fix; stringdb hardening + error masking.
4. **P1 injection:** untrusted-content envelope fleet-wide (Response-Envelope standard).
5. **P2 supply chain:** digest-pin + image scan + SBOM across the router and all backends (pubtator CI as template).
6. **Org track (parallel):** DPIA, data-flow diagram, intended-purpose statement, AVVs, ISO/SAIF mapping.

---

## 9. Sources

**MCP / Anthropic / threat landscape:** MCP Security Best Practices and Authorization spec
(modelcontextprotocol.io, 2025-06-18 / 2025-11-25); RFC 9728; RFC 8707; Anthropic
[Claude Code security](https://code.claude.com/docs/en/security),
[how-we-contain-claude](https://www.anthropic.com/engineering/how-we-contain-claude),
[claude-code-sandboxing](https://www.anthropic.com/engineering/claude-code-sandboxing),
[building-effective-agents](https://www.anthropic.com/research/building-effective-agents),
[framework for safe & trustworthy agents](https://www.anthropic.com/news/our-framework-for-developing-safe-and-trustworthy-agents);
[Invariant tool-poisoning](https://invariantlabs.ai/blog/mcp-security-notification-tool-poisoning-attacks);
[lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/);
[OWASP LLM Top 10 (2025)](https://genai.owasp.org/llm-top-10/);
[OWASP MCP Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html).

**Google / compliance:** [SAIF](https://blog.google/innovation-and-ai/technology/safety-security/introducing-googles-secure-ai-framework/);
[Google agent security](https://research.google/pubs/an-introduction-to-googles-approach-for-secure-ai-agents/);
[Model Armor + MCP](https://docs.cloud.google.com/model-armor/model-armor-mcp-google-cloud-integration);
GDPR [Art. 9](https://gdpr-info.eu/art-9-gdpr/) / [25](https://gdpr-info.eu/art-25-gdpr/) /
[28](https://gdpr-info.eu/art-28-gdpr/) / [32](https://gdpr-info.eu/art-32-gdpr/) /
[35](https://gdpr-info.eu/art-35-gdpr/) / [Chapter V](https://gdpr-info.eu/chapter-5/) /
[Recital 26](https://gdpr-info.eu/recitals/no-26/);
[GA4GH genomic (non-)anonymity](https://www.ga4gh.org/news_item/can-genomic-data-be-anonymised/);
[MDCG 2019-11](https://health.ec.europa.eu/system/files/2020-09/md_mdcg_2019_11_guidance_en_0.pdf);
[AI Act Art. 2](https://artificialintelligenceact.eu/article/2/) / [Art. 50](https://artificialintelligenceact.eu/article/50/);
[BSI C5](https://www.bsi.bund.de/EN/Themen/Unternehmen-und-Organisationen/Informationen-und-Empfehlungen/Empfehlungen-nach-Angriffszielen/Cloud-Computing/Kriterienkatalog-C5/kriterienkatalog-c5_node.html);
[ISO 27001](https://www.isms.online/iso-27001/annex-a-2022/) / [ISO 42001](https://www.iso.org/standard/42001) /
[NIST AI RMF](https://nvlpubs.nist.gov/nistpubs/ai/nist.ai.100-1.pdf).

**Sibling standards:** `CONTAINER-HARDENING-STANDARD-v1.md`, `TOOL-NAMING-STANDARD-v1.md`, `RESPONSE-ENVELOPE-STANDARD-v1.md`.
