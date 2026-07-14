# GeneFoundry README Standard v1

> Canonical reference for the `genefoundry-router` aggregator and the 21 `-link` MCP
> servers. Adopted 2026-07-14. Sibling of the
> [Tool-Naming](TOOL-NAMING-STANDARD-v1.md), [Response-Envelope](RESPONSE-ENVELOPE-STANDARD-v1.md),
> [Container-Hardening](CONTAINER-HARDENING-STANDARD-v1.md) and
> [Versioning](VERSIONING-STANDARD-v1.md) standards. Rationale and the audit that
> produced it: [`specs/2026-07-14-fleet-readme-standard-design.md`](specs/2026-07-14-fleet-readme-standard-design.md).

A README is the repository's **front door**, not its manual. Its only job is to get a
stranger from "what is this?" to "it works" — everything else belongs in `docs/`,
`AGENTS.md`, or the tool descriptions themselves.

## Why this standard exists

A 2026-07-14 audit scored all 22 fleet READMEs across ten dimensions. The fleet was strong
on orientation (7.3/10) and tool visibility (7.5/10) but failed on **badges (2.0)**,
**signal-to-noise (4.5)** and **scannability (4.7)**. Quality correlated inversely with
length (Spearman ρ = −0.79): the five shortest READMEs averaged 7.2/10, the five longest
4.0/10. Long READMEs had become unversioned operator runbooks that no reader scans and no
CI verifies.

Two facts were **already false** when the audit ran, which is why the load-bearing rules
below are machine-checked rather than merely written down:

- The router's README advertised a hardcoded `tests: 126 passing` badge and
  `21 backends, 280 tools`; the authoritative baseline said **272** (and `pubtator` 35,
  not 43). A hand-typed aggregate drifts the moment the fleet changes.
- Five repos (`clinvar`, `vep`, `mondo`, `hpo`, `orphanet`) declared `license = MIT` in
  `pyproject.toml` while shipping **no LICENSE file** — asserting a grant they never made.

## Rules

### 1. Length

Target **≤ 150 lines** of Markdown source; **hard ceiling 200** (CI-enforced). Only the
Tools table may grow with the server. The largest backend (`pubtator`, 35 tools) lands at
~110 lines under this skeleton, so the ceiling is comfortable, not aspirational. If a
section will not fit, it belongs in `docs/`.

### 2. Section order is fixed

Exactly these headings, in this order. Omit an optional section rather than reorder it.
No other H2s.

| # | Heading | Required | Budget | Purpose |
|---|---------|:--------:|-------:|---------|
| 1 | `# <repo-name>` (H1) | yes | 1 | Repo slug. Exactly one H1. |
| 2 | Badge row (no heading) | yes | 4 | The four fleet badges, one per line. |
| 3 | Lead paragraph (no heading) | yes | 3 | **What** it is, in one or two sentences. |
| 4 | Research-use callout (no heading) | yes | 3 | `> [!IMPORTANT]` safety banner, above the fold. |
| 5 | `## Why` | yes | 6 | **Why** it exists — the concrete problem it solves. |
| 6 | `## Quick start` | yes | 20 | Time-to-first-success. Copy-pasteable. |
| 7 | `## Tools` | yes | 8 + one row per tool | The exposed MCP surface. |
| 8 | `## Data & provenance` | yes | 12 | Upstream source, refresh model, data licence, citation. |
| 9 | `## Documentation` | yes | 10 | Introduced links to `docs/` and `AGENTS.md`. |
| 10 | `## Contributing` | yes | 4 | Link `AGENTS.md`; name the single gate. |
| 11 | `## License` | yes | 4 | Code licence **and** data licence. Always last. |

### 3. Badges: exactly four, in this order

Identity → health → assurance → legal. Slots 2 and 3 are **live GitHub Actions workflow
status**; slots 1 and 4 restate facts that `pyproject.toml` and CI already enforce.

**The 21 `-link` backends** (slot 3 = `conformance.yml`, the MCP Transport & Session
contract gate — present and `push`-triggered on all 21):

```markdown
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/<repo>/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/<repo>/actions/workflows/ci.yml)
[![Conformance](https://github.com/berntpopp/<repo>/actions/workflows/conformance.yml/badge.svg)](https://github.com/berntpopp/<repo>/actions/workflows/conformance.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
```

**The router** has no `conformance.yml`. Slot 3 is `security.yml` — its assurance gate at
the trust boundary, and `push`-triggered:

```markdown
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![CI](https://github.com/berntpopp/genefoundry-router/actions/workflows/ci.yml/badge.svg)](https://github.com/berntpopp/genefoundry-router/actions/workflows/ci.yml)
[![Security](https://github.com/berntpopp/genefoundry-router/actions/workflows/security.yml/badge.svg)](https://github.com/berntpopp/genefoundry-router/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
```

Rules for slot 3, stated so they cannot be misapplied:

- It MUST name a workflow that runs on **`push` to the default branch**. A status badge
  renders the default branch's most recent run; a `schedule`-only or
  `workflow_dispatch`-only workflow therefore reports a *stale or absent* run, not the
  current commit. The router's `drift.yml` and `fleet-probe.yml` are schedule-only **and**
  gated behind `vars.DRIFT_ENABLED` — they MUST NOT be badged.
- Do **not** append `?branch=`. Every fleet repo's default branch is `main` and the badge
  already defaults to it.
