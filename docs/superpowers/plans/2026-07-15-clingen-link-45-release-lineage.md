# ClinGen #45 — Previous-Known-Good Release Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the privileged ClinGen data publisher refuse a candidate whose declared previous-known-good digest is not the immediately preceding published `data-clingen-*` release, before it can create a draft or upload an asset.

**Architecture:** The unprivileged `build` job continues to transform data and emits a sealed handoff containing a candidate manifest. The isolated `publish` job uses its already checksum-pinned `gh` binary to select the newest non-draft, non-prerelease, non-candidate `data-clingen-*` release, downloads only that prior manifest, normalizes both artifact digests, and performs a fail-closed lineage comparison before the existing `gh release create --draft` branch. The all-zero SHA-256 is the sole explicit first-publication sentinel; it is valid only when no prior published release exists.

**Tech Stack:** Python 3.12, pytest, PyYAML, GitHub Actions, pinned GitHub CLI 2.96.0, `jq`, GitHub Releases.

---

**Repository and branch:** `/home/bernt-popp/development/clingen-link`, new branch `fix/data-release-lineage-45` from current `origin/main`. The plan document lives in the router only; all implementation paths below are relative to the ClinGen repository.

## Fixed contract values

- Candidate tags match `^data-clingen-[0-9]{4}-[0-9]{2}-[0-9]{2}$` and the candidate tag is never a predecessor of itself.
- A predecessor is a published (`isDraft == false`, `isPrerelease == false`) `data-clingen-*` release with the latest `publishedAt`; drafts, prereleases, unrelated tags, and the candidate tag are ignored.
- Candidate and predecessor digests are normalized by removing one optional `sha256:` prefix, lowercasing, and requiring exactly 64 hexadecimal characters.
- First publication requires `previous_known_good_digest` to be `sha256:0000000000000000000000000000000000000000000000000000000000000000`. Any non-sentinel candidate with no predecessor fails. Any sentinel candidate with a predecessor fails.
- Current non-bootstrap candidates must name the immediate predecessor artifact digest, not a manually remembered or older digest. The build job remains `permissions: {contents: read}` and never receives `GH_TOKEN`, `id-token`, or write permission.

## File map

| File | Change | Responsibility |
|---|---|---|
| `.github/workflows/data-refresh.yml` | Modify | Add a manually supplied, SHA-validated candidate lineage input; replace the hard-coded manifest lineage value; preflight predecessor before `gh release create` or `gh release upload`. |
| `tests/unit/test_data_refresh_workflow.py` | Modify | Execute the extracted lineage preflight against fake GitHub CLI fixtures and assert the workflow order/permissions. |
| `docs/data.md` | Modify | State the first-publication sentinel and the required reviewed re-pin procedure for every later manual publish. |

### Task 1: Add executable failing lineage-preflight tests

**Files:**
- Modify: `tests/unit/test_data_refresh_workflow.py`

- [ ] **Step 1: Add test helpers and three fixture manifests.**

  Add these imports and helpers below `HANDOFF_DIR` so tests invoke the *same* shell extracted from the workflow rather than an imitation:

  ```python
  import json
  import os
  import subprocess

  ZERO_DIGEST = "0" * 64
  PRIOR_DIGEST = "b389b1dbea7921d414c647fdc88ce19ff81bcf27acae39a7ce8b150ee0a2fc17"
  CANDIDATE_TAG = "data-clingen-2026-07-15"

  def _manifest(previous: str) -> str:
      return json.dumps(
          {
              "artifact": {"sha256": f"sha256:{PRIOR_DIGEST}"},
              "previous_known_good_digest": previous,
          }
      )

  def _lineage_script() -> str:
      for step in _steps("publish"):
          if step.get("name") == "Verify prior lineage before release mutation":
              return str(step["run"])
      raise AssertionError("missing lineage preflight step")
  ```

  Add `_run_lineage_preflight(tmp_path, *, releases, candidate_previous)` that creates `$RUNNER_TEMP/data-release/data-release-manifest.json`, writes a fake executable at `$RUNNER_TEMP/gh`, and runs `_lineage_script()` with `TAG=CANDIDATE_TAG`, `RUNNER_TEMP`, `GH_TOKEN=test-token`, and `PATH` inherited. Its fake `gh` must implement exactly these calls:

  ```bash
  gh release list --limit 100 --json tagName,isDraft,isPrerelease,publishedAt
  gh release download "$prior_tag" --pattern data-release-manifest.json --dir "$RUNNER_TEMP/prior-release"
  ```

  `release list` prints the JSON fixture from `FAKE_RELEASES_JSON`; `release download` copies `FAKE_PRIOR_MANIFEST` to the requested directory. It must exit non-zero for every other command so a test proves preflight does not create or upload a release.

