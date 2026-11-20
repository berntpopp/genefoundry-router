# Research workflow skills over MCP — design

Date: 2026-11-21 · Closes #336 · Status: approved for implementation

## Intent

Agents using GeneFoundry can find every tool, but on multi-step research jobs they pick the wrong
order, skip batching, re-check without end, cite PMIDs they never retrieved, and present gaps as
negatives. Benchmark data (10+ models): phenotype-to-gene costs 200k+ tokens per case and open
models hit the message cap on 12–58% of cases. Multi-hop lookups lose points because models never
stop. A skill encodes what the tool catalog cannot: step order and batching, SOP rules, stop rules
and failure states, and an auditable output template.

Research use only. Not clinical decision support.

## Goals

1. The router serves `skills/` over MCP: SEP-2640 (`skills/list`, `skills/get`) and `skill://`
   resources, plus a `search_tools` hit that points to the skill (hosts barely load MCP skills yet).
2. Ship a first batch of six new skills, each A/B tested against the benchmark harness.
3. CI gates that keep skills honest: frontmatter, size cap, every tool name a skill mentions exists
   in the catalog, and the shared rules block is identical in every skill.

## Non-goals

- MCP prompts as slash commands (later, if hosts keep lagging).
- Skills forwarded from backends (namespacing hides them; serve from the router only).
- Any patient-data workflow. Skills require de-identified input.
- Jurisdiction-specific legal claims in skill text beyond what was verified and approved.

## Anti-goals (a failure even if it works)

- A skill that restates tool docs ("call X then Y"): fix the tool description instead.
- A skill that lets the model assert a fact no tool returned, or turn "not found" into "negative".
- Growing the pinned tool listing (strict budget has ~10 tokens of headroom).

## Approaches considered

- **A. One skill per professional job (chosen).** Matches the agentskills.io spec, SEP-2640
  per-skill listing, anthropics/skills and Superpowers; each skill gets its own search hit and A/B.
- **B. One entry skill with modes** (Impeccable, academic-research-skills, K-Dense consolidation).
  Used *inside* a skill where variants share most checks: `genetic-report-review` has one
  `references/<type>.md` per report type; `germline-variant-classification` has references per
  evidence type.
- Sizing: 8–12 skills total for v1, hard cap ~15. Body ≤ ~300 lines, under 20 kB; detail in
  `references/` loaded on demand.

## Router change (decided)

- `config.py`: `GF_SKILLS_DIR: str = "skills"`.
- `server.py` `build_server`: `server.add_provider(SkillsDirectoryProvider(settings.GF_SKILLS_DIR))`
  after the backend mount loop, using `fastmcp.skills.provider.SkillsDirectoryProvider` (the
  `fastmcp.server.providers.skills` class is deprecated in 4.2, removed in 4.3). A missing directory
  must fail loudly (the provider serves zero skills silently).
- `tool_search.py`: each skill becomes a synthetic BM25 document (name `skill_<name>`, text = name +
  description). A skill hit serializes as `{"name": "skill_<name>", "skill": "skill://<name>/SKILL.md",
  "description": ..., "read": {"method": "resources/read", "uri": ...}}` with no `call` key. It never
  enters the call catalog, drift checks or conformance. Same mapping in compact/full/strict modes.
  Do not grow the `search_tools` docstring (listing budget).
- `instructions.py`: one early sentence: workflow skills exist as `skill://` resources and show up in
  `search_tools`.
- `docker/Dockerfile`: `COPY skills ./skills` and extend the existing build-time assert to require
  at least one `skills/*/SKILL.md`. Do not put skills under `genefoundry_router/data/`.
- Tests: provider wiring (`resources/list`; `skills/list` on the 2026-07-28 protocol), search returns
  the skill pointer, pinned-listing budget unchanged, update the three `list_resources() == []`
  assertions in `tests/integration/test_protocol_beyond_tools.py`.
- `scripts/check_skills.py` + `make lint-skills` (in `ci-local`): frontmatter per agentskills.io,
  folder = name, SKILL.md < 20 kB, every backticked `<namespace>_<tool>` name exists in the catalog,
  the shared rules block (below) is byte-identical to the canonical copy in `skills/README.md`.
- Version bump, CHANGELOG, `skills/README.md` updated.

## Shared rules block (canonical, copied into every SKILL.md)

