# Community backend admission

Federation Standard v1 §8. A community backend is admitted by a pull request that changes only
`registry/community.yaml`, `registry/pins/community/<namespace>.json` and, for a delisting,
`registry/tombstones.yaml`. The `Community admission` workflow
(`.github/workflows/community-admission.yml`) checks it in two jobs.

## 1. Static checks (every PR, no network)

- The loader rules of §3: schema, required and forbidden keys, `https://<host>/mcp`, a
  namespace free of core entries, tombstones and duplicates.
- Every served entry has its pin file, which holds only its own namespace; no pin file lacks
  an entry.
- Each tool description is at most 300 characters (the core description budget).
- Every string in the entry and the pin is NFC and free of bidi, zero-width, tag and other
  invisible formatting characters and of C0/C1 controls (the Response Envelope v1.2 removal
  set).
- The PR changes no file outside the registry.

## 2. Probe (maintainer approval, then network)

Runs only for namespaces the PR adds or changes, in the `community-admission` environment,
with a read-only token and no secrets. In order:

1. The host is resolved; any non-global address fails the run before any request.
2. Ownership proof, one of:
   - DNS TXT record at `_genefoundry.<host>`:
     `namespace=<namespace>; github_login=<PR author>; probe_consent=true`
   - `https://<host>/.well-known/genefoundry.json`:
     `{"namespace": "<namespace>", "github_login": "<PR author>", "probe_consent": true}`

   The login must be the PR author's (case-insensitive). Without a matching proof no tool is
   called. (Proof through a verified MCP Registry domain namespace is reviewed by hand.)
3. The GTS conformance kit (`gts-conformance` v0.2.3) at the core level, `--name` set to the
   entry's `server_name`. The kit calls every tool, hence the consent.
4. The live `tools/list`, normalised as by `genefoundry-router pin`, must equal the PR's pin
   file; a difference fails with a unified diff.

Every connection uses the connect-time IP guard of §6: the router's community transport for
the proof and the live definitions, and a guarded resolver (port 443, global addresses only)
for the conformance kit.

## Repository setup (owner, once)

Settings → Environments → **New environment** `community-admission`:

- **Required reviewers**: the maintainers who may approve a probe; enable **Prevent
  self-review**.
- **Deployment branches and tags**: no restriction (PR merge refs must be able to use it).
- **Environment secrets** and **environment variables**: none.

Settings → Actions → General: **Require approval for all external contributors** for fork
PRs, and workflow permissions **Read repository contents**.

Before approving, the reviewer confirms that the PR changes only registry files and not the
workflow or `scripts/admission_check.py`: under `pull_request` the PR's own version of both
runs.
