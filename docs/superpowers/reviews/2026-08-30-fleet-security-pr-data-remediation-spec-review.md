# Fleet Security and Data Remediation — Adversarial Spec Review

> Historical record — independent `gpt-5.6-sol` high-reasoning review performed on 2026-08-30.
> Current behavior and evidence are defined by merged code, immutable releases, live systems, and
> tests. This record describes the pre-implementation design review and its disposition.

## Scope

The review challenged
`docs/superpowers/specs/2026-08-30-fleet-security-pr-data-remediation-design.md` against the router,
the 21 configured backends, current GitHub state, data/release workflows, and the three supporting
repositories. The reviewer was read-only and asked to prioritize unsafe mutations, impossible
closure claims, identity/rights defects, missing fail-closed behavior, and sequencing conflicts.

## Findings and dispositions

| Severity | Finding | Disposition in the amended design/plans |
| --- | --- | --- |
| Critical | GeneReviews publication was required even though redistribution authority was unresolved. | Publication, including drafts, is prohibited without a dated affirmative record naming authority/reviewer, terms/version, permitted asset/use, attribution, and durable evidence. Local build/verification may continue; ambiguity leaves program status blocked. |
| Important | The existing GeneReviews data-only publisher/verifier could not reconstruct and verify the current corpus safely. | Added credential-free sealed build handoff, full digest-pinned PostgreSQL/pgvector, migrations-before-restore, actual manifest/checksum assets, bounded allowlisted downloads, strict restore, invariants, representative queries, and separate privileged publisher. |
| Important | ClinVar, MaveDB, ClinGen, and Orphanet tag collision rules did not bind tags to stable source/artifact identity. | Added absent/identical/published/draft-collision state machines, pure bounded identity helpers, volatile-field exclusions, and no automatic deletion; only the exact audited malformed ClinVar draft may be removed by database ID as a separate ledgered action. |
| Important | Scheduled fleet monitoring could convert probe failures to warnings and did not prove schema-derived behavior. | Scheduled monitoring is explicitly non-closure evidence; a separate strict job must produce exact 21/21 transport and 21/21 behavior results and fail on every nonzero/malformed/timeout result. |
| Important | Vulnerability closure could be read as requiring removal of immutable historical findings or unfixable advisories. | Acceptance is scoped to exact selected/new production images and the versioned `fixable-high-critical-v1` policy with zero fixable HIGH/CRITICAL results and no waivers. |
| Important | Backend restart edits could precede the canonical policy and turn one-shot services into daemons. | Router standard, role-aware validator, and reference overlay land first; serving/database roles require `unless-stopped`, init/build roles require `no`. |
| Important | The security profile risked becoming an aspirational source rather than a final assessment. | Gitlink and reassessment are the last mutable workstream, after final releases, deployment, probes, alerts, and controls; blocked items remain blocked. |
| Important | Multi-repository execution lacked a single-writer/worktree authority. | Added central ownership/mutation ledger, exact origin/base SHA, dedicated clean worktrees, and no concurrent writers per branch/worktree. |
| Important | Closing 175 Dependabot PRs by package name could lose changed head revisions or files. | Added immutable per-PR head/file/version mapping, re-query before merge and closure, replacement merge/check evidence, and explicit still-open disposition. |
| Important | A stored PAT or installation token was an unsafe control-audit prerequisite. | Added dedicated GitHub App, protected environment, short-lived full-SHA token minting, exact 22-repository scope, Metadata/Admin read only, revocation, and no PAT fallback. |
| Important | Router ruleset matching accepted approval counts `{0,1}` instead of current exact policy. | Exact current approval value is `0`; `1` is rejected until a separately reviewed second-maintainer transition. |
| Important | Supporting repositories lacked testable release/security criteria. | Added exact website npm/container gates and release boundaries, benchmark repository-automation tests/native gates, and security-profile-last evidence requirements. |
| Important | Fleet release did not require per-service deployment and rollback records. | Every promotion now requires current/target/rollback tuples, compatibility/backup decision, exact commands, observation window, acceptance checks, and verified rollback. |
| Important | Locally computed data could be mislabeled as upstream-authenticated or fully attested. | Added integrity classes and local computation provenance: code/lock/model/runtime/GPU/parameters/counts/fixtures/builder. Capture-only hashes are never called upstream authentication. |
| Important | Closure relied on prose rather than a typed, fresh evidence object. | Added fail-closed closure model/CLI, exact repository cardinality, strict types, discriminated verified/not-applicable/blocked states, and a maximum evidence age of 24 hours invalidated by relevant changes. |
| Important | External GitHub/release/deployment mutations had no explicit authority/reversibility mapping. | Added dated scoped owner authorization plus per-mutation authority, credential, reversibility, verification, and rollback ledger; rights and dedicated secrets remain separate preconditions. |
| Minor | Caller token passthrough was phrased as a backend concern. | Final router proxy integration owns the proof that caller Authorization is stripped and only backend-specific credentials are injected. |
| Minor | Some data-currentness outcomes were subjective. | Replaced them with exact identities, counts, source metadata, representative queries, typed no-op/blocker states, and repository-native verification commands. |
| Minor | Release evidence could be generated from different image builds. | Release workflow must build once and scan, SBOM, smoke, attest, and publish that exact OCI digest. |

## Decision

All Critical and Important design findings were accepted. The design was amended before the four
implementation plans were considered executable. GeneReviews rights and dedicated GitHub App or
deployment credentials remain real external preconditions: their absence does not stop safe source
work, but it prevents a truthful `complete` closure state.
