# P0 release truth, governance, and contract enforcement design

**Date:** 2026-07-18  
**Status:** Review complete — awaiting approval for planning

**Issues:** `genefoundry-router` #79, #63, and #68

> Historical record — this document records the 2026-07-18 design. Current behavior is
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

Create an active branch-targeting GitHub ruleset for the router's `main` branch with exactly
these mandatory controls:

- a pull request is required before merge;
- one approving review is required;
- non-fast-forward updates are blocked;
- branch deletion is blocked;
- administrators are subject to the rule rather than silently exempt;
- no bypass actors are configured;
- no required status checks, code-owner approval, merge queue, or second reviewer are added.

The control is deliberately configured by a repository administrator, not by the release workflow
itself. A workflow must never be able to relax the branch policy that protects its own source.

This rule has a non-negotiable operational precondition: before it is enabled, the router has at
least two active, independently controlled maintainer accounts with write access. GitHub does not
permit self-approval, and the ruleset has no bypass actor. The bootstrap sequence is therefore:

1. establish and test the two-person reviewer pool on a non-protected branch;
2. enable the active no-bypass `main` ruleset;
3. run the authenticated control probe and commit its evidence through a one-approved-review PR;
4. do not enable the rule at all if the two-person precondition cannot be maintained.

This is intentionally a precondition, not a break-glass exemption. A missing second reviewer must
be fixed by restoring the reviewer pool before a `main` merge, rather than by granting a hidden
administrator bypass.

`scripts/audit_container_controls.py` gains a router-only `main_branch_ruleset` probe. The
control-ledger schema declares an explicit repository role (`trusted-builder` or `backend`): it
requires that field only for the one `trusted-builder` row and forbids it for `backend` rows. The
expected-fleet validator derives the router row from its local registry and requires it to be the
sole `trusted-builder`; every registered backend must be `backend`. This avoids binding the model
to a mutable owner/repository string.

The probe checks the ruleset detail returned by the GitHub API, including its branch target, active
enforcement, exactly-one-approval setting, force-push/deletion blocks, and an empty
`bypass_actors` array. Missing API evidence is a failure, not a manual pass.

The probe runs with a short-lived GitHub App installation token, or a fine-grained token limited to
the target repository's read-only administration capability. It is injected as `GH_TOKEN` only
into the scheduled/manual audit job, never committed to the ledger, command line, or logs. The
implementation must document the exact GitHub permission name validated against the current API;
failure to obtain sufficient read access is an `unavailable` control, never a soft pass.

### Track B: #63 — observed data identity

Every `data-bound` backend that has opted into `data_identity_contract: "runtime-v1"` in its
`container-release.json` exposes this fragment in its successful readiness/health JSON. The
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
materialized authoritative data at runtime, not copied from configuration. `none` and
`data-independent` services do not emit this fragment and are not passed to the data-bound
verifier.

The `digest` has one fleet-wide, reproducible pre-image. A data release contains a
`data-identity-manifest.json` whose canonical UTF-8 JSON serialization uses sorted object keys,
no insignificant whitespace, and a fixed `schema_version`. It records `release_tag` plus an
ascending-path inventory of every authoritative runtime input: each item has its POSIX relative
path, byte length, and SHA-256 of the exact materialized byte stream. Database-backed releases use
a documented deterministic logical-dump recipe as their input stream; prepared-upstream releases
first materialize their pinned snapshot into that same inventory. The identity digest is the
lowercase SHA-256 of those canonical manifest bytes, prefixed `sha256:`. This digest replaces any
ambiguous archive-only digest in `data_requirements` for a migrated backend.

A small shared runtime library, not backend-specific health glue, reads the installed identity
manifest, re-hashes every listed runtime input, rebuilds the canonical manifest, and emits the
result as `actual`. It rejects missing, extra, unreadable, path-traversing, or byte-mismatching
inputs. Startup may perform this work once and cache the successful result for readiness, but the
cached value must be produced by that verification run. Each backend's conformance test corrupts
or substitutes one materialized input and proves that readiness no longer emits the declared
actual identity and that the release verifier fails. That negative-derivation test, plus shared
code, makes configuration-copying insufficient to pass the contract.

