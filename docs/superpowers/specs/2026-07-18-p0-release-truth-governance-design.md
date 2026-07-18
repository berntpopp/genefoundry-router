# P0 release truth, governance, and contract enforcement design

**Date:** 2026-07-18  
**Status:** Approved for planning  
**Issues:** `genefoundry-router` #79, #63, and #68

> Historical record — this document records the approved 2026-07-18 design. Current behavior is
> defined by the implemented standards, release evidence, and checked-in tests.

## Goal

Make the fleet's trust root governable, make data-bound release evidence attest what a
published container actually serves, and make the remaining client-facing contract claims
mechanically enforceable. The work must be independently releasable in three tracks and must not
turn #68 into an unbounded effort to fix every already-filed backend defect.

## Scope and boundaries

### In scope

1. **#79 — trusted-builder governance.** Protect this repository's `main` branch, which carries
   `.github/workflows/_container-release.yml`, and continuously verify that protection as part of
   the control ledger.
2. **#63 — runtime data identity.** For every `data-bound` release, compare the manifest's
   declared data identity with the running published image's independently reported identity,
   record that observed identity in sealed definition evidence, and fail on disagreement.
3. **#68 — central contract enforcement.** Add a canonical, byte-identical lint that prevents
   invalid documented tool-call arguments, unsafe universal claims, and unfenced historical
   documents. Repair the three current tool-surface-budget failures: PubTator B2, gnomAD B2, and
   GeneReviews `search_passages` B1.

### Explicitly out of scope

- The already-filed functional defects discovered in the 2026-07-14 fleet audit. They remain
  owned by their individual backend issues and are not silently folded into this program.
- #52's fleet-level deployment manifest and endpoint runtime application identity. #63 provides
  the data-identity primitive that #52 will consume, but does not implement #52's broader
  version/revision/image-digest chain.
- Adding required status checks, CODEOWNERS, a second approval, or a new release environment to
  the #79 branch rule. One approving review is the agreed policy.
- Rewriting dated design and plan documents to current behavior. Historical records remain
  historical; the gate only makes that status visible.

## Architecture

The program has three bounded tracks. They share a fail-closed posture but not an implementation
branch or release cadence.

```text
GitHub branch ruleset ──> control-ledger probe ──> trusted reusable builder

service materializes data ──> /health release_identity ──> typed verifier
                                                              ├─ pre-publish smoke gate
                                                              └─ published-digest capture ──> sealed evidence

live tool registry + client-facing docs ──> contract-truth lint ──> backend CI
reviewed fleet baseline ──> tool-surface gate ──> ci-local after zero violations
```

### Track A: #79 — one-review `main` protection

Create an active branch-targeting GitHub ruleset for `main` in
`berntpopp/genefoundry-router` with exactly these mandatory controls:

- a pull request is required before merge;
- one approving review is required;
- non-fast-forward updates are blocked;
- branch deletion is blocked;
- administrators are subject to the rule rather than silently exempt;
- no required status checks, code-owner approval, merge queue, or second reviewer are added.

The control is deliberately configured by a repository administrator, not by the release workflow
itself. A workflow must never be able to relax the branch policy that protects its own source.

`scripts/audit_container_controls.py` gains a router-only `main_branch_ruleset` probe. The
control-ledger model requires that field for `berntpopp/genefoundry-router` and forbids it for
ordinary backend rows. The probe checks the ruleset detail returned by the GitHub API, including
its branch target, active enforcement, exactly-one-approval setting, and force-push/deletion
blocks. Missing API evidence is a failure, not a manual pass.

### Track B: #63 — observed data identity

Every `data-bound` backend exposes this fragment in its successful readiness/health JSON. The
surrounding health document may retain backend-specific fields.

```json
{
  "release_identity": {
    "schema_version": 1,
    "data_identity": {
      "expected": {
        "release_tag": "data-clingen-2026-07-16",
        "digest": "sha256:..."
      },
      "actual": {
        "release_tag": "data-clingen-2026-07-16",
        "digest": "sha256:..."
      }
    }
  }
}
```

`expected` is the service's configured release requirement. `actual` is computed from the
materialized or observed authoritative data at runtime. Copying the configured values into
`actual` is non-compliant. `none` and `data-independent` services do not emit this fragment and
are not passed to the data-bound verifier.

Add a small typed verifier to the router's release library. It takes a readiness JSON document and
the parsed data requirements from `container-release.json`, rejects absent/malformed/extra-key
fragments, and returns one canonical observed identity only when:

1. the requirements are data-bound;
2. `expected` equals the declared `release_tag` and `digest`;
3. `actual` equals that same declared identity.

The verifier has two call sites.

1. The existing local composed smoke gate verifies the readiness JSON before publishing. This
   prevents a known bad data configuration from consuming an immutable release tag.
2. The published-digest capture verifies the fetched `*-health.json` and writes the verifier's
   canonical **observed** identity to the definition-capture input. The capture no longer obtains
   the data identity directly from shell reads of `container-release.json`.