- The badge label MUST match what the workflow actually asserts. Do not label `drift.yml`
  "Conformance".

> A badge MUST link to the run it reports, and its truth MUST be maintained by a machine.
> A hand-typed badge is forbidden — the router's rotted `tests: 126 passing` is why.

The CI badge already attests that tests pass (`ci.yml` runs the suite), so a separate
test-count or coverage badge is redundant *and* rot-prone. No vanity metrics, no
self-awarded scores, no hand-typed counts.

### 4. State what it is before why it matters

The lead paragraph names the protocol, the data source, and the scope in one breath, and
byte-matches the GitHub repo description. No marketing adjectives, no emoji feature lists.

### 5. `## Why` justifies the wrapper

Every `-link` server wraps a resource that already exists on the public web; the README
must say what it adds. Name the concrete deficiency — `mgi-link` does it in one sentence:
*"the MGI gene page is a rich JS app with no clean JSON API."* This was the fleet's
weakest content dimension (5.1/10) and it is what a reader evaluating the project actually
needs.

### 6. `## Tools` lists every exposed tool

A two-column table (`Tool` | `Purpose`), one row per registered tool, plus the canonical
namespace token. This is the point of an MCP server README and the one section permitted
to grow with the server. Do **not** inline per-tool schemas or parameters — those live in
the tool descriptions.

**The table is machine-verified, not hand-maintained** (see [Enforcement](#enforcement)):
`tests/unit/test_readme_tools.py` asserts the table's tool names equal the server's
registered tools exactly. Adding a tool without updating the README fails CI.

**The router is different and MUST NOT list all 272 federated tools.** Its `## Tools`
section lists only the tools a model actually sees — `search_tools`, `call_tool`, and the
pinned per-backend entrypoints — followed by a **generated** backend inventory table
(namespace · domain · tool count · repo). The counts are produced from `servers.yaml` +
`fleet-baseline.json`, never typed.

### 7. The research-use disclaimer sits above the fold

A GitHub alert, in this fixed wording. It is a safety control, not a footer.

```markdown
> [!IMPORTANT]
> Research use only. Not clinical decision support. Do not use for diagnosis,
> treatment, triage, or patient management.
```

### 8. Every link must resolve

A README MUST NOT link to a file that does not exist. Two consequences, both enforced:

- **Relocation creates its destination in the same commit.** Moving a deployment runbook
  to `docs/deployment.md` means *writing* `docs/deployment.md` in that PR. Most fleet
  repos do not yet have `configuration.md` / `deployment.md` / `architecture.md` /
  `data.md`; do not link one before it exists.
- **Link `AGENTS.md`** as the contributor guide. It is present in all 22 repos and already
  *is* that guide. Do not reference a `CONTRIBUTING.md` the repo does not ship.

Every repo MUST ship the `LICENSE` file its `pyproject.toml` declares.

### 9. No hand-typed derived facts

**Aggregate or derived numbers are forbidden in prose and badges**: tool counts, test
counts, coverage percentages, benchmark scores, version numbers, status claims. They drift
silently because nothing reviews them.

An *enumeration* is not an aggregate: the Tools table lists names, and adding a tool is
already a reviewed, version-bumped event under the Tool-Naming and Versioning standards —
and Rule 6 puts a test behind it. Where a derived number genuinely must appear, it is
generated or guarded by a test.

## What does NOT belong in a README

Move it — do not delete it — unless the last column says otherwise. **Create the
destination file in the same PR.**

| Content | Destination |
|---------|-------------|
| Exhaustive environment-variable tables | `docs/configuration.md` / `.env.example` |
| Deployment & ops runbooks (Docker, systemd, reverse proxy, release, rollback) | `docs/deployment.md` |
| Host/Origin/CORS allowlist semantics, auth & OAuth setup | `docs/deployment.md` |
| Architecture deep-dives, pipeline diagrams, schema internals | `docs/architecture.md` |
| Make-target tours, dev-environment setup, test-layout notes | `AGENTS.md` |
| Per-tool schemas, parameters, response modes, error codes | tool descriptions / `docs/mcp-tool-catalog.md` |
| Data-bundle build procedure beyond the one-liner | `docs/data.md` |
| Changelog fragments, "Recent fixes", "Modern stack" sections | `CHANGELOG.md` |
| Roadmaps, speculative features, marketing prose, emoji feature lists | delete |
| Hand-typed counts, scores, status footers, stale badges | delete |

## Enforcement

Two gates, both already in `make ci-local`:

1. **`scripts/check_readme.py`** — a static linter, identical in every repo. Fails on:
   line count over ceiling; missing, reordered, or unexpected H2s; a badge row that does
   not match the canonical four for that repo class; a missing or reworded research-use
   callout; a relative link whose target does not exist; a hand-typed aggregate
   (`\d+ tools`, `\d+ (tests?|passing)`, `coverage: \d+`).
2. **`tests/unit/test_readme_tools.py`** — asserts the `## Tools` table matches the
   server's registered tools exactly, reusing the same tool-enumeration fixture the
   repo's existing `test_tool_names.py` already uses. In the router, it asserts the
   generated backend inventory matches `servers.yaml` + `fleet-baseline.json`.

The gates are the standard; this document is their rationale. A written convention with no
gate decays — this fleet has watched it happen.

## Boundary

Research use only. Not clinical decision support. Every fleet README mirrors its backend's
disclaimers.