Add a small typed verifier to the router's release library. It takes a readiness JSON document and
the parsed data requirements from `container-release.json`, rejects absent/malformed/extra-key
fragments for an adopted `runtime-v1` service, and returns one canonical observed identity only
when:

1. the requirements are data-bound;
2. `expected` equals the declared `release_tag` and `digest`;
3. `actual` equals that same declared identity and comes from a successful shared runtime
   materialization verification.

The verifier has two call sites.

1. The existing local composed smoke gate verifies the readiness JSON before publishing. This
   prevents a known bad data configuration from consuming an immutable release tag.
2. The published-digest capture verifies the fetched `*-health.json` and writes the verifier's
   canonical **observed** identity to the definition-capture input. The capture no longer obtains
   the data identity directly from shell reads of `container-release.json`.

`capture-definitions` then seals the observed identity in `mcp-capture-context.json`.
`assemble-evidence` retains its current comparison against sealed `data-requirements.json`, but
the comparison now proves declaration-versus-observation rather than declaration-versus-itself.

Version 1 rejects unknown keys and pins SHA-256. A schema change creates a new integer version;
the router release library gains read support before any backend emits it, and during migration it
accepts only the explicitly documented supported versions. This keeps v1 strict without making
the first additive evolution an accidental fleet-wide flag day.

The rollout begins with ClinGen, an `external-reference` data-bound service. Its release must
prove: a matching local smoke payload passes; a corrupt materialized input prevents production of
the declared `actual` and fails before publish; and a published-digest capture records the same
observed identity. The checked-in `ci/fleet-application-releases.json` / per-repository
`container-release.json` contract is the authoritative data-bound classification. The release
candidate inventory also records each data-bound service as either `unadopted` or `runtime-v1`;
a missing state is invalid. The enforcement predicate is **data-bound AND runtime-v1**: that
combination requires a valid fragment, while an explicitly `unadopted` service remains on the
legacy capture path and is visibly outstanding in the rollout ledger. Only then is the contract
vendored to the remaining data-bound backends. The router track exits with the ClinGen canary and
a checked-in rollout ledger; each other backend becomes release-blocking only in its own adoption
PR, avoiding a fleet-wide router-release hostage. The existing published-digest capture remains a
required post-publish assertion; its failure is release-blocking and its earlier local smoke
equivalent minimizes avoidable burned tags.

### Track C: #68 — contract-truth and surface enforcement

The router owns a canonical `docs/conformance/contract_truth.py` helper, vendored byte-identically
by backend repositories. Each backend uses its own live FastMCP tool registry as the oracle; no
gate owns a hand-maintained list of tools, documents, or parameters.

The helper enforces three deterministic rules. The router runs the same helper against its own
active documentation and publishes a SHA-256 pin for the canonical source. Every backend test
asserts its vendored helper matches that pin before executing it, so a locally edited copy cannot
silently drift.

1. **Documented-call argument names.** Active documentation comprises root `README.md` and
   `CHANGELOG.md`, plus every `docs/**/*.md` outside the explicit internal roots
   `docs/specs/`, `docs/plans/`, `docs/superpowers/`, and `docs/reviews/`. The helper extracts
   only `tool_name(argument=value, ...)` expressions whose callee exactly matches a live registry
   tool, then rejects an argument name absent from that tool's `inputSchema.properties`. A
   non-matching callee is ignored as ordinary code, not treated as an MCP tool. This rule covers
   unknown keyword names only; argument values, types, requiredness, positional arguments,
   multiline calls, and JSON-form examples are intentionally outside this P0 gate. Every eligible
   document is discovered by glob; a hardcoded file list is forbidden.
2. **Universal response-contract prose.** Active documentation may not make an unqualified
   universal claim about response or envelope shape, such as "every response includes" or "all
   tools return", when that clause presents an MCP contract. The detector is sentence/clause
   scoped, case-insensitive, and ignores negated or explicitly qualified forms (for example,
   "not every response" or "all tools except …"). It permits the exact, canonical fleet research
   disclaimer through a narrow fixture-tested allowlist. Authors otherwise replace an overclaim
   with a bounded tool list or explicit documented exception. This deliberately avoids pretending
   a generic static parser can infer arbitrary runtime response semantics.