`capture-definitions` then seals the observed identity in `mcp-capture-context.json`.
`assemble-evidence` retains its current comparison against sealed `data-requirements.json`, but
the comparison now proves declaration-versus-observation rather than declaration-versus-itself.

The rollout begins with ClinGen, an `external-reference` data-bound service. Its release must
prove: a matching local smoke payload passes; a wrong actual digest fails before publish; and a
published-digest capture records the same observed identity. Only then is the contract vendored
to the remaining data-bound backends. The existing published-digest capture remains a required
post-publish assertion; its failure is release-blocking and its earlier local smoke equivalent
minimizes avoidable burned tags.

### Track C: #68 — contract-truth and surface enforcement

The router owns a canonical `docs/conformance/contract_truth.py` helper, vendored byte-identically
by backend repositories. Each backend uses its own live FastMCP tool registry as the oracle; no
gate owns a hand-maintained list of tools, documents, or parameters.

The helper enforces three deterministic rules.

1. **Documented-call arguments.** It discovers `README.md`, `CHANGELOG.md`, and active
   `docs/**/*.md` files, extracts examples of `tool_name(argument=value, ...)`, and rejects each
   named argument absent from that tool's live `inputSchema.properties`. Every active document is
   discovered by glob; a hardcoded file list is forbidden.
2. **Universal prose.** Active client-facing docs may not make an unqualified universal claim
   such as "every response", "all responses", or "all tools". The lint recognizes these phrases
   case-insensitively and requires authors to replace them with a bounded claim that names its
   applicable tools or an explicit documented exception. This deliberately avoids pretending a
   generic static parser can infer arbitrary runtime response semantics.
3. **Historical-record fence.** Dated files beneath `docs/specs/`, `docs/plans/`, and
   `docs/superpowers/` are excluded from the two client-facing checks only if the first prose
   block contains `> Historical record`. A dated document without that visible marker is a failure,
   not a silent exclusion.

The canonical helper provides pure parsing functions and a small CLI for fixture-driven tests. A
backend's own test obtains its live tool registry using the installed FastMCP API and passes that
catalog plus its repository root into the helper. This keeps FastMCP-version-specific introspection
in the backend test and keeps the vendored lint independent of backend implementation details.

Separately, the current `scripts/check_tool_surface.py` remains the fleet-wide offline budget
authority. The three owner repositories must bring their live, reviewed definitions below B1/B2
without deleting required parameter documentation or reintroducing oversized `outputSchema`.
After the baseline is re-pinned and `make lint-surface` is green, add that target to `ci-local`.

## Error handling and security properties

- A 2xx health response without a valid `release_identity` fragment is not valid evidence for a
  data-bound release.
- The runtime identity verifier rejects JSON types, keys, tags, and digests outside the strict
  release models. It does not coerce or normalize a mismatch into a pass.
- Verification output contains only release tags and digests already present in signed release
  evidence; credentials, service tokens, and complete health payloads are not logged.
- The branch-rule audit treats a GitHub API 404, permission failure, missing branch target, or
  unavailable detail endpoint as unproven and therefore blocking.
- Contract-truth parsing reports the exact file, line, tool, and bad argument or universal phrase.
  It never rewrites documentation automatically.

## Acceptance criteria

### #79

- GitHub's rulesets API reports an active `main` rule with PR-required, one approval,
  non-fast-forward prevention, and deletion prevention.
- The router's checked-in control ledger carries verified API evidence for this rule.
- `audit_container_controls.py --check` fails if the rule is disabled, targets another branch,
  permits force pushes/deletion, or requires zero/two-or-more approvals.

### #63

- A data-bound readiness payload with matching expected/actual identity passes both typed unit
  verification and the release smoke path.
- A mismatched actual identity, mismatched expected identity, missing field, malformed digest, or
  data-independent payload passed as data-bound fails with an actionable error.
- A published-digest capture writes the observed identity into its sealed context; changing only
  `container-release.json` no longer changes the captured identity.
- Evidence assembly fails when manifest requirements and sealed observed identity disagree.
- The ClinGen canary succeeds through release and the data-bound fleet rollout has an explicit
  per-repository verification record.

### #68

- The canonical helper's own tests cover argument extraction, nested documentation roots,
  unknown arguments, known arguments, universal-language detection, and valid/invalid historical
  markers.
- Each backend's CI runs the byte-identical helper against its live tool registry and active docs.
- The three currently failing surface-budget rows are below B1/B2 in a reviewed fleet baseline.
- `make lint-surface` passes and becomes a required `ci-local` target only in the same change that
  makes it green.

## Verification and closure protocol

Every implementation PR runs `make ci-local`. The release-library and workflow changes also run
the focused release tests and rendered Compose smoke tests named in their implementation plan.
After merge, the release evidence uses an immutable image digest and the exact deployed health
payload; it is not inferred from GitHub HEAD or a working tree. Issue closure comments link the
governing PR, exact test/run evidence, and, where applicable, the canary or fleet-rollout record.

## Consequences

This design intentionally makes release failure earlier and louder. It will expose backend health
payloads that cannot prove their own data identity, documentation that overstates capability, and
branch governance that had been assumed rather than verified. That friction is the required P0
control, not incidental complexity.
