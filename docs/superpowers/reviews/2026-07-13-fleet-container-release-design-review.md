# Fleet Container Release Design — Adversarial Review Record

**Artifact reviewed:**
`docs/superpowers/specs/2026-07-13-fleet-container-release-design.md`

**Reviewer requested by the maintainer:** Claude Code,
`claude-opus-4-8`, effort `xhigh`

**Review date:** 2026-07-13

## Invocation

The completed review used Claude Code in non-interactive print mode with tools
disabled so the submitted design text was the complete review boundary. The
model and effort were selected explicitly as `claude-opus-4-8` and `xhigh`.
Earlier tool-enabled attempts exhausted their bounded run budget before returning
a final verdict and were not treated as completed reviews.

## Blocking and high findings

| ID | Finding | Disposition |
|---|---|---|
| B1 | Claimed `job.workflow_*` contexts do not exist | Rejected. Current GitHub Actions documentation exposes `job.workflow_repository`, `job.workflow_ref`, and `job.workflow_sha` for reusable workflows. The design adds non-empty, expected-repository/path, and SHA-ref assertions so any future context change fails closed. |
| B2 | A backfill dispatched at an old tag may not contain the workflow | Superseded after plan review. Dispatch from `main` binds GitHub provenance to the wrong source ref/SHA, while dispatch at the old tag lacks the adopted caller/config. Pre-adoption tags are not backfilled; a new post-adoption version/tag is required. |
| H1 | Publishing the version alias before all acceptance gates creates a selectable partial release | Accepted. PUSH creates only the source-SHA alias. The version alias is created after immutable release publication and verification. |
| H2 | Executing leaf code in a job with registry/content write and OIDC permissions crosses the privilege boundary | Accepted. The workflow now has read-only preparation/build/gate, capture, and evidence-assembly jobs plus non-executing publisher/attestor and finalizer jobs. A verified OCI layout with a build-declared digest crosses the job boundary without a rebuild or Docker recompression. |
| H3 | Raw Trivy exit status cannot reliably distinguish findings from scanner failure | Accepted. Trivy emits JSON with exit zero; an independent versioned evaluator distinguishes valid policy findings from operational or parse failures. |
| H4 | Fixture-derived MCP definitions may differ from production-data definitions | Accepted. Repositories declare either a two-context-proven `data-independent` contract or a `data-bound` contract tied to the exact production data identity. |
| H5 | The data model omitted services materializing authoritative data from live upstreams | Accepted. A transitional `upstream-live` mode records egress and the lack of reproducible data rollback, with migration to immutable external references. |
| H6 | Attestation verification pinned workflow path but not exact reusable-workflow revision | Accepted. Verification requires `--signer-digest` plus signer repository/workflow and source identity checks. |

## Medium and low findings

The review also resulted in these accepted changes:

- bootstrap each initially private GHCR package, set and verify public visibility,
  remove the disposable tag, and record the control state;
- make draft retries digest-aware while prohibiting overwrite of published
  release assets;
- disable implicit Buildx provenance/SBOM and `push: true`, then attach explicit
  GitHub attestations to the known registry digest;
- verify immutable release assets and retain their digests without assuming an
  undocumented settings API;
- retain an attestation bundle and trusted root for offline verification;
- use `pull_policy: missing` only after an explicit verified deployment pull;
- protect tag creation, update, deletion, and force movement;
- bound reconciliation concurrency and disk use;
- handle the router, enabled backends, and disabled backends explicitly;
- version cache namespaces by toolchain and pin static analysis tool versions;
- turn the definition of done into generated, machine-checkable evidence.

## Closure

All accepted findings are reflected in the reviewed design. The rejected B1
finding is closed by authoritative platform documentation plus fail-closed
workflow assertions. The later plan review further hardened OCI digest transfer,
data modes, and backfill semantics; its separate disposition record is normative
where this initial review was superseded. No blocking finding remains open at
design handoff.
