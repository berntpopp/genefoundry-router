# P0 Dependabot completion and fleet release-truth design

> Historical record — this document records the approved execution design as of 2026-07-19.
> Current behavior is defined by merged code, immutable release evidence, GitHub state, and tests.

**Status:** approved operational scope; reconciled against GitHub at 2026-07-19 12:55 UTC.

## Goal and guardrails

Complete the remaining P0 Contract Truth work and every open Dependabot update across the router
and its 21 registered backends without converting a green check into an unreviewed security claim.
All changes use an isolated worktree and a non-`main` branch. A merge requires fresh required
checks, a reviewed diff, and immutable evidence where the change affects a release claim.

The router P0 implementation is already merged as router PR #103 (`2e27a1b`); it is versioned
0.7.0 but has neither a `v0.7.0` tag nor a GitHub release. That publication is out of scope.
Issue #63 remains open pending an observed published ClinGen runtime identity. Issue #68 remains
open pending the twelve blocked Contract Truth adoptions. Issue #79 remains open: its protected
`main` bootstrap and `control-audit` environment/token need a repository administrator and are not
to be bypassed or simulated.

## Reconciled Dependabot inventory

The router and all 21 backends were enumerated from `servers.yaml`; local sibling paths were
accepted only after their `origin` remote matched that configured repository. At the timestamp
above, there are 23 open Dependabot PRs. `mcp` is transitive through the direct FastMCP dependency
unless otherwise noted. All MCP 1.28.1 changes address GHSA-vj7q-gjh5-988w / CVE-2026-59950. The
1.27.1 cases also address GHSA-jpw9-pfvf-9f58 / CVE-2026-52869 and
GHSA-hvrp-rf83-w775 / CVE-2026-52870.

| Repository | PR | Change and scope | Current decision |
| --- | --- | --- | --- |
| router | none | no open Dependabot work | no action |
| autopvs1, gnomad, gtex, litvar | none | no open Dependabot work | no action |
| clingen | #52 | Python 3.14-slim digest; production container base | provenance/digest review then merge |
| clingen | #53, #56 | reusable release/CI workflow SHA `2f62…` → `2e27…` | adapt stale SHA assertion with TDD; update, do not merge unchanged |
| clingen | #54 | `actions/attest-build-provenance` pinned SHA | action provenance review then merge |
| clingen | #55 | FastAPI, Typer, Ruff, mypy; direct prod/dev | import, CLI, type smoke then merge |
| clinvar | #30 | MCP 1.27.2 → 1.28.1; lockfile-only production resolution | security-first P0 unblock |
| gencc | #44, #45 | reusable workflow pins to obsolete `0a625…` | consolidate to `2e27…`; close only after replacement is pushed |
| gencc | #46 | FastAPI, Typer, Ruff, mypy; direct prod/dev | import, CLI, type smoke then merge |
| genereviews | #111 | MCP 1.27.1 → 1.28.1; lockfile-only production resolution | security-first P0 unblock |
| hgnc | #30 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| hpo | #32 | MCP 1.28.0 → 1.28.1; lockfile-only | security-first P0 unblock |
| mavedb | #37 | MCP 1.28.0 → 1.28.1; lockfile-only | security-first P0 unblock |
| metadome | #25 | MCP 1.28.0 → 1.28.1; lockfile-only | security-first P0 unblock |
| mgi | #32 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| mondo | #29 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| orphanet | #32 | MCP 1.28.0 → 1.28.1; lockfile-only | security-first P0 unblock |
| panelapp | #29 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| pubtator | #137 | Torch 2.12.1 → 2.13.0; direct production | separate major runtime/ABI compatibility review |
| spliceailookup | #28 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| stringdb | #37 | MCP 1.27.1 → 1.28.1; lockfile-only | security-first P0 unblock |
| uniprot | #33 | MCP 1.27.2 → 1.28.1; lockfile-only | security-first P0 unblock |
| vep | #27 | MCP 1.27.2 → 1.28.1; lockfile-only | diagnose failed behaviour probe before any merge |

The first nine Contract Truth adoptions are merged. The remaining twelve are blocked by the
fixable MCP scan finding, except that their preceding Dependabot work may itself need refreshed
checks. Dependabot green checks dated before a `main` advance are stale evidence, so each candidate
is rebased or re-run before merge. No PR is merged merely because Dependabot created it.

## Dependency, workflow, and lockfile policy

Security fixes precede convenience upgrades. Keep an indirect MCP resolution indirect: use the
existing lockfile-only PR where it resolves MCP >=1.28.1, and add a direct constraint only if the
declared dependency model requires it, with the reason recorded in the PR. Refresh using the
repository's documented `uv` workflow; inspect both `pyproject.toml` and `uv.lock`, retain solver
markers, and reject an unrelated resolver expansion.

For MCP/FastMCP updates, inspect installed imports against the resolved environment and run the
backend's live Streamable-HTTP and tool-registry smoke. MCP 1.28.1 changes Streamable-HTTP
buffering and WebSocket security/deprecations, so HTTP-only use remains an assumption to prove,
not a waiver. Major upgrades (notably Torch 2.13.0) require upstream migration/ABI review and an
explicit compatibility test before implementation. GitHub Actions remain exact SHA pins; Docker
bases remain digest pins. Dependabot changes to either require source-provenance/digest review,
not a tag substitution.

Overlapping or obsolete workflow PRs are replaced by one deliberate branch pinned to the reviewed
router builder commit. Only once that replacement is pushed, linked, verified, and reviewable may
the superseded Dependabot PRs be closed with an explanatory comment. A conflict is resolved in the
new branch, never by force-pushing bot history.

## Execution and evidence model

One implementer owns one repository worktree at a time; a fresh reviewer checks specification
compliance and a second reviewer checks code quality before publication or merge. Independent
repositories may run in parallel. Before editing a backend, its `AGENTS.md` is read in full.

Repository ownership is allocated in security-first waves: Wave 0 router documentation and
evidence; Wave 1 ClinVar, GeneReviews, HGNC, HPO, MaveDB, MetaDome, MGI, Mondo, Orphanet,
PanelApp, SpliceAI Lookup, STRINGdb, UniProt, and VEP (one named writer each, with VEP held at
diagnosis); Wave 2 ClinGen and GenCC workflow/library work; Wave 3 PubTator Torch; Wave 4 the
already-clear autopvs1, clingen, gencc, gnomad, gtex, hpo, litvar, orphanet, and pubtator P0
evidence review. A reviewer is never the writer for the repository being reviewed. Finished
agents are closed before replacements are assigned, and no repository has concurrent writers.

For a dependency-only PR, verification is: documented environment sync, resolved-version/import
smoke, live MCP/HTTP smoke, container scan/SBOM where present, native `make ci-local` (or its
documented equivalent), and all required GitHub Actions. A compatibility or behavior change uses
strict TDD: demonstrate the failing test, minimally implement, prove focused success, then run the
full gate. Any failure triggers systematic debugging before a proposed fix.

The P0 sequence is: merge/revalidate each MCP security update; rebase the corresponding Contract
Truth PR; resolve lock conflicts intentionally; rerun all local and GitHub checks; merge only after
fresh success. Afterwards, run router fleet conformance and surface checks only against published
release evidence. PubTator remains Contract-Truth-only until measured surface evidence passes;
GeneReviews and gnomAD surface evidence waits for relevant releases; runtime-data-identity remains
unadopted until a published observation exists.

Rollback is a new corrective PR or a release rollback under the owning repository's release
procedure; never rewrite release evidence, tags, or merged history. This work does not publish
tags/releases, change rulesets, secrets, environments, GitHub Apps, or live infrastructure.
