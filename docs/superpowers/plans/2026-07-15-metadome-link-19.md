# MetaDome #19: make local evidence, aggregates, and pagination honest

> **For the implementer:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task by task.

**Goal:** Stop presenting cross-gene MetaDome domain aggregates as residue-local gnomAD/ClinVar evidence; preserve only clearly provenance-scoped aggregate data; and ensure paginated MCP responses never silently lose rows after pagination metadata has been calculated.

**Architecture:** Put evidence semantics and public projection in the landscape service, where raw MetaDome entries first become tool data. Model unavailable residue-local gnomAD explicitly as unavailable/null—not zero—and count ClinVar only from the local `ClinVar` record list when that list is present. Move any retained domain-wide totals under a named `meta_domain_evidence` block with scope/provenance labels. Budget pages while constructing their final response payload and recompute `returned`, `next_offset`, and `truncated` from the emitted rows; the generic envelope guard must never delete rows from an already paginated response.

**Tech Stack:** Python 3.12, FastMCP 3.x, Pydantic, pytest/pytest-asyncio, uv, Docker.

**Release decision:** `services/shaping.py` already contains the v0.2.0 minimal-mode repair (minimal removes optional commentary, not essential result data). Treat that as a characterization/redeployment requirement only: do not reimplement or weaken it. The new evidence/pagination contract is a pre-1.0 breaking semantic change, so release this work as `0.3.0` after all probes pass.

## Task 1: Characterise the two false-locality cases and the existing minimal-mode contract

**Files:**

- Modify: `tests/test_metadome_service_views.py`
- Modify: `tests/test_tool_positions.py`
- Modify: `tests/test_tool_landscape.py`
- Inspect: `tests/fixtures/`, `metadome_link/services/shaping.py`

**Step 1: Add service-level failing tests for local evidence.**

In `tests/test_metadome_service_views.py`, construct a small cached/raw position entry with:

- a `domains` member with `normal_variant_count=98` and `pathogenic_variant_count=6`, representing homologous MetaDome aggregate data;
- a local `ClinVar` list containing two records, one missense and one non-missense;
- a second entry where the local `ClinVar` key is absent.

Exercise the same view function used by the public position and variant-count tools. The required assertions are:

~~~python
assert counts["gnomad"]["available"] is False
assert counts["gnomad"]["variant_count"] is None
assert counts["gnomad"]["missense_variant_count"] is None
assert counts["clinvar"] == {
    "available": True,
    "variant_count": 2,
    "missense_variant_count": 1,
    "at_position_count": 2,
}
assert missing_clinvar_counts["clinvar"]["available"] is False
assert missing_clinvar_counts["clinvar"]["variant_count"] is None
~~~

Also assert no local `gnomad` or `clinvar` field equals `98` or `6`. This locks the issue's key distinction: absent local data is not a zero count, and domain-wide values are not residue-local values.

**Step 2: Require explicit aggregate provenance.**

For an entry with domain data, assert any retained aggregate is beneath `meta_domain_evidence`, not `variant_counts`, and includes:

~~~python
assert aggregate["scope"] == "homologous_aligned_residues"
assert aggregate["local_to_requested_residue"] is False
assert "source" in aggregate
~~~

For an entry without domain data, assert `meta_domain_evidence` is absent or `None`, never a fabricated zero aggregate. The test must also assert public position/landscape output no longer exposes raw `domains.*.normal_variant_count` or `pathogenic_variant_count` outside that named block.

**Step 3: Add a characterization test for `minimal`.**

Using an existing real fixture path in `tests/fixtures/`, invoke the current public tool in `response_mode="minimal"`. Assert an essential requested result (position identity, domain membership/evidence when available, and local-evidence availability state) remains present. Assert only optional `data_currency_caveat`/next-command commentary is absent. This should be green before code changes and protects the v0.2.0 fix from regression.

**Step 4: Add public MCP-path assertions.**

In `tests/test_tool_positions.py` and/or `tests/test_tool_landscape.py`, use the repository's existing FastMCP test client to call `get_position`, `get_variant_counts`, and `get_landscape`. Assert structured content—not rendered prose—has the availability/null semantics above. This confirms the repaired service output survives the tool/envelope path.

**Step 5: Run focused tests and observe RED only for the defect.**