```
## Evidence rules (all GeneFoundry skills)

- Research use only. Not clinical decision support. Use de-identified input only; if the input
  contains names, dates of birth or record numbers, stop and ask for a de-identified version.
- Every fact comes from a tool result in this session. Cite the record id the tool returned
  (PMID, VCV, HGNC id, MONDO id, ...). Never cite from memory.
- Give every checked claim one state: **confirmed** (tool agrees), **contradicted** (tool disagrees;
  show the evidence), **not confirmable** (tool returned nothing or failed), **not checked**.
  "Not found in source X" never means "negative" or "benign".
- State genome build and transcript before any variant lookup. Resolve identifiers once, in batch.
- Scores, points and risks are added up step by step by the stated rule, never estimated.
- If a call errors or is rate limited, do not repeat the identical call: use another source or
  report the gap. Stop when the skill's stop rule is met.
- End with: tools used and the data versions they report.
```

## First batch (six new skills + existing somatic)

| Skill | Persona | Core content | Source of rules |
|---|---|---|---|
| `identifier-resolution-and-lookup` | anyone, bioinformaticians | batch-resolve genes/variants/diseases/phenotypes; build handling; multi-hop with stop rule (two sources agree, or a call budget then state what is missing) | benchmark failure analysis |
| `phenotype-to-candidate-genes` | rare-disease clinician/researcher | HPO resolve → batch HGNC → ONE `hpo_score_genes_for_phenotypes` with candidates → close_call tie-break via GenCC/ClinGen → stop | benchmark; draft already in A/B |
| `germline-variant-classification` | variant scientist | ACMG/AMP 2015 + ClinGen SVI + VCEP specs; points by rule (Tavtigian 2020); double-count rules; references/: pvs1, splicing, functional (MAVE), mitochondrial, repeat-expansion | Richards 2015 (25741868), Tavtigian 2020 (32720330), Horak not here |
| `genetic-report-review` | clinical geneticist (second look before sign-out) | anti-anchoring first pass (form own expectation from the indication); identity/consistency; per-variant re-derivation; citation-to-allele check; gene–disease and inheritance math; what was not tested; recommendations; freshness vs report date; verdict + Critical/Major/Minor; references/: germline, cnv, negative, somatic, pgx, predictive-segregation | Deans 2022 (35361922), Claustres 2014 (23942201), Matthijs 2016 (26508566), Richards 2015, Riggs 2020 (31690835), Li 2017 (27993330), Horak 2022 (35101336), Caudle 2017 (27441996), Deignan 2019 (31015575), HGVS (Hart 2024, 39702242), MANE (35388217); EQA error classes (Tack 2016, Vears 2021, Mersch 2018) |
| `literature-evidence-review` | researcher, curator | gene/variant/disease evidence synthesis; every PMID verified to the specific allele or gene by a tool; "new since <date>" surveillance mode; curated sources before free literature for facts | LLM citation-fabrication evidence |
| `lay-summary` | genetic counsellor | gene/disease/variant explained for patients; reading level ~grade 8; VUS never worded as a cause; no new facts beyond the tool-verified input; questions for your clinician | readability literature |

Report-review specifics decided with the owner:
- Secondary findings are checked only for consistency with documented consent and the lab's stated
  policy. No jurisdiction-specific rule about which gene list to report.
- The review never rewrites HGVS, gene names, ACMG class or OMIM ids; it flags.
- Output: verdict (concur / concur with comments / do not sign out yet), findings Critical / Major /
  Minor each with fix + tool evidence, a "checks out" list and a "not checked / needs human" list.
- German context limited to verified, uncontroversial items: genetic counselling referral wording,
  report addressed to the ordering physician, prenatal reports without adult-onset findings (GenDG
  §§10, 11, 15). Clause numbers of ISO 15189 / CAP / GfH S1 are not cited (unverified).

Batch 2 (not in this change): `gene-disease-validity-curation`, `case-reanalysis`,
`cnv-interpretation`; P2 `pgx-report`, `gene-panel-design`.

## Quality gate per skill

1. `make lint-skills` passes.
2. Trigger: 2–3 realistic queries per skill added to the discoverability set rank the skill in top 5
   without pushing an expected tool out of top 5 for existing tasks.
3. A/B in the benchmark harness (local GPU/cluster, no paid runs): skill text in context vs not, paired
   per item on development items only. Ship if not worse on accuracy and better or equal on
   tokens/calls; otherwise revise. Skills without a matching benchmark suite (report review, lay
   summary) get a small hand-built scenario set and pressure tests (patient data pasted in;
   "clinician needs it today"; a planted wrong PMID and wrong HGVS in a synthetic report).

## Left to the builder

BM25 weighting of skill documents, exact test layout, the scenario fixtures (synthetic reports only),
and wording of skill bodies within the rules above.
