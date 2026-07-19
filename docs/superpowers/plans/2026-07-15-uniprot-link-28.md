# UniProt Link #28 Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Release the existing registered-tool boundary repair and make UniProt's high-volume annotation tools truthful, lean, and response-mode aware.

**Architecture:** Keep \`mcp/notfound_guard.py\` as the only protocol dispatch classifier; remove the nonexistent RDF feature filter at the closed vocabulary source; project only fully shaped and counted records. The release is bound from the reviewed main SHA to one GHCR digest and then to deployed health provenance.

**Tech Stack:** Python 3.12, FastMCP 3.x, Pydantic, pytest/respx, uv, Ruff, mypy, Docker Compose, GitHub CLI, GHCR.

---

## File Map

- Modify: \`uniprot_link/services/constants.py\` — remove \`dna_binding\` from \`FEATURE_TYPES\`.
- Modify: \`uniprot_link/services/shaping_annotations.py\` — pure, non-mutating record projectors for features, variants, and diseases.
- Modify: \`uniprot_link/services/sparql_service.py\` — take \`response_mode\` after filtering/counting and before serializing lists.
- Modify: \`uniprot_link/mcp/tools/proteins.py\` — expose a compact-default \`response_mode\` for features, variants, and diseases; retain \`output_schema=None\`.
- Modify: \`tests/unit/test_queries.py\`, \`tests/unit/test_shaping.py\`, \`tests/unit/test_service_and_tools.py\`, and \`tests/unit/mcp/test_notfound_guard.py\`.
- Modify only after behavior is green: \`pyproject.toml\`, \`uv.lock\`, \`CHANGELOG.md\`.
- Read-only contracts: \`.github/workflows/{ci,conformance,container-release}.yml\`, \`container-release.json\`, \`uniprot_link/app.py\`, \`uniprot_link/mcp/notfound_guard.py\`.

## Non-negotiable Contract

- \`dna_binding\` is not advertised and is rejected as \`invalid_input\` with \`field="feature_types"\`; it never becomes an empty success.
- \`minimal\` retains accession plus stable identity/coordinates; compact is default and omits fenced free prose; standard/full retain all current source fields. Membership, \`count\`, and \`truncated.returned\` are computed before projection and remain equal to the emitted rows.
- A failure for a registered tool is \`isError:true\`, \`error_code:"internal"\`, and retains the real tool in \`_meta.tool\`. Only an unknown name is \`not_found\`.
- Existing \`output_schema=None\` is part of the public repair. Do not add output schemas while exposing modes.
- Successful release evidence is the exact merged SHA, a tag pointing to it, an image digest built from it, and deployed health reporting that SHA.

### Task 1: Establish Candidate and Preserve the Existing Boundary Repair

**Files:** read-only inspection.

- [ ] **Step 1: Branch from current remote main.**

Run:

\`\`\`bash
cd /home/bernt-popp/development/uniprot-link
git fetch origin
git switch main
git pull --ff-only origin main
git switch -c fix/mcp-audit-28
git status --short
\`\`\`

Expected: no output from \`git status --short\`. Never reset a non-clean checkout.

- [ ] **Step 2: Prove the known-tool and schema repairs already exist.**

Run:

\`\`\`bash
rg -n "_is_known_tool|_masked_dispatch_result|build_internal_tool_envelope" uniprot_link/mcp/notfound_guard.py
rg -n "output_schema=None" uniprot_link/mcp/tools
rg -n '"dna_binding"' uniprot_link/services/constants.py
\`\`\`

Expected: the first two searches match; the third matches before Task 2. This prevents accidentally reimplementing the release-worthy known-tool fix or reintroducing a schema payload.

- [ ] **Step 3: Capture the stale public behavior as red deployment evidence.**

Run:

\`\`\`bash
BASE=https://uniprot-link.genefoundry.org
curl -fsS "$BASE/health" | jq '{status,version,git_sha}'
curl -sS "$BASE/mcp" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":28,"method":"tools/call","params":{"name":"get_protein_variants","arguments":{"accession":"P04637","limit":5}}}' \
  | tee /tmp/uniprot-28-before.json | jq '.. | objects | select(has("error_code")) | {success,error_code,retryable,recovery_action,_meta}'
\`\`\`

Expected before release: stale production can return the false \`not_found\` frame. Save response only as issue evidence.

### Task 2: Reject the Unsupported Feature Filter Test-First

**Files:**

- Modify: \`tests/unit/test_queries.py\`
- Modify: \`tests/unit/test_service_and_tools.py\`
- Modify: \`uniprot_link/services/constants.py\`

- [ ] **Step 1: Write failing vocabulary and service tests.**

Add:

\`\`\`python
def test_feature_types_do_not_advertise_unavailable_dna_binding() -> None:
    assert "dna_binding" not in FEATURE_TYPES


def test_protein_features_rejects_dna_binding_before_sparql() -> None:
    with pytest.raises(InvalidInputError) as excinfo:
        protein_features("P26367", ["dna_binding"])
    assert excinfo.value.field == "feature_types"


@pytest.mark.asyncio
async def test_get_features_dna_binding_is_invalid_input(service_factory: Any) -> None:
    service = service_factory([])
    with pytest.raises(InvalidInputError) as excinfo:
        await service.get_features("P26367", ["dna_binding"])
    assert excinfo.value.field == "feature_types"
\`\`\`

- [ ] **Step 2: Run the tests red.**

Run: \`uv run pytest tests/unit/test_queries.py tests/unit/test_service_and_tools.py -q\`

Expected: the first test fails because the current closed vocabulary incorrectly includes \`dna_binding\`.

- [ ] **Step 3: Implement the minimal correction.**

Delete only this dictionary entry from \`FEATURE_TYPES\`:

\`\`\`python
"dna_binding": "DNA_Binding_Annotation",
\`\`\`

Do not add an invented RDF mapping, REST fallback, deprecated alias, or a successful “unavailable” empty response. \`protein_features\` already converts the resulting unknown filter into the required parameter-specific \`InvalidInputError\`.

- [ ] **Step 4: Run focused tests green and commit.**

Run: \`uv run pytest tests/unit/test_queries.py tests/unit/test_service_and_tools.py -q\`

Expected: PASS.

\`\`\`bash
git add uniprot_link/services/constants.py tests/unit/test_queries.py tests/unit/test_service_and_tools.py
git commit -m "fix: reject unavailable DNA-binding feature filter"
\`\`\`

### Task 3: Add Lean, Honest Response Modes Test-First

**Files:**

- Modify: \`tests/unit/test_shaping.py\`
- Modify: \`tests/unit/test_service_and_tools.py\`
- Modify: \`uniprot_link/services/shaping_annotations.py\`
- Modify: \`uniprot_link/services/sparql_service.py\`
- Modify: \`uniprot_link/mcp/tools/proteins.py\`

- [ ] **Step 1: Write failing pure projection tests.**

Import \`project_features\`, \`project_variants\`, and \`project_diseases\`, then add:

\`\`\`python
FEATURE = {"type": "binding_site", "begin": 176, "end": 179, "description": {"text": "zinc"}}
VARIANT = {"begin": 72, "end": 72, "notation": "P72R", "variant_type": "substitution",
           "dbsnp": "rs1042522", "diseases": ["Li-Fraumeni"], "description": {"text": "long"}}
DISEASE = {"disease": "Breast cancer", "disease_id": "DI-00001", "mim": "114480",
           "definition": {"text": "long"}, "involvement": {"text": "long"}}

def test_annotation_projectors_keep_stable_identity_in_minimal() -> None:
    assert project_features([FEATURE], "minimal") == [{"type": "binding_site", "begin": 176, "end": 179}]
    assert project_variants([VARIANT], "minimal") == [{"begin": 72, "end": 72, "notation": "P72R", "variant_type": "substitution", "dbsnp": "rs1042522"}]
    assert project_diseases([DISEASE], "minimal") == [{"disease": "Breast cancer", "disease_id": "DI-00001", "mim": "114480"}]

def test_compact_drops_prose_and_full_preserves_it() -> None:
    assert "description" not in project_variants([VARIANT], "compact")[0]
    assert project_variants([VARIANT], "compact")[0]["diseases"] == ["Li-Fraumeni"]
    assert project_variants([VARIANT], "full")[0] == VARIANT
\`\`\`

- [ ] **Step 2: Run red.**

Run: \`uv run pytest tests/unit/test_shaping.py -q\`

Expected: import failure because the three projectors do not yet exist.

- [ ] **Step 3: Implement explicit-copy projectors.**

Add pure helpers in \`shaping_annotations.py\`, never deleting fields from inputs:

\`\`\`python
_FEATURE_MINIMAL = ("type", "begin", "end")
_VARIANT_MINIMAL = ("begin", "end", "notation", "variant_type", "dbsnp")
_VARIANT_COMPACT = _VARIANT_MINIMAL + ("diseases",)
_DISEASE_LEAN = ("disease", "disease_id", "mnemonic", "mim")
\`\`\`

For \`minimal\` select the minimal/lean tuples; for compact select \`_FEATURE_MINIMAL\`, \`_VARIANT_COMPACT\`, and \`_DISEASE_LEAN\`; for standard/full return a shallow \`dict(record)\`. Omit only absent/\`None\` optional fields. This has one source of truth and never removes a legitimate coordinate.

- [ ] **Step 4: Thread the mode only after filtering and counting.**

Use these signatures:

\`\`\`python
async def get_features(..., include_secondary_structure: bool = False,
                       response_mode: str = "compact") -> dict[str, Any]: ...
async def get_variants(self, accession: str, limit: int = 200,
                       disease_associated_only: bool = False,
                       response_mode: str = "compact") -> dict[str, Any]: ...
async def get_diseases(self, accession: str, response_mode: str = "compact") -> dict[str, Any]: ...
\`\`\`

Keep full shaped lists in \`features_full\`, \`variants_full\`, and \`diseases_full\`. Compute \`count\`, truncation, secondary-structure exclusion, untrusted-content enforcement, and next-command input from those full emitted lists. Assign only the corresponding projector result to the wire list. This means every count/pagination value remains true even when description text is suppressed.

Add \`response_mode: ResponseMode = "compact"\` to each matching tool in \`mcp/tools/proteins.py\`, pass it to the service, and amend the signatures/descriptions. Retain every \`output_schema=None\`.

- [ ] **Step 5: Add MCP registration and metadata assertions.**

Call each new mode with the mocked service and assert retained fields. In the existing facade-list test assert the three parameter schemas contain \`response_mode.default == "compact"\` and that their output schemas are absent/\`None\`. Add a truncation fixture whose result has two emitted variants and assert \`payload["count"] == len(payload["variants"]) == payload["truncated"]["returned"]\`.

- [ ] **Step 6: Run green and commit.**

Run:

\`\`\`bash
uv run ruff format uniprot_link tests
uv run pytest tests/unit/test_shaping.py tests/unit/test_service_and_tools.py tests/unit/test_discovery_surface.py -q
git add uniprot_link/services/shaping_annotations.py uniprot_link/services/sparql_service.py \
  uniprot_link/mcp/tools/proteins.py tests/unit/test_shaping.py tests/unit/test_service_and_tools.py \
  tests/unit/test_discovery_surface.py
git commit -m "feat: add lean response modes to UniProt annotations"
\`\`\`

Expected: PASS.

### Task 4: Lock the Known-Tool Error Boundary

**Files:** \`tests/unit/mcp/test_notfound_guard.py\`; read-only implementation \`uniprot_link/mcp/notfound_guard.py\`.

- [ ] **Step 1: Add a raw protocol characterization regression.**

Use the existing \`_raw_request\` harness and a temporary registered \`crash_known_tool\` that raises \`RuntimeError\`. Assert the raw response has all four facts:

\`\`\`python
assert '"isError":true' in response
assert '"error_code":"internal"' in response
assert '"error_code":"not_found"' not in response
assert '"tool":"crash_known_tool"' in response
\`\`\`

- [ ] **Step 2: Run and commit without changing the already-correct handler.**

Run:

\`\`\`bash
uv run pytest tests/unit/mcp/test_notfound_guard.py -q
git add tests/unit/mcp/test_notfound_guard.py
git commit -m "test: lock known UniProt tool dispatch errors"
\`\`\`

Expected: PASS. If it fails, fix only the \`_is_known_tool\`/masked-dispatch path, retain fixed name-free messages, then rerun red/green.

### Task 5: Version, Verify, Merge, and Release an Exact Artifact

**Files:** \`pyproject.toml\`, \`uv.lock\`, \`CHANGELOG.md\`, GitHub/deployment state.

- [ ] **Step 1: Prepare the version only after all behavior is green.**

Set \`version = "5.1.0"\`, run \`uv lock\`, and add a changelog entry for unavailable-filter removal, lean modes, and release of the existing boundary/output-schema repair.

- [ ] **Step 2: Run complete local verification.**

Run:

\`\`\`bash
uv run pytest tests/unit/test_version_single_source.py -q
make ci-local
\`\`\`

Expected: both exit 0.

- [ ] **Step 3: Commit, PR, and require checks on the precise reviewed head.**

\`\`\`bash
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore(release): prepare UniProt link 5.1.0"
git push -u origin fix/mcp-audit-28
gh pr create --base main --head fix/mcp-audit-28 --title "fix: remediate UniProt MCP audit issue 28" --fill
HEAD_SHA=$(gh pr view --json headRefOid -q .headRefOid)
gh api "repos/berntpopp/uniprot-link/commits/$HEAD_SHA/check-runs" --jq '.check_runs[] | {name,status,conclusion}'
\`\`\`

Expected: CI, conformance, container, and security checks succeeded for exactly \`$HEAD_SHA\`.

- [ ] **Step 4: Merge, tag exactly main, then deploy only its digest.**

\`\`\`bash
gh pr merge --squash --delete-branch
git fetch origin main --tags
MAIN_SHA=$(git rev-parse origin/main)
git tag -a v5.1.0 "$MAIN_SHA" -m "uniprot-link 5.1.0"
git push origin v5.1.0
test "$(git rev-parse v5.1.0^{commit})" = "$MAIN_SHA"
\`\`\`

Wait for the tag-triggered Container release, resolve its GHCR manifest digest, and deploy \`UNIPROT_LINK_IMAGE=ghcr.io/berntpopp/uniprot-link@sha256:<digest>\`. Never deploy \`v5.1.0\` or \`latest\` by tag. If the tag already points at a different commit, stop rather than force-moving it.

### Task 6: Public Proof and Issue Closure

**Files:** GitHub issue #28 evidence only.

- [ ] **Step 1: Verify provenance before semantic probes.**

\`\`\`bash
BASE=https://uniprot-link.genefoundry.org
curl -fsS "$BASE/health" | tee /tmp/uniprot-28-health.json | jq .
jq -e --arg sha "$MAIN_SHA" '.version == "5.1.0" and (.git_sha | startswith($sha[0:7]))' /tmp/uniprot-28-health.json
\`\`\`

Expected: exit 0; version without matching revision is not proof.

- [ ] **Step 2: Run public MCP acceptance probes.**

Use \`tools/list\`, then calls for TP53 features, TP53 variants, and PAX6 \`feature_types:["dna_binding"]\`. Assert: both TP53 lists are nonempty; the PAX6 request is \`invalid_input\` naming \`feature_types\`; no advertised feature enum contains \`dna_binding\`; and a disease-associated SCN1A variants \`compact\` response is smaller in bytes than \`full\` while each \`count\`/truncation field equals its emitted array length.

- [ ] **Step 3: Close with immutable evidence.**

Comment the exact main SHA, tag, GHCR digest, exact-SHA check-run URLs, health JSON summary, and acceptance results on #28. Close only when all probes pass; stale SHA, a registered-tool \`not_found\`, advertised \`dna_binding\`, or dishonest pagination keeps the issue open.
