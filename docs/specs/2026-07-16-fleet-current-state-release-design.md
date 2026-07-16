# Fleet Current-State Release Design

- **Date:** 2026-07-16
- **Status:** Approved for execution
- **Scope:** Every GeneFoundry `*-link` service plus the router and Strato deployment lock.

## Summary

Bring the fleet to the current reviewed GitHub state without treating a default branch as a
release artifact. Every deployed application image must come from a signed immutable release,
the router's signed inventory must agree with every deployed backend definition, and Strato must
attest the final running fleet.

The rollout has two application waves and one coordinated deployment wave. The first application
wave publishes the eleven default heads that already have a reviewed version/changelog and green
checks. The second remediates five heads that are intentionally not safe to publish yet. gnomAD
v9.0.0 is already released, but is a breaking MCP-contract migration: it is deployed backend-first
and becomes visible to the router only with a new signed router baseline.

## Immutable-release rule

For every service, the release tag must be annotated and signed, the protected Container Release
workflow must publish the image and evidence, and the resulting manifest must carry source
revision, image digest, and normalized MCP-definition digest. Strato uses `make pin`, not a hand
edited lockfile, so it verifies SLSA provenance before recording those values. A default branch or
a mutable container tag is never a deployment input.

## Router self-provenance

The router's packaged release-candidate inventory is a proof about **backends**. It cannot also
embed the immutable manifest of the router image that will package it: that would require a tag to
be created from a commit that already contains the tag's post-build image digest. The current
inventory exposes this impossible self-reference by naming an obsolete router release.

Before the fleet reconciliation, remove that `router` manifest field and make the candidate schema
backend-only. The router itself is independently proven by its protected release manifest and by
Strato's lockfile-to-running-digest attestation. This leaves no stale self-manifest in a new router
image while retaining the fail-closed backend definition/revision checks that actually guard the
router's startup.

## Current classification

| Class | Services | Required action |
| --- | --- | --- |
| Release-ready | ClinVar 0.5.0, GenCC 0.8.0, GeneReviews 5.1.0, GTEx 3.1.0, HGNC 2.1.0, MAVeDB 0.5.0, MGI 0.6.0, MONDO 0.4.0, PanelApp 0.6.0, SpliceAI Lookup 4.0.0, VEP 1.1.0 | Move the generated behaviour-gate vendor note from `Unreleased` into the existing version section, run the repository CI, review, merge, sign/tag/release. |
| Requires remediation | ClinGen 4.0.0, HPO 0.4.0, MetaDome 0.2.0, Orphanet 0.4.0, UniProt 5.0.0 | Fix and test the tracked contract/security defect first; then follow the same release flow. |
| Already released; coordinated deployment | gnomAD 9.0.0 | Pin and deploy the signed backend release, regenerate/review router inventory, baseline and discoverability catalog, then release/pin/deploy router. |
| Already current | AutoPVS1 4.1.1, LitVar 6.0.0, PubTator 7.1.4, STRING-db 4.1.0 | Do not publish a duplicate release. Include their existing manifest, lock, runtime image, and router-inventory entries in the final full-fleet attestation. |
| Current router | genefoundry-router 0.6.12 | Keep serving during the gnomAD backend-first gap. Replace it only with the final re-baselined router release. |

## Blocker decisions

- **ClinGen:** v4 uses data schema 2 while production is pinned to schema 1. Publish a schema-2
  data release only after fixing its stale `previous_known_good_digest` rollback reference
  ([#45](https://github.com/berntpopp/clingen-link/issues/45)); then deploy app and data together.
- **HPO and Orphanet:** replace in-process external data bootstrap with the hardened init-sidecar
  pattern before publication ([HPO #23](https://github.com/berntpopp/hpo-link/issues/23),
  [Orphanet #23](https://github.com/berntpopp/orphanet-link/issues/23)).
- **MetaDome:** resolve the cross-gene count/provenance and pagination correctness claim before
  v0.2.0 ([#19](https://github.com/berntpopp/metadome-link/issues/19)).
- **UniProt:** repair the default feature/variant calls, DNA-binding false negatives, and compact
  response mode rather than publishing the current partial error remapping ([#28](https://github.com/berntpopp/uniprot-link/issues/28)).

## Deployment sequencing

1. Publish reviewed backend releases. Do not modify router or Strato locks before each release
   manifest and image digest exist.
2. Confirm `GF_POLL_INTERVAL=0` in the deployed router before gnomAD moves. Its current
   drift mode is enforce; restarting a router with the old baseline after gnomAD v9 goes live
   would fail startup, and polling would quarantine changed tools.
3. On Strato branches, `make pin` every newly released backend and deploy the reviewed lockfile
   batches. gnomAD goes first; keep the old router running during that gap. Deploy and health-check
   all remaining newly released backends before beginning the router baseline work.
4. From that fully deployed backend fleet, run Strato's dedicated `attest --backend-prebaseline
   --json` mode and save its result. This mode deliberately permits only stale router pins while
   the old router is still serving; it must still fail closed on incomplete backend coverage, any
   runtime/lock/revision mismatch, or any unconfined container. Pass that exact artifact to
   `export-release-manifests --attestation …`; the exporter must match every locked
   version/digest/revision/definition digest and emit the exact backend application-release
   manifests. In the router release worktree, use that artifact as
   `ci/fleet-application-releases.json`, then run `make release-candidate` with both
   `RELEASE_MANIFESTS=ci/fleet-application-releases.json` and a reviewed `IDENTITY`, followed by
   `make snapshot-baseline RELEASE_CANDIDATE_INVENTORY=ci/release-candidate-inventory.json` and
   `make snapshot-catalog`. This protected release shell must supply the existing
   `GF_PUBTATOR_TOKEN` as an exported environment variable without writing or printing it, so the
   complete candidate probe can authenticate to PubTator. Inspect all generated diffs. Release a signed router patch only if
   the candidate inventory exactly matches every live backend manifest, including already-current
   AutoPVS1, LitVar, PubTator, and STRING-db.
5. Pin/deploy that router release in Strato, perform only `nginx -t && nginx -s reload` for NPM,
   then run `make attest`. For every backend, attestation must prove the lock, running digest, and
   router inventory provenance agree. For the router, it must prove the lock, running digest, and
   protected immutable release manifest agree. All application containers must be confined.

## Success criteria

- Every service default branch is represented by a reviewed immutable GitHub release, or no longer
  contains unreleased fixes because its remediation PR has merged and been released.
- Every release has a green protected workflow and signed tag.
- For every backend, Strato's lock, running container, and router inventory match the same
  version/revision/digest/definitions hash; for the router, its lock and running container match
  its protected immutable release manifest.
- The pre-baseline backend verifier fails closed on every control other than the intentionally
  stale router pin, and its exact JSON artifact is the only input accepted by manifest export.
- Full fleet health and confinement attestation pass.
