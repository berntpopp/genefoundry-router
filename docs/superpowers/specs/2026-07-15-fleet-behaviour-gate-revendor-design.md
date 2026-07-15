# Fleet Behaviour Gate Re-Vendor Design

## Goal

Bring the 21 in-scope GeneFoundry `*-link` backend repositories to the router's canonical
behaviour conformance gate at router commit `ba09fdc`, blob
`30d639242b700e556abf41be620172e1f3d497ec`, and prove each backend's `main` branch has a green
`mcp-conformance` check after merge.

## Scope

In scope:

- Safe relaxation-only repos: `hpo-link`, `panelapp-link`, `mondo-link`, `gtex-link`,
  `uniprot-link`, `autopvs1-link`, `hgnc-link`, `genereviews-link`, `mgi-link`, `gencc-link`,
  `metadome-link`, `gnomad-link`, `stringdb-link`, `vep-link`, `spliceailookup-link`,
  `mavedb-link`.
- Under-gated repos requiring live validation: `clinvar-link`, `orphanet-link`, `clingen-link`,
  `litvar-link`.
- Already completed but included in final audit: `pubtator-link`.

Out of scope:

- Repeating the already merged MCP contract-hardening sweep.
- Editing `tests/conformance/conformance.py`, `tests/conformance/test_behaviour_v1.py`, transport
  tests, or backend runtime code unless a stricter live behaviour gate exposes a defect.
- Renaming `spliceailookup-link`; its server identity remains `spliceailookup-link`.
- The extra local `omim-link` checkout, which is not in the 21-repo task list.

## Architecture

The router remains the single source of truth for behaviour conformance logic. Each backend vendors
that file byte-for-byte at `tests/conformance/behaviour.py`. Backend CI executes the vendored probe
through each repo's existing `.github/workflows/conformance.yml`, which builds the production
container, starts it with `make docker-up`, waits for health and `/mcp`, then runs
`tests/conformance/test_transport_v1.py` and `tests/conformance/test_behaviour_v1.py` against the
local container.

The work is split into two execution lanes. The safe lane is mechanical because the old gate blob
`0e605447ff6e44dff164c6315d4a332c499f5fd6` differs from the new one only by relaxing a
`not_found` example-acceptance outcome from failure to inconclusive. The under-gated lane is
sequential and live because those repos were certified by older gates that missed grouped payload,
array enum, wrong-server, or `not_found` behaviour.

## Data Flow

For each backend:

1. Start from the latest `origin/main` in the main checkout.
2. Create a short-lived branch named `chore/revendor-behaviour-gate-ba09fdc`.
3. Copy router `docs/conformance/behaviour.py` from commit `ba09fdc` into
   `tests/conformance/behaviour.py`.
4. Add a concise `CHANGELOG.md` note saying the behaviour conformance gate was re-vendored to router
   blob `30d639242b`.
5. Run local validation.
6. Push, open a PR, and wait for GitHub CI.
7. Query check runs for the PR head SHA with the GitHub check-runs API endpoint for the concrete
   repository and commit SHA, and require a check-run whose name contains `onformance` and whose
   conclusion is `success`.
8. Merge the PR.
9. Query check runs on the new `main` SHA and require the same conformance success.

The final fleet audit compares every in-scope backend's `main:tests/conformance/behaviour.py` blob
with `30d639242b700e556abf41be620172e1f3d497ec`.

## Error Handling

Dirty local checkout state is preserved, not overwritten. If a checkout cannot move to `main`
because of local work, stash only that repo's uncommitted state with a descriptive message before
starting the re-vendor branch.

GitHub's `gh pr checks --watch` is not used as the merge gate because it can exit before the
DB-dependent conformance job is queued. The merge gate is the check-runs API response for the exact
head SHA. Missing, queued, or red conformance checks block merge.

Live probes are sequential for the four under-gated repos. If an upstream returns rate limiting,
the run backs off and retries without printing or copying credentials. Tokens are never displayed,
copied into files, or committed.

## Testing

Safe repos run `make ci-local` locally after the vendored file and changelog note. Their live
behaviour proof comes from GitHub's `mcp-conformance` job on the PR and again on `main`.

Under-gated repos run local live validation before PR:

```bash
make docker-build
make docker-up
CONFORMANCE_NAME=clinvar-link CONFORMANCE_MCP_URL=http://127.0.0.1:8000 uv run pytest tests/conformance/test_behaviour_v1.py -v
make docker-down
```

If a live gate fails or reports an ungated tool, add a focused failing test for the backend defect,
run it red, make the minimal fix, run it green, rerun `make ci-local`, rerun live behaviour
conformance, open the PR, run one adversarial Codex review with `gpt-5.6-sol` and
`reasoning_effort=high`, rework if requested, then merge only after PR and `main` conformance are
green by API.

## Acceptance Criteria

- All 21 in-scope backend `main` branches have `tests/conformance/behaviour.py` blob
  `30d639242b700e556abf41be620172e1f3d497ec`.
- Every in-scope backend's latest `main` SHA has a check-run matching `onformance` with conclusion
  `success`.
- The only safe-lane repo source changes are `tests/conformance/behaviour.py` and `CHANGELOG.md`.
- Under-gated repo runtime changes exist only where live validation exposed a real stricter-gate
  defect, and each such change has local tests plus one adversarial Codex review round.
