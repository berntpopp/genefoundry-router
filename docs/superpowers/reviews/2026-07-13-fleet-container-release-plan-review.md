# Fleet Container Release Plans — Adversarial Review Record

**Artifacts:** the control-plane, immutable-data, and fleet-adoption plans dated
2026-07-13

**Requested reviewer:** Claude Code 2.1.207, model `claude-opus-4-8`, effort
`xhigh`

**Review date:** 2026-07-13

## Invocation evidence

The installed CLI reports `--effort` values including `xhigh` and accepted the
exact requested model. An initial combined no-tools review exceeded its bounded
window without a verdict and was terminated. A first focused invocation emitted
attempted tool-call markup despite tools being disabled and was rejected as an
invalid review. Three corrected, isolated invocations used a reviewer-only system
prompt, tools disabled, no session persistence, and the complete corresponding
plan on stdin. All three returned textual NO-SHIP verdicts with ranked findings.

Invocation failures are recorded here and were not counted as review evidence.
The three completed subsystem verdicts are the review evidence dispositioned
below.

## Control-plane disposition

| Finding | Disposition |
|---|---|
| B1, local Docker archive/push cannot establish the gated registry digest | Accepted. The build job now exports an OCI layout and declares its manifest digest; a pinned daemonless publisher performs byte-preserving copy and proves registry equality. |
| B2, aliasing may create a wrapper index | Accepted. Finalization applies identical manifest bytes with a pinned daemonless client and verifies source/version/attested digests are equal; `imagetools create` is forbidden. |
| B3, late reruns cannot rely on a reproducible rebuild | Accepted. `prepare` is a resume oracle; an existing source digest is imported/re-gated without rebuilding. |
| B4, `main` backfill binds provenance to the wrong source | Accepted. Pre-adoption/backfill publication is removed; only post-adoption exact stable tag pushes release. |
| B5, evidence assembly could execute leaf code in a write job | Accepted. The release DAG has six jobs and a read-only `assemble-evidence`; publisher/finalizer use only pinned runner binaries. |
| B6, flattened rootfs misses deleted layer content and config secrets | Accepted. Policy scans every OCI layer, whiteouts, and image config/history. |
| B7, conflicting CLI exit codes | Accepted. One shared `0/1/2/3` enum and JSON verdict contract is normative. |
| B8, `gh release verify` allegedly does not exist | Rejected as stale platform knowledge. Current official GitHub CLI documentation includes `gh release verify` and `verify-asset`; the plan pins/checks a minimum supporting CLI. Draft/publish sequencing and post-publication verification were nevertheless clarified. |
| H1–H8 | Accepted where factual: caller permission ceiling, cross-repository signer/source fixture, no PR-writable release cache, complete release gates, explicit modes, offline manifest/bundle test, protected environment, and bounded centrally reviewed allowlists. |

## Data-plan disposition