- [ ] **Step 2: Write the failing behavior tests.**

  ```python
  def test_lineage_preflight_accepts_explicit_first_publication(tmp_path: Path) -> None:
      completed = _run_lineage_preflight(
          tmp_path,
          releases=[],
          candidate_previous=f"sha256:{ZERO_DIGEST}",
      )
      assert completed.returncode == 0, completed.stderr

  def test_lineage_preflight_accepts_only_immediately_previous_published_release(
      tmp_path: Path,
  ) -> None:
      releases = [
          {"tagName": "data-clingen-2026-07-14", "isDraft": False,
           "isPrerelease": False, "publishedAt": "2026-07-14T12:00:00Z"},
          {"tagName": "data-clingen-2099-01-01", "isDraft": True,
           "isPrerelease": False, "publishedAt": "2099-01-01T00:00:00Z"},
          {"tagName": CANDIDATE_TAG, "isDraft": False,
           "isPrerelease": False, "publishedAt": "2026-07-15T12:00:00Z"},
      ]
      completed = _run_lineage_preflight(
          tmp_path, releases=releases, candidate_previous=PRIOR_DIGEST
      )
      assert completed.returncode == 0, completed.stderr

  def test_stale_lineage_fails_before_any_release_mutation(tmp_path: Path) -> None:
      completed = _run_lineage_preflight(
          tmp_path,
          releases=[{"tagName": "data-clingen-2026-07-14", "isDraft": False,
                     "isPrerelease": False, "publishedAt": "2026-07-14T12:00:00Z"}],
          candidate_previous="a" * 64,
      )
      assert completed.returncode != 0
      assert "re-pin previous_known_good_digest" in completed.stderr
      assert "release create" not in completed.stdout
      assert "release upload" not in completed.stdout
  ```

  Also add structural assertions that the `build` job permissions remain exactly `{"contents": "read"}`, the `publish` job contains the named preflight step, and `body.index("Verify prior lineage before release mutation") < body.index("release create")` in the concatenated publish script.

- [ ] **Step 3: Run the targeted tests and observe the intended red state.**

  Run:

  ```bash
  uv run pytest tests/unit/test_data_refresh_workflow.py -q
  ```

  Expected before implementation: the three new tests fail with `AssertionError: missing lineage preflight step`; the pre-existing workflow tests pass.

- [ ] **Step 4: Commit the red test only.**

  ```bash
  git add tests/unit/test_data_refresh_workflow.py
  git commit -m "test: specify ClinGen data release lineage"
  ```

### Task 2: Bind the candidate handoff to an explicit lineage declaration

**Files:**
- Modify: `.github/workflows/data-refresh.yml`

- [ ] **Step 1: Add this workflow-dispatch input under `redistribution_review`.**

  ```yaml
      previous_known_good_digest:
        description: >-
          SHA-256 of the immediately preceding published data-clingen release;
          use sha256:0000000000000000000000000000000000000000000000000000000000000000 only for the first publication.
        required: true
        default: sha256:e0204a40541e82fb86cf4725a2b5fa9edc5e0eec838ada9564fa8de973c51626
        type: string
  ```

  This is a reviewed declaration, not a credential. Scheduled runs still build and seal a handoff but never enter the publish job; a manual publish must explicitly carry the lineage value.

- [ ] **Step 2: Pass the value into the credential-free build environment and validate it before manifest creation.**

  In `Build snapshot and release evidence`, extend `env:` exactly as follows:

  ```yaml
          PREVIOUS_KNOWN_GOOD_DIGEST: ${{ inputs.previous_known_good_digest || 'sha256:e0204a40541e82fb86cf4725a2b5fa9edc5e0eec838ada9564fa8de973c51626' }}
  ```

  In the first inline Python block, before constructing `manifest`, replace the hard-coded `previous_known_good_digest` with this validation:

  ```python
          previous = os.environ["PREVIOUS_KNOWN_GOOD_DIGEST"].strip().lower()
          if previous.startswith("sha256:"):
              previous = previous.removeprefix("sha256:")
          if len(previous) != 64 or any(char not in "0123456789abcdef" for char in previous):
              raise SystemExit("PREVIOUS_KNOWN_GOOD_DIGEST must be a SHA-256 digest")
  ```

  Then set the manifest value to `"previous_known_good_digest": f"sha256:{previous}",`. Do not add GitHub API calls, a checkout, a token, or write permissions to `build`.