~~~bash
uv run pytest tests/test_metadome_service_views.py tests/test_tool_positions.py tests/test_tool_landscape.py -q
~~~

Expected result before implementation: tests that expect unavailable/null gnomAD and provenance-scoped aggregates fail because `variant_counts_for` in `metadome_link/services/landscape.py` currently sums domain totals into `gnomad`/`clinvar`; the existing minimal test remains green.

**Step 6: Commit the characterization tests.**

~~~bash
git add tests/test_metadome_service_views.py tests/test_tool_positions.py tests/test_tool_landscape.py
git commit -m "test: define local evidence semantics"
~~~

## Task 2: Replace false-locality counters with explicit local evidence and provenance-scoped aggregates

**Files:**

- Modify: `metadome_link/services/landscape.py`
- Modify: `metadome_link/services/landscape_views.py`
- Modify: `metadome_link/services/metadome_service.py`
- Modify: `tests/test_metadome_service_views.py`

**Step 1: Make `variant_counts_for` semantically local.**

In `metadome_link/services/landscape.py`, replace the current accumulation of `entry["domains"]` `normal_variant_count` and `pathogenic_variant_count` in `variant_counts_for`. Do not name these values gnomAD or ClinVar anywhere in local count output.

Emit the following stable shapes:

~~~python
{
    "gnomad": {
        "available": False,
        "variant_count": None,
        "missense_variant_count": None,
        "reason": "MetaDome supplies homologous meta-domain aggregates, not residue-local gnomAD evidence.",
    },
    "clinvar": {
        "available": True,  # only when the raw local ClinVar list is present
        "variant_count": len(local_records),
        "missense_variant_count": local_missense_count,
        "at_position_count": len(local_records),
    },
}
~~~

When the local `ClinVar` key is absent or not a list, emit `available: False`, `variant_count: None`, `missense_variant_count: None`, `at_position_count: None`, and a reason that says local ClinVar records are unavailable. An explicitly present empty list is a verified local zero and therefore remains `available: True` with zero counts. Use a named module constant for the gnomAD reason so docs and tests cannot drift.

**Step 2: Project raw domain values only under an explicit aggregate block.**