3. **Historical-record fence.** A dated historical file is a Markdown file named
   `YYYY-MM-DD-*.md` beneath `docs/specs/`, `docs/plans/`, or `docs/superpowers/`. Its first
   non-title, non-metadata prose block must be a blockquote whose first trimmed line is
   `> Historical record` followed only by end-of-line, whitespace, or an em-dash explanation;
   otherwise the helper fails with the path and line. Those internal-root records are excluded
   from rules 1 and 2 only after that check passes. Other internal-root documents are excluded by
   the explicit path policy rather than an implicit date heuristic.

The canonical helper provides pure parsing functions and a small CLI for fixture-driven tests. A
backend's own test obtains its live tool registry using the installed FastMCP API and passes that
catalog plus its repository root into the helper. This keeps FastMCP-version-specific introspection
in the backend test and keeps the vendored lint independent of backend implementation details.

Separately, the current `scripts/check_tool_surface.py` remains the fleet-wide offline budget
authority. The three owner repositories must bring their live, reviewed definitions below B1/B2
without deleting required parameter documentation or reintroducing oversized `outputSchema`.
They shrink the surface first, then qualify affected prose without re-inflating schemas or example
budgets. After the baseline is re-pinned and `make lint-surface` is green, add that target to
`ci-local`.

## Error handling and security properties

- A 2xx health response without a valid `release_identity` fragment is not valid evidence for a
  data-bound release.
- The runtime identity verifier rejects JSON types, keys, tags, and digests outside the strict
  release models. It does not coerce or normalize a mismatch into a pass.
- A `data-bound` plus `runtime-v1` classification with an absent runtime fragment fails.
  Classification and adoption state are derived from checked-in release manifests, never an
  optional backend convention.
- Verification output contains only release tags and digests already present in signed release
  evidence; credentials, service tokens, and complete health payloads are not logged.
- The branch-rule audit treats a GitHub API 404, permission failure, missing branch target, or
  unavailable detail endpoint as unproven and therefore blocking.
- Contract-truth parsing reports the exact file, line, tool, and bad argument or universal phrase.
  It never rewrites documentation automatically.

## Acceptance criteria

### #79

- GitHub's rulesets API reports an active `main` rule with PR-required, one approval,
  non-fast-forward prevention, deletion prevention, and no bypass actors.
- Before activation, two independently controlled maintainers with write access have completed a
  test PR; the first protected `main` change is itself merged with one approval.
- The router's checked-in control ledger carries verified API evidence for this rule.
- `audit_container_controls.py --check` fails if the rule is disabled, targets another branch,
  permits force pushes/deletion, has any bypass actor, or requires zero/two-or-more approvals.

### #63

- An adopted `data-bound` (`runtime-v1`) readiness payload with matching expected/actual identity
  passes both typed unit verification and the release smoke path.
- A mismatched actual identity, mismatched expected identity, missing field, malformed digest, or
  data-independent payload passed as data-bound fails with an actionable error.
- For ClinGen, corrupting a listed materialized input prevents the shared runtime calculation from
  producing the declared identity and fails the readiness/release path; a backend cannot pass by
  copying configuration into `actual`.
- A published-digest capture writes the observed identity into its sealed context; changing only
  `container-release.json` no longer changes the captured identity.
- Evidence assembly fails when manifest requirements and sealed observed identity disagree.
- The ClinGen canary succeeds through release, and a checked-in rollout ledger records each
  subsequent backend's independently merged verification without making unfinished backends a
  router-release gate; every data-bound backend has an explicit `unadopted` or `runtime-v1`
  state.

### #68

- The canonical helper's own tests cover argument extraction, known/non-tool callees, unknown and
  known argument names, qualified/negated/allowlisted universal language, active/internal roots,
  and valid/invalid historical markers.
- The router and each backend CI run the SHA-pinned helper against their live tool registry and
  active docs; a byte-drifted vendored copy fails before linting.
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