- [ ] **Step 3: Run the static and targeted tests.**

  Run:

  ```bash
  uv run pytest tests/unit/test_data_refresh_workflow.py -q
  ```

  Expected at this point: only tests expecting the new publish preflight still fail; the build-permission assertion remains green.

### Task 3: Preflight prior release lineage before draft creation

**Files:**
- Modify: `.github/workflows/data-refresh.yml`

- [ ] **Step 1: Add the named step immediately after `Install checksum-pinned GitHub CLI` and before `Verify handoff and create matching draft`.**

  ```yaml
      - name: Verify prior lineage before release mutation
        env:
          GH_TOKEN: ${{ github.token }}
          TAG: ${{ needs.build.outputs.tag }}
        run: |
          set -euo pipefail
          gh="$RUNNER_TEMP/gh"
          cd "$RUNNER_TEMP/data-release"
          normalize_digest() {
            local value="${1#sha256:}"
            value="${value,,}"
            [[ "$value" =~ ^[0-9a-f]{64}$ ]] || {
              echo "lineage digest is not a SHA-256 value" >&2
              exit 1
            }
            printf '%s' "$value"
          }
          [[ "$TAG" =~ ^data-clingen-[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
            echo "candidate data tag is invalid" >&2
            exit 1
          }
          prior_tag="$("$gh" release list --limit 100 \
            --json tagName,isDraft,isPrerelease,publishedAt \
            --jq "[.[] | select(.isDraft == false and .isPrerelease == false and .tagName != \"$TAG\" and (.tagName | test(\"^data-clingen-[0-9]{4}-[0-9]{2}-[0-9]{2}$\")))] | sort_by(.publishedAt) | last | .tagName // empty")"
          candidate_previous="$(normalize_digest "$(jq -er '.previous_known_good_digest' data-release-manifest.json)")"
          if [[ -z "$prior_tag" ]]; then
            [[ "$candidate_previous" = "0000000000000000000000000000000000000000000000000000000000000000" ]] || {
              echo "no published predecessor exists; set previous_known_good_digest to the explicit first-publication sentinel" >&2
              exit 1
            }
            exit 0
          fi
          [[ "$prior_tag" =~ ^data-clingen-[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || {
            echo "latest predecessor tag is invalid" >&2
            exit 1
          }
          rm -rf "$RUNNER_TEMP/prior-release"
          mkdir -p "$RUNNER_TEMP/prior-release"
          "$gh" release download "$prior_tag" --pattern data-release-manifest.json \
            --dir "$RUNNER_TEMP/prior-release"
          prior_digest="$(normalize_digest "$(jq -er '.artifact.sha256' "$RUNNER_TEMP/prior-release/data-release-manifest.json")")"
          [[ "$candidate_previous" = "$prior_digest" ]] || {
            echo "candidate previous_known_good_digest does not match $prior_tag; re-pin previous_known_good_digest and rebuild the handoff" >&2
            exit 1
          }
  ```

  Keep the existing checksum verification as the first release-mutating-step predecessor. The preflight must neither create a release nor alter an existing draft; its only write locations are `$RUNNER_TEMP/prior-release` and the already-downloaded handoff directory.

- [ ] **Step 2: Extend the fake `gh` helper to make a release mutation observable.**

  In `_run_lineage_preflight`, make any fake invocation other than `release list` or `release download` append its full argv to `mutations.log` and exit `97`. Assert `mutations.log` does not exist for the stale mismatch test. Add a test where the fake predecessor manifest has `artifact.sha256: "sha256:" + PRIOR_DIGEST.upper()`; it must pass, proving canonical normalization is intentional.

- [ ] **Step 3: Run the focused proof.**

  Run:

  ```bash
  uv run pytest tests/unit/test_data_refresh_workflow.py -q
  ```

  Expected: all workflow tests pass, including explicit first publication, valid predecessor, uppercase/prefix normalization, and stale lineage failure before any mutation.

