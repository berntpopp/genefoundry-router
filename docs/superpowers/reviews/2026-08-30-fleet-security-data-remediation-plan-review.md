# Fleet Security and Data Remediation — Adversarial Plan Review

> Historical record — independent `gpt-5.6-sol` high-reasoning review performed on 2026-08-30.
> Current behavior and evidence are defined by merged code, immutable releases, live systems, and
> tests. This record describes the pre-implementation review and disposition of the four plans.

## Scope and method

Three read-only reviewers challenged the control/container plans, data/deployment plans, and
cross-plan sequencing against the current router, sibling repositories, live immutable release
metadata, and deployment-controller source. The reviewers were asked to report only executable
Critical/Important defects: unsafe mutation, unverifiable identity, authorization leakage,
impossible task ordering, stale evidence selection, or a closure claim that could pass without its
stated proof.

The plans were frozen for each review pass. Findings were amended, then the same reviewers verified
the corrections. The final targeted pass reported no remaining Critical or Important findings.

## Findings and dispositions

| Area | Adversarial finding | Disposition in the reviewed plans |
| --- | --- | --- |
| Rights | GeneReviews and ClinGen could mint release credentials before an exact rights/artifact decision. | Publication is digest-bound and rights-gated; GeneReviews uses a content-addressed no-follow local handoff and ClinGen splits read-only validation from the no-checkout write job. |
| Historical data | HPO's existing immutable release predates the modern checksum/attestation contract. | The one literal legacy release has a frozen date-qualified asset/manifest/count/digest fixture; future releases must satisfy the complete modern contract. |
| Release states | MaveDB draft reuse could not be represented by a single skip boolean. | Added disjoint `published_noop`, `draft_publish_existing`, `create`, and `collision` states with mutation-specific gates. |
| Strict behavior | Existing behavior reporting could call skipped or schema-ungated probes conformant. | Added opt-in strict JSON mode; failed, skipped, ungated, empty, duplicate, missing, malformed, and timeout results all fail closure. |
| Application version | Generic latest-release selection could choose a newer data release. | Application releases are enumerated by strict SemVer, resolved to peeled tag/source/manifest identity, and selected without the generic latest pointer. |
| Bot inventory | A fixed 175-row claim could not represent new PRs or changed heads, and later resolution fields conflicted with an immutable baseline. | The 175 audit identities are hashed and immutable; mutable resolution objects and separately counted typed deltas preserve honest current state. A tested versioned validator enforces the model. |
| Workflow authority | Merely checking 40-hex reusable-workflow refs allowed older trusted-builder revisions, and scanning sibling roots observed the wrong checkouts. | A versioned validator requires equality to the Plan 1 SHA, accepts only a ledgered repo-to-clean-worktree/HEAD map, reruns after all Plan 2/3 merges, and reruns in the final closure window. |
| Workflow-run identity | Several manual workflows selected `--limit 1`, permitting a stale or unrelated run. | Run selection is bound to exact workflow, `workflow_dispatch` event, head SHA, and captured dispatch time; exact artifacts are downloaded by run ID and hashed. |
| Container rendering | Raw Compose fallbacks lacked required environment/data inputs and could not prove the candidate tuple. | Only repository-native build/render contracts and the shared exact candidate validator are accepted. |
| Controller fetch | The deployment controller continued after failed Git operations and could rebuild or deploy a stale projection. | Safety TDD requires a clean exact checkout, reviewed Compose projection, pinned digest pull, `up --no-build`, and fail-closed rollback. |
| Controller lifecycle | A detached safety worktree could not also author/merge tracked lock and ledger updates. | Immutable executor and per-phase config worktrees are separate; records bind executor, config base, pre-activation merge, and post-verification merge SHAs. |
| Record timing | A complete target OCI tuple was required before the tag workflow had built it, while its validator was planned later. | Pre-tag records use typed `pending_release`; generic controller schema/safety Steps 1–4 merge first, then release evidence finalizes and validates the record before pin/deploy. |
| Data activation | A generic volume switch did not specify PostgreSQL seed/restore or repository-specific service sets. | A literal adapter registry covers audited SQLite services and a separate GeneReviews PostgreSQL/pgvector flow, with candidate-only writes and crash/rollback tests at every phase. |
| Pre-baseline attest | Backends could not satisfy router-inventory equality before the final router baseline existed. | Pre-baseline attest verifies live release/deployment records without old-router equality; ordinary post-router attest retains strict baseline equality. |
| Non-data handoff | Plan 4 expected a Plan 3 SHA for repositories Plan 3 never changes. | Plan 2 merge SHA is the default release handoff; a later Plan 3 source/pin SHA supersedes it only with ancestry proof. |
| Ownership | Plan 2 omitted Orphanet and GTEx from Plan 3 worktree transfer. | Every repository receiving Plan 3 source/workflow commits is explicitly transferred through the single-writer ledger. |
| Router closure source | Closure could dispatch from a newer unreleased router `main`. | Before dispatch and sealing, current `main`, release-manifest source, and deployed health revision must be identical or the final release/deployment restarts. |
| Website/router rollout | Website and router skipped the controller's pre/post merge and rollback lifecycle. | Both now use the same staged record, reviewed pre-activation config, exact deployment, promotion PR, and verified rollback protocol as backends. |
| Evidence freshness | The security-profile merge and later evidence release contradicted “final mutation” and could stale evidence. | The profile is the final ordinary source/control/deployment mutation. All change-sensitive evidence is rerun afterward; substantive change restarts assessment. One predeclared detached seal is the sole exception, followed by a signed read-only receipt. |
| Seal schema | `expected_detached_seal` was introduced after an `extra="forbid"` schema had already been planned. | The field, exact tag/target/assets/state, sole-mutation rule, cross-validation, and negative tests are part of Task 1's initial schema/TDD. |
| Plan ordering | The design placed strict probes before the final router while Plan 4 required the final router endpoint. | The design and plan now agree: deploy backends, build/release/deploy the router baseline, then run strict live probes. |

## Final decision

The final frozen design and all four plans passed the three reviewers' targeted correction audit
with zero remaining Critical or Important findings. External rights determinations, dedicated
GitHub App secrets, release credentials, signing identity, and production deployment access remain
real execution preconditions; the plans require an honest `blocked` closure when any is absent and
never convert missing authority into success.
