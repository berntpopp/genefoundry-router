# Fleet Current-State Release Implementation Plan

> **For agentic workers:** execute the tasks in order. Do not publish a raw default-branch head,
> hand-edit `fleet.lock.yaml`, or restart the router during the gnomAD backend-first gap.

**Goal:** Publish every reviewed current GeneFoundry service version, deploy every resulting
immutable image, and prove the complete fleet state with router inventory and Strato attestation.

**Design:** [`2026-07-16-fleet-current-state-release-design.md`](../specs/2026-07-16-fleet-current-state-release-design.md).

## Task 0 — Remove recursive router self-provenance and export verified backend manifests

1. In the router, write a failing release-candidate test proving an inventory with `identity` and
   the complete backend manifest map is valid without a router application manifest, while an
   incomplete backend map remains rejected. Update the candidate parser/validator, fixtures, and
   documentation so the packaged inventory is explicitly backend-only.
2. In Strato, first write failing tests for `attest --backend-prebaseline --json`: it permits an
   `AHEAD OF ROUTER PIN` mismatch only, but exits nonzero for missing backend coverage, missing or
   mismatched lock/runtime/revision values, an unconfined container, or an unreadable inventory.
   Its JSON contains only complete verified backend provenance. Then write a failing test for
   `export-release-manifests --attestation <path>`: it accepts only that successful artifact and
   emits the complete backend manifest map from the *locked* releases after matching lock
   version/digest/revision/definition digest to each downloaded immutable application manifest.
   A missing, mismatched, or unverified backend must fail closed.
3. Implement the smallest changes to make those tests pass. The exporter command is
   `python scripts/manage.py export-release-manifests --attestation <path> --out <path>`; its JSON
   output is `{ "backends": { "<namespace>": <application-release-manifest>, ... } }`. It must
   cover Strato's exact enabled `*-link` project set; the following router `make release-candidate`
   independently requires that set to cover `servers.yaml` exactly. Router and Strato CI must pass
   before release work continues.

## Task 1 — Release-ready backend metadata batch

For ClinVar, GenCC, GeneReviews, GTEx, HGNC, MAVeDB, MGI, MONDO, PanelApp, SpliceAI Lookup,
and VEP:

1. Re-fetch the default branch and confirm it is still the audited SHA, with no open blocking PR.
2. Move only the generated behaviour-gate vendor note from `Unreleased` into the existing dated
   release section; do not alter application behaviour or silently fold in later commits.
3. Run the repository's full CI and release checks.
4. Obtain a focused spec and code-quality review, merge the release-prep PR, then create and
   locally verify the signed annotated SemVer tag.
5. Wait for the protected Container Release workflow and record its manifest SHA, image digest,
   source revision, and definitions digest.

## Task 2 — Remediate application blockers

1. **ClinGen:** test and fix schema-2 data publication plus rollback-digest handling; publish its
   data artifact before the v4 app release.
2. **HPO / Orphanet:** test-first migrate bootstrap to the hardened init-sidecar pattern and
   verify the rendered base + NPM deployment model.
3. **MetaDome:** test-first correct filter count/provenance/pagination behaviour so response
   claims are honest.
4. **UniProt:** test-first repair default feature/variant results, DNA-binding detection, and
   compact response mode.
5. Review each remediation before repeating Task 1's release procedure.

## Task 3 — Deploy every released backend

1. Verify gnomAD v9.0.0's signed tag and release manifest. Confirm deployed router
   `GF_POLL_INTERVAL=0` before the backend move.
2. Use Strato `make pin` to capture gnomAD v9 after provenance verification; review, merge,
   deploy, and health-check it without restarting the previous router.
3. Use `make pin` for every other newly released backend; review and merge the lockfile changes,
   deploy each coordinated batch, and health-check every service. Do not hand-edit the lockfile.
4. Preserve existing AutoPVS1 4.1.1, LitVar 6.0.0, PubTator 7.1.4, and STRING-db 4.1.0 pins;
   verify them alongside the changed services rather than creating duplicate releases.

## Task 4 — Router baseline and release

1. From fresh Strato main, run the fail-closed handoff and stop on either nonzero status:

   ```bash
   set -euo pipefail
   python scripts/manage.py attest --backend-prebaseline --json \
     > /tmp/fleet-backend-prebaseline.json &&
   python scripts/manage.py export-release-manifests \
     --attestation /tmp/fleet-backend-prebaseline.json \
     --out /tmp/fleet-application-releases.json
   ```

   Do not use a release list or manifest set captured before backend deployment. The first command
   is permitted to observe only stale router pins; every other integrity or confinement failure
   aborts the handoff.
2. In the isolated router release worktree, copy the exported artifact to
   `ci/fleet-application-releases.json`. In the protected release shell, make the existing
   `GF_PUBTATOR_TOKEN` available without echoing, writing, or committing it; fail immediately if
   it is absent. Export it only inside a subshell containing the complete candidate/baseline/catalog
   capture, so it is not inherited by later CI or release commands. Then run:

   ```bash
   (
     set -euo pipefail
     : "${GF_PUBTATOR_TOKEN:?missing protected PubTator service token}"
     export GF_PUBTATOR_TOKEN
     make release-candidate \
       RELEASE_MANIFESTS=ci/fleet-application-releases.json \
       IDENTITY=fleet-2026-07-16-current
     make snapshot-baseline RELEASE_CANDIDATE_INVENTORY=ci/release-candidate-inventory.json
     make snapshot-catalog
   )
   ```

   Inspect the full normalized MCP diff and the candidate's source/digest/definition provenance.
   Commit the reviewed release artifact, inventory, baseline, and catalog together.
3. Run router CI, release the resulting patch from a signed tag, wait for its protected release
   workflow, and record its immutable manifest. The new router is proven by that manifest plus
   Strato's lock/runtime attestation; it is not recursively embedded in its backend inventory.

## Task 5 — Router deployment and final proof

1. Use Strato `make pin` for the new router release; review/merge/deploy its generated lockfile
   change and health-check it.
2. Verify NPM with `nginx -t && nginx -s reload` only; never restart it as a release shortcut.
3. Run `make attest` from fresh Strato main. It must prove each backend lock/runtime/inventory
   provenance agrees, including AutoPVS1, LitVar, PubTator, and STRING-db; and that the router's
   lock/runtime image agrees with its protected immutable release manifest. Every application
   container must be confined.