| Finding | Disposition |
|---|---|
| B1, data modes were internally inconsistent | Accepted with a stronger model: four authoritative modes are `none`, `external-reference`, `restored-database`, and `upstream-live`; runtime cache is independent. GeneReviews/PubTator use restored-database and Metadome is none plus cache. |
| B2, router-owned contract had no runtime distribution path | Accepted. Leaf repositories vendor the generated schema plus content hash from the protected router commit and run `make vendor-check`; they do not depend on router runtime code. |
| B3, content/definition gates were undefined | Rejected as a cross-plan false positive: control-plane Tasks 4 and 6 create them. Explicit dependency edges were added so the data plan cannot run first. |
| B4, flattened scanning misses shipped data | Accepted fleet-wide; all 22 OCI layouts are inspected per layer/config. |
| B5, immutable releases/`gh release verify` allegedly do not exist | Rejected by current GitHub documentation. The atomic-directory concern was accepted: versioned directories, a materialization lock, atomic symlink selection, fsync, retention, and readiness replace generic rename. |
| B6, data builder held publication credentials and assets lacked provenance | Accepted. Data workflows split credential-free build from non-executing, SHA-pinned, attesting publish jobs. |
| B7, PostgreSQL dump restore is executable content | Accepted in substance. Only data-only custom-format archives with an allowlisted TOC are restored without egress as a non-superuser after in-repo migrations. The proposed nonexistent `pg_restore --no-superuser` flag was rejected; role privilege is enforced by the connection role. |
| B8, rollback was not implemented | Accepted. Compatibility ranges, previous-known-good tuples, rollback command/tests, retention, and upstream-live degraded recovery are explicit. |
| B9, licensing was descriptive rather than gating | Accepted. Public publication hard-fails without affirmative redistribution review; operator-preseeded/private or upstream-live modes remain available. |
| B10, app materialization/read-only mount/tmpfs conflicted | Accepted. A writable hardened init service materializes; the app mounts read-only/no-egress, SQLite uses immutable read mode, and offline pre-seeding is first-class. |
| H1–H5, H6 | H1–H5 accepted: fleet readiness identity, reviewed digest PRs, production rejection of development latest, concurrency locks/groups, and canonical expanded-tree hashes. H6 was rejected: the installed Claude CLI demonstrably supports exact model plus `xhigh`. |

## Adoption-plan disposition

| Finding | Disposition |
|---|---|
| B1, central workflows were absent | Rejected as a cross-plan false positive; the control-plane plan authors/tests them. A hard merged-control-plane dependency was added. |
| B2, releases preceded PR creation/merge | Accepted. Tasks are reordered into explicit router-first merge gates before release/candidate/deploy. |
| B3, callers could pin a squash-discarded branch SHA | Accepted. Rendering uses only a protected `origin/main` ancestor after merge and rejects feature/worktree SHAs. |
| B4, old tag backfill creates source/config/provenance skew | Accepted and removed. |
| B5, digest-preserving trust split unspecified | Accepted through the OCI-layout six-job control-plane contract and fleet static tests. |
| B6, data-mode/rollback/definition comparison contradictions | Accepted. Modes are closed, upstream-live recovery is explicitly degraded, and definition identity separates tool schema digest from capture-context provenance. |
| B7, deferred enabled backends conflict with atomic candidates | Accepted. Candidate reconciliation is blocked until every enabled backend has a verified release; no unverified row is admitted. |
| H1–H10 | Accepted where applicable: release cache isolation, non-cancelling concurrency, protected environment, structured signer checks plus current `--signer-digest`, no standing bootstrap PAT, personal-account control preflight, continuous config drift, leaf-code-free SARIF reporting, data-plan prerequisites, and explicit AMD64/no-Buildx-attestation settings. |
| H11, retain Codex instead of requested Opus | Rejected because it contradicts the maintainer's explicit reviewer choice. Exact Opus model/effort availability is probed fail-closed and its output/disposition is retained. |
| M1, `genereview-link` service name is probably a typo | Rejected against repository evidence: the existing Compose service key is exactly `genereview-link`. The Metadome classification portion was accepted and corrected to none plus runtime cache. |
| M10, use `imagetools create` for aliasing | Rejected because it can introduce a wrapper index; the control-plane review's manifest-identical daemonless tag operation is used. |

## Primary platform evidence for rejected findings

- Current GitHub immutable releases lock published assets and the associated tag
  and create a release attestation:
  <https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases>
- Current GitHub CLI documents `gh release verify` and `gh release verify-asset`:
  <https://cli.github.com/manual/gh_release>
- Current GitHub CLI documents `--signer-digest`, `--source-digest`, offline
  bundles, custom trusted roots, and self-hosted-runner denial:
  <https://cli.github.com/manual/gh_attestation_verify>
- GitHub OIDC documents both `job_workflow_ref` and `job_workflow_sha` for
  reusable workflows:
  <https://docs.github.com/en/actions/reference/security/oidc>

## Closure

All accepted blocking/high findings are represented in the revised design and
plans. Rejected findings have current primary evidence or direct repository/CLI
evidence. No blocking plan-review finding remains open for the implementation
handoff.