- [ ] **Step 4: Commit workflow and test implementation.**

  ```bash
  git add .github/workflows/data-refresh.yml tests/unit/test_data_refresh_workflow.py
  git commit -m "fix: verify ClinGen data release lineage before publish"
  ```

### Task 4: Document the reviewed publication procedure

**Files:**
- Modify: `docs/data.md`
- Test: `tests/unit/test_data_refresh_workflow.py`

- [ ] **Step 1: Add a `## Data release lineage` section to `docs/data.md`.**

  It must state these exact operational steps:

  1. Run the data workflow with `publish=false` and inspect `data-release-manifest.json` from the sealed artifact.
  2. Use `gh release list --repo berntpopp/clingen-link --limit 100 --json tagName,isDraft,isPrerelease,publishedAt` to identify the newest published, non-prerelease `data-clingen-*` tag.
  3. Download that tag's `data-release-manifest.json`, copy its `artifact.sha256` into `previous_known_good_digest`, and start a fresh manual build/publish attempt; do not alter an existing handoff.
  4. Use the all-zero SHA-256 only when no published predecessor exists. A mismatch is a deliberate stop requiring a reviewed re-pin, never a draft deletion or asset overwrite.

- [ ] **Step 2: Add a documentation assertion.**

  ```python
  def test_data_docs_explain_lineage_repin_and_first_publication() -> None:
      text = (WORKFLOW.parents[2] / "docs" / "data.md").read_text(encoding="utf-8")
      assert "previous_known_good_digest" in text
      assert "first publication" in text.lower()
      assert "re-pin" in text
  ```

- [ ] **Step 3: Verify and commit.**

  Run:

  ```bash
  uv run pytest tests/unit/test_data_refresh_workflow.py -q
  make ci-local
  ```

  Expected: workflow and documentation tests pass; `make ci-local` reports green format, lint, LOC, README, strict type check, and fast unit suite.

  ```bash
  git add docs/data.md tests/unit/test_data_refresh_workflow.py
  git commit -m "docs: document ClinGen data release lineage"
  ```

### Task 5: PR, release, deployment, and issue-close evidence

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add the issue entry and open the PR.**

  Add an unreleased `Fixed` entry saying that data publication validates the immediately preceding published manifest before release mutation. Push `fix/data-release-lineage-45`, create a draft PR referencing `Fixes #45`, and wait for every required GitHub check on the exact head SHA to pass.

- [ ] **Step 2: Review and merge only the checked SHA.**

  Record the PR URL, review approval, final head SHA, merge SHA, and GitHub checks URL. Merge after checks are green; run `git fetch origin main`, set `MERGE_SHA="$(git rev-parse origin/main)"`, and prove `git merge-base --is-ancestor "$MERGE_SHA" origin/main` exits `0`.

- [ ] **Step 3: Perform an evidence-bearing manual data publication.**

  Run the `Immutable ClinGen data release` workflow on the merged SHA with `publish=true`, an affirmative redistribution review, and the predecessor digest copied from the immediately prior published manifest. Capture the run URL. It must show:

  - `build` has only `contents: read` and completes without credentials or release mutation;
  - lineage preflight downloads the actual immediate predecessor manifest and succeeds;
  - the candidate release is created draft-first, attested, then published once;
  - the new manifest's `previous_known_good_digest` equals the prior release's `artifact.sha256` after normalization.

- [ ] **Step 4: Verify the public artifact and close the issue.**

  Run immediately after publication to resolve the newest published immutable data tag:

  ```bash
  NEW_TAG="$(gh release list --repo berntpopp/clingen-link --limit 100 --json tagName,isDraft,isPrerelease,publishedAt --jq '[.[] | select(.isDraft == false and .isPrerelease == false and (.tagName | test("^data-clingen-[0-9]{4}-[0-9]{2}-[0-9]{2}$"))] | sort_by(.publishedAt) | last | .tagName')"
  gh release download "$NEW_TAG" --repo berntpopp/clingen-link \
    --pattern data-release-manifest.json --dir /tmp/clingen-45
  jq -r '.previous_known_good_digest' /tmp/clingen-45/data-release-manifest.json
  ```

  Expected: the printed value is the normalized digest from the immediately preceding published data release. Post the merge SHA, workflow URL, new tag, predecessor tag/digest, manifest digest, and command output summary to #45, then close it.