Add a small public-projection helper in `landscape.py` (or a new focused service module if that keeps either module within the repository's 500-LOC limit). It must remove raw domain count keys from normal position/landscape output and, when raw domain aggregates exist, emit:

~~~python
"meta_domain_evidence": {
    "scope": "homologous_aligned_residues",
    "local_to_requested_residue": False,
    "source": "MetaDome domain alignment aggregate",
    "domains": {
        "<domain-id>": {
            "normal_variant_count": <int-or-null>,
            "pathogenic_variant_count": <int-or-null>,
        },
    },
}
~~~

Retain only actual source values and normalise invalid/missing values to `None`; do not derive gnomAD/ClinVar labels or counts. If existing `get_meta_domain_view` already owns a more detailed aggregate representation, route through one helper so the provenance label is identical in position, landscape, compare, and meta-domain outputs.

**Step 3: Use the public projection in every relevant view.**

Trace `get_position_view`, `get_landscape`, `get_variant_counts_view`, compare views, and `get_meta_domain_view` in `metadome_link/services/landscape_views.py` and `metadome_link/services/metadome_service.py`. Replace `dict(entry)`/raw-domain serialisation with the new projection before response shaping. The public result must not have a second unscoped copy of aggregate counts.

Preserve source accession/position fields and all established response envelopes. Do not modify `shape_record`'s minimal-mode policy: it is already the correct v0.2.0 implementation.

**Step 4: Run focused service and MCP tests, then refactor.**

~~~bash
uv run pytest tests/test_metadome_service_views.py tests/test_tool_positions.py tests/test_tool_landscape.py -q
uv run ruff check metadome_link/services/landscape.py metadome_link/services/landscape_views.py metadome_link/services/metadome_service.py
uv run mypy metadome_link/services
~~~

Expected result: all pass. If a modified module exceeds 500 lines, split the new projection/evidence code into a named service module with direct unit tests rather than disabling the LOC check.

**Step 5: Commit the semantic repair.**

~~~bash
git add metadome_link/services/landscape.py metadome_link/services/landscape_views.py metadome_link/services/metadome_service.py tests
git commit -m "fix: label MetaDome evidence by locality"
~~~

Stage only the test files actually changed; inspect `git diff --cached` before committing.

## Task 3: Budget pages before serialisation and keep pagination metadata exact

**Files:**

- Modify: `metadome_link/services/pagination.py`
- Modify: `metadome_link/services/landscape_views.py`
- Modify: `metadome_link/mcp/envelope.py`
- Modify: `tests/test_services_utils.py`
- Modify: `tests/test_tool_landscape.py`
- Modify: `tests/test_envelope.py`

**Step 1: Add failing pagination-budget tests.**

In `tests/test_services_utils.py`, write a deterministic test against a new/extended pagination helper with an injected small character budget and a payload-builder callback. Supply several valid records large enough that the requested `limit` cannot all fit. Require:

~~~python
assert page["pagination"]["returned"] == len(page["items"])
assert page["pagination"]["next_offset"] == offset + len(page["items"])
assert page["pagination"]["truncated"] is True
assert len(serialise(page)) <= budget
~~~

Fetch the next page using `next_offset` and assert the concatenated emitted IDs have no duplicates or gaps. Include a full requested page case where `returned == limit` and `next_offset` is `None` only when no further source rows remain.

In `tests/test_envelope.py`, add a regression test showing `char_budget_guard` cannot remove an item from a response that contains a pagination contract. The test should fail under the old behavior where post-pagination guarding strips generic list elements while leaving stale metadata.

**Step 2: Implement a final-payload-aware page builder.**

In `metadome_link/services/pagination.py`, retain the ordinary pure slice helper for callers that do not promise a byte/character cap. Add a focused helper (for example `budgeted_page`) that accepts the already validated items, `limit`, `offset`, a final-payload builder callback, and `max_response_chars`.

It must add items in source order, construct the final response shape for each candidate, measure its deterministic JSON serialisation, and stop before the budget is exceeded. After choosing emitted rows, calculate `returned`, `next_offset`, and `truncated` solely from those emitted rows and the total eligible source count. The callback must include envelope/view overhead in its measurement; measuring only a child list is insufficient.

If one individually valid item cannot fit even on an otherwise empty page, return a typed, explicit `response_too_large` tool error naming the item/limit rather than silently dropping it. This is an error condition, not a zero-row page with a misleading next offset.

**Step 3: Apply it to every paginated public view.**

In `landscape_views.py`, refactor `get_landscape`, range/position result assembly, compare output, variant-count lists, and the independent lists in `get_meta_domain_view` so each response which exposes pagination uses `budgeted_page` against its final shaped payload. Respect client `limit`/`offset` limits first, then budget the requested candidate page. For nested meta-domain lists, budget the complete outer payload so two independently valid nested pages cannot jointly exceed the response cap.

Use the same operation for normal and `minimal` modes; minimal may retain more rows because the final payload is smaller, but it must never change which fields make a row essential.

**Step 4: Make the generic envelope guard safe.**

In `metadome_link/mcp/envelope.py`, remove the code path that applies `char_budget_guard` by deleting arbitrary list rows after a view has supplied pagination metadata. Keep the guard only for explicitly non-paginated payloads. If a paginated payload reaches the envelope unexpectedly over budget, return the typed `response_too_large` error with diagnostic fields instead of mutating it. Do not alter successful envelope field names or error-envelope `isError` behavior.

**Step 5: Confirm tests are GREEN.**

~~~bash
uv run pytest tests/test_services_utils.py tests/test_envelope.py tests/test_tool_landscape.py tests/test_tool_positions.py -q
~~~

Expected result: every emitted paginated response is self-consistent, no generic guard removes rows, and the next page has neither duplication nor omission.

**Step 6: Commit pagination correctness separately.**

~~~bash
git add metadome_link/services/pagination.py metadome_link/services/landscape_views.py metadome_link/mcp/envelope.py tests/test_services_utils.py tests/test_tool_landscape.py tests/test_envelope.py
git commit -m "fix: budget MetaDome pages before response shaping"
~~~

## Task 4: Document the semantic contract and prepare v0.3.0

**Files:**

- Modify: `docs/usage.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `uv.lock` only if changed by an intentional lock operation

**Step 1: Document evidence availability and provenance.**

In `docs/usage.md`, document the `available`/`null` contract: absent local gnomAD is unavailable/null, not zero; ClinVar counts are local only when a local source list is present; an empty present local list is a verified zero. Describe `meta_domain_evidence.scope="homologous_aligned_residues"` and `local_to_requested_residue=false`, stating it must not be interpreted as local evidence for the queried residue or gene.

In `docs/architecture.md`, state that pagination is built within the response budget and that metadata describes exactly the rows emitted. Include client guidance to continue from `next_offset` until it is `null`.

**Step 2: Record minimal-mode status and release impact.**

Add a `0.3.0` changelog section that notes: v0.2.0's already-shipped minimal-mode data-preservation fix is retained; local evidence fields now have explicit availability/provenance semantics; and paginated output has exact metadata. Bump the single-source version in `pyproject.toml` from `0.2.0` to `0.3.0`.

**Step 3: Run full local verification and container/conformance checks.**

~~~bash
uv run python -c 'from metadome_link import __version__; assert __version__ == "0.3.0"'
make ci-local
make test-integration
make docker-prod-config
make docker-npm-config
~~~

Run the repository's documented Docker-backed conformance command as well, using its expected local port, and preserve output showing streamable HTTP, response-envelope behavior, and pagination behavior pass. Every command must exit 0 before review.

**Step 4: Commit release documentation.**

~~~bash
git add docs/usage.md docs/architecture.md CHANGELOG.md pyproject.toml uv.lock
git commit -m "docs: explain MetaDome evidence provenance"
~~~

Only stage `uv.lock` if it changed. Keep existing changes not created by this work out of the commit.

## Task 5: Merge, release by immutable digest, redeploy minimal mode, and close #19

**Files:**

- Inspect: `.github/workflows/`, `docker/`, `compose*.yaml`, and deployment manifests

**Step 1: Open a focused PR and review the compatibility impact.**

Open a PR linked with `Fixes #19`. Include examples of the old misleading aggregate values and the new unavailable/local/provenance fields, the pagination no-loss proof, the retained minimal-mode characterization, and all local/CI command output. Call out the `0.3.0` semantic change for downstream users.

**Step 2: Bind the release to the exact reviewed main SHA.**

After approvals and required checks are green, fetch main and record:

~~~bash
git fetch origin main --tags
MERGE_SHA="$(git rev-parse origin/main)"
git show --no-patch --format='%H %s' "$MERGE_SHA"
~~~

Verify `pyproject.toml` at `$MERGE_SHA` says `0.3.0`, then create and push an annotated tag that points to exactly that SHA:

~~~bash
git tag -a v0.3.0 "$MERGE_SHA" -m "MetaDome Link v0.3.0"
git push origin v0.3.0
~~~

**Step 3: Verify the release image is immutable and source-bound.**

From the release build, collect the exact image repository, `v0.3.0` tag, `sha256:...` digest, build workflow URL, and OCI source-revision label. Verify the label equals `$MERGE_SHA`. The tag is an aid for humans; the digest and source SHA are the deployable identity.

**Step 4: Deploy exactly the verified digest.**

Use the standard environment workflow/manifests to set `METADOME_LINK_IMAGE` (or the repository's actual image input) to `image@sha256:...`. Record the deployment revision, then verify health/readiness and exposed version/revision against `0.3.0`/`$MERGE_SHA`. This deployment also redeploys the already-correct minimal-mode implementation; no separate minimal-mode source edit is authorised.

**Step 5: Probe the public MCP endpoint.**

Through the router/public endpoint with normal edge authentication, run and preserve redacted structured responses for:

1. a TP53 local position that has a local ClinVar record, proving local ClinVar counts are derived from that list;
2. a position with no local gnomAD record, proving `gnomad.available == false` and numeric fields are `null`, not zero;
3. a position with a meta-domain, proving any aggregate is under `meta_domain_evidence` with the required scope flags;
4. a landscape response large enough to page, following `next_offset` until null and proving no IDs are duplicated or missing; and
5. an equivalent `response_mode="minimal"` request, proving the essential result remains while optional commentary is absent.

Confirm each response is a successful Response-Envelope result and does not report fabricated local evidence.

**Step 6: Attach closure evidence and close only after production verification.**

Post to #19 with the PR URL, merge SHA, v0.3.0 tag, image digest, deployment revision, CI/conformance output, and redacted probe summaries. Let the linked PR close the issue or close it explicitly only after the exact-digest public deployment passes all five probes.
