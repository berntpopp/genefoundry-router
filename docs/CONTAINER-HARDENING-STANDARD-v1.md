# GeneFoundry Container & Deployment Hardening Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet and the `genefoundry-router`.
> Adopted 2026-06-29. Sibling to `TOOL-NAMING-STANDARD-v1.md`, the
> `RESPONSE-ENVELOPE-STANDARD-v1.md`, and the Logging & CLI Standard. A tracking issue
> titled **"Adopt GeneFoundry Container & Deployment Hardening Standard v1"** should exist
> in each `-link` repo to record bringing _that_ server into compliance.
>
> **Reference implementation:** the controls below are not aspirational — they are the
> already-shipped `genefoundry-router` deployment generalized for the fleet. Copy
> [`docker/Dockerfile`](../docker/Dockerfile),
> [`docker/docker-compose.prod.yml`](../docker/docker-compose.prod.yml), and
> [`docker/docker-compose.npm.yml`](../docker/docker-compose.npm.yml) as the starting point.

Part of the **GeneFoundry MCP router** initiative (`genefoundry-router`): the fleet is ~21
public-facing FastMCP containers plus the router, all reachable from the internet behind one
reverse proxy on one host. That makes the container the **primary trust and blast-radius
boundary**: a single weak image is a foothold on the host that runs every other backend. This
standard exists because connecting an LLM host to externally-reachable MCP servers draws
legitimate scrutiny from institutional security/IT — *"what could a compromised or malicious
server do?"* — and the honest answer must be **"almost nothing, by construction."** Container
hardening is how we make that true and *show* it.

It is also a **medical-grade prerequisite**: GDPR Art. 32 (security of processing) and Art. 25
(data protection by design) expect documented, state-of-the-art technical measures.
A consistent, audited hardening baseline across the fleet is exactly such a measure.

## Threat model (what these rules defend against)

| Threat | Hardening control(s) |
|---|---|
| Container breakout → **host compromise** (one bad backend owns the VPS) | non-root, `cap_drop: ALL`, `no-new-privileges`, read-only rootfs, seccomp, no `docker.sock` |
| **Resource exhaustion / DoS** (one backend starves siblings) | `mem`/`cpus`/`pids` limits, `ulimits`, log rotation |
| **Accidental public exposure** of an unauthenticated endpoint | expose-only behind the reverse proxy; never publish a host port in prod; TLS at the edge |
| **Secret leakage** (URLs, tokens, keys) | runtime `env_file` only; never bake secrets into image layers; `.dockerignore`; no secrets in logs |
| **Supply-chain / CVE drift** (vulnerable base or deps) | pinned base by digest, frozen lockfile, image scanning in CI, rebuild cadence, optional SBOM |
| **Persistence/tamper** of a running container | read-only rootfs + explicit, minimal writable mounts |

## Rules

### 1. Image build — minimal, reproducible, secret-free

1. **Multi-stage build; ship a minimal runtime.** Build deps (`build-essential`, compilers) live
   only in the builder stage. The production stage installs **no** toolchain — at most `curl`
   for the healthcheck. (Router: `docker/Dockerfile`, `builder` → `production`.)
2. **Pin the base image by digest, not just tag.** `FROM python:3.12-slim@sha256:…` so a rebuild
   remains traceable to its selected base and is not silently re-pointed. Digest pins and a frozen
   lockfile do not make Debian repositories, wheels, timestamps, or compression byte-reproducible.
   Tag-only pinning (`python:3.12-slim`) is the
   v1 floor; **digest pinning is required for a medical-grade deployment** and is tracked by
   Renovate/Dependabot for patch bumps.
3. **Install dependencies from a frozen lockfile.** `uv sync --frozen --no-dev --no-editable`
   (no resolver drift at build time, no dev deps in prod, self-contained `.venv`).
4. **No secrets in the image.** Never `COPY .env`, never `ARG`/`ENV` a token. Maintain a
   `.dockerignore` that excludes `.env`, `.env.*` (keep `.env.example`), `.git`, tests, docs, and
   caches. Verify no secret is in any layer (`docker history`, scanners).
5. `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1`. Pin a single app version; emit it on
   startup so a running container is identifiable.

### 2. Runtime identity & filesystem — non-root, read-only

6. **Run as a non-root, fixed high UID** (`useradd --uid 10001 app`; `USER app`). Never run as
   root; never rely on the base image's default user.
7. **Read-only root filesystem** (`read_only: true`). A reference data server has no reason to
   write to `/`.
8. **Declare every writable path explicitly and minimally:**
   - ephemeral scratch → `tmpfs` mounted `rw,noexec,nosuid` with a size cap
     (router: `/tmp` `rw,noexec,nosuid,size=64m,mode=1777`);
   - persistent state (e.g. a backend's built SQLite/index that must survive restart) → a
     **named volume mounted read-write only at that path**, everything else read-only.
9. **Application images are code-only.** Authoritative SQLite/OBO/parquet/corpus content is an
   independently released, digest-verified artifact mounted read-only after hardened
   materialization. Only exact, bounded code/schema/baseline resources may be allowlisted in an
   application image. Runtime state and mutable caches use separate explicit writable mounts.

### 3. Kernel & privilege surface — drop everything

10. **`cap_drop: ALL`.** Add back a capability only with a written justification (a read-only MCP
    server needs none).
11. **`security_opt: ["no-new-privileges:true"]`** — block setuid privilege escalation.
12. **`init: true`** — a real PID 1 to reap zombies and forward signals (clean shutdown).
13. **Never** `privileged: true`, never mount the Docker socket (`/var/run/docker.sock`), never
    `network_mode: host`, never add `SYS_ADMIN`/`SYS_PTRACE`. Keep the default seccomp profile
    (do not set `seccomp:unconfined`).

### 4. Resource limits — bound the blast radius

14. **Cap memory, CPU, and PIDs** so one container cannot starve the host or fork-bomb it
    (router prod: `memory: 1G`, `cpus: "1.0"`, `pids: 256`). Size per backend; document the value.
15. **Compose caveat — make limits actually apply.** `deploy.resources.limits` is honored by
    Compose v2 (`docker compose up`) but ignored by legacy `docker-compose` (Swarm-only key).
    Deploy with Compose v2, **or** additionally set the non-swarm keys (`mem_limit`, `cpus`,
    `pids_limit`). Verify limits are live (`docker stats`, `docker inspect`).
16. Set sane `ulimits` (e.g. `nofile`) where the runtime needs them; otherwise inherit the
    hardened host default.

### 5. Network & exposure — expose-only, TLS at the edge

17. **Behind the reverse proxy, the container is expose-only.** `expose: ["8000"]` +
    `ports: !reset []`, attached to the proxy network. **The `!reset []` is mandatory:** Compose
    *merges* list fields across `-f` overlays, so a plain `ports: []` does **not** drop a base
    file's `"<host>:8000"` mapping — it stays published on the public IP, exposing the endpoint
    directly. (Router: `docker/docker-compose.npm.yml`.)
18. **TLS terminates at the reverse proxy; only HTTPS is reachable externally.** Bind `0.0.0.0`
    *inside* the container only; never publish that to the host in production.
19. **Per-stack Compose project name** (`name: <repo>`) so `--remove-orphans` from one repo can
    never delete a sibling stack's containers/volumes.
20. Put each backend on the shared proxy network only; no backend needs to reach another backend
    directly (the router is the only client). Prefer least-connectivity networking.

### 6. Secrets — runtime only

21. Inject URLs/tokens via `env_file` at **runtime** (`required: false` so the container still
    starts for local dev). Secrets never enter an image layer, a committed file, or a log line.
22. If a backend ever needs an upstream credential, give it its **own** service credential via
    env/secret store — **never** the MCP caller's token (the no-token-passthrough rule from the
    router design also binds the leaves).

### 7. Observability & ops

23. **`HEALTHCHECK`** hitting `/health` (router: 30s interval, 10s timeout, 3 retries,
    10s start-period) so the orchestrator can restart a wedged container.
24. **Bound log growth**: `json-file` driver with `max-size` + `max-file` rotation
    (router: 50m × 5). Unbounded container logs are a disk-exhaustion DoS.
25. **Logs carry no PII / no query payloads.** Log the correlation/request id, tool name, and
    timings — never the variant coordinates, phenotype text, or free-text query that may be
    patient-derived (Logging Standard §3; GDPR data-minimisation).
26. Set an explicit `restart:` policy (`on-failure` or `unless-stopped`).

### 8. Supply chain & CVE management

27. **Scan every image in CI** (Trivy or Grype); **fail the build on HIGH/CRITICAL** fixable
    vulnerabilities. Re-scan on a schedule, not just at release.
28. **Rebuild cadence.** Rebuild + redeploy on base-image security updates (Renovate/Dependabot
    watching the base digest and `pyproject`/`uv.lock`). A pinned-but-never-patched base is a
    liability, not a control.
29. **Generate an SBOM** (`syft`, or `docker buildx --sbom`) for the production image and retain
    it — required-grade supply-chain evidence for an institutional security review.
30. Keep `uv.lock` committed and `--frozen` in the build (already a fleet rule); enable the ruff
    `S` (security) lint set in CI.

## Reference implementation

| Concern | File in `genefoundry-router` |
|---|---|
| Multi-stage minimal image, non-root, healthcheck, frozen install | `docker/Dockerfile` |
| read-only rootfs, `cap_drop`, `no-new-privileges`, `init`, resource limits, log rotation | `docker/docker-compose.prod.yml` |
| expose-only behind reverse proxy, `ports: !reset []` | `docker/docker-compose.npm.yml` |
| secret exclusion from image | `.dockerignore` |

Each `-link` repo already mirrors the `-link` Docker layout; this standard says **adopt the
`prod` + `npm` overlays' hardening verbatim**, then close the per-repo gaps the conformance
audit surfaces (digest pinning, image scanning, SBOM — the three the router itself still owes).

## Fleet conformance snapshot (2026-06-29 audit baseline)

A security audit of all 21 registered backends (2026-06-29) found **no malicious code** anywhere
and **good in-overlay hardening on most repos**, but three **universal gaps** and an uneven base
posture. Tiers below are the adoption baseline; each repo's tracking issue closes against the DoD.

| Tier | Backends | State | Gaps to close |
|---|---|---|---|
| **A — overlay fully hardened** | gnomad, clinvar, vep, spliceai, mavedb, metadome, uniprot, gtex, clingen | `read_only`+tmpfs, `cap_drop: ALL`, `no-new-privileges`, limits, healthcheck ✓ in prod/npm overlay | universal gaps (below); add `HEALTHCHECK` to the Dockerfile where only compose has it (gnomad, spliceai, clingen) |
| **B — npm overlay only; base/prod weak** | hpo, mondo, orphanet, mgi, gencc, panelapp | hardened in `docker-compose.npm.yml`; `prod`/base missing `read_only` (gencc/panelapp — need a writable-volume-only path) or all keys (hpo/mondo/orphanet/mgi base) | backport overlay hardening to the base/prod compose so running it directly is still safe |
| **C — incomplete** | hgnc (no `docker-compose.prod.yml`; only npm hardened), autopvs1 (dev compose runs `user: root`) | partial | add a hardened prod overlay; never run any stage as root |
| **D — substandard** | stringdb | prod **and** npm overlays lack `read_only`/`cap_drop`/`no-new-privileges`/tmpfs/pids/init (older template) | port the gtex/router hardening wholesale; also fix `allow_origins=*`+credentials and set `mask_error_details=True` |

**Universal gaps (apply to all 21 + the router):**
1. **Base `docker-compose.yml` is unhardened and publishes a host port** binding `0.0.0.0` — running it directly drops every control and exposes the backend outside the router. Make non-publishing + hardened the default; keep direct exposure out of prod.
2. **Base images are tag-pinned, not digest-pinned** (e.g. `python:3.12-slim`/`3.14-slim`). Pin `@sha256:`.
3. **No CI image scanning and no SBOM** on any repo. Add Trivy/Grype (fail on fixable HIGH/CRITICAL) + an SBOM artifact.

> The **`genefoundry-router` itself is Tier A** (its `docker/docker-compose.prod.yml` is the
> reference) but, like the fleet, still owes the three universal items — digest pinning, image
> scanning, and SBOM. Fixing them on the router first sets the pattern the fleet copies.

### Canonical Trivy evidence gate (ratified 2026-07-13, fleet reference)

Every repository MUST separate **scanner operation** from **policy evaluation**. The authoritative
scan is Trivy JSON with `exit-code: "0"`, `severity: CRITICAL,HIGH`, and `ignore-unfixed: true`.
The scanner process status is captured independently. A successful process does not itself mean
the image passed policy, and a failed process is not misreported as a vulnerability finding.

The release tooling consumes one `trivy.json` evidence envelope:

```json
{
  "schema_version": 1,
  "scan": {"...": "native JSON from trivy image --format json"},
  "version": {"...": "native JSON from trivy version --format json"}
}
```

Generate the two native documents with the same pinned Trivy binary, assemble the envelope with a
strict JSON tool, and then run the repository-owned evaluator:

```text
evaluate-trivy --report trivy.json --scanner-exit scanner.exit --out verdict.json
```

The command's shared exit contract is stable across every release subcommand:

| Exit | JSON `verdict` | Meaning |
|---:|---|---|
| `0` | `pass` | complete, fresh evidence and no fixable HIGH/CRITICAL finding |
| `1` | `policy_violation` | complete evidence contains a fixable HIGH/CRITICAL finding |
| `2` | `invalid_evidence` | malformed, incomplete, ambiguous, or stale evidence |
| `3` | `infrastructure_failure` | Trivy itself exited non-zero; retry or repair infrastructure |

The workflow MUST branch on the evaluator's JSON `verdict` and matching exit code. It MUST NOT
infer policy from the raw Trivy process exit, a table, SARIF, or presence/absence of a report file.
The verdict records the pinned Trivy version, database `UpdatedAt`, `NextUpdate`, and
`DownloadedAt`, scan creation time, and bounded identifying fields for findings. Unfixable
HIGH/CRITICAL findings, if present in the native JSON, remain evidence but do not gate.

**Key implementation notes:**

- **Capture the process result before evaluation.** Configure Trivy's policy exit to zero, retain
  its real process status in `scanner.exit`, and run evidence assembly/evaluation under
  `if: always()`. Any non-zero scanner result maps to infrastructure failure even when a partial
  JSON file exists.
- **Freshness is evidence, not an assumption.** `trivy version --format json` MUST contain the
  vulnerability database version and aware `UpdatedAt`, `NextUpdate`, and `DownloadedAt`
  timestamps. The evaluator rejects missing, misordered, future, or already-expired metadata.
  Evaluation allows at most five minutes of clock skew and rejects scan reports older than one
  hour plus that skew; reruns therefore rescan the immutable image digest instead of replaying old
  vulnerability evidence.
- **Fixability is evaluated from validated JSON.** Only HIGH/CRITICAL entries with a nonempty
  `FixedVersion` return `policy_violation`. Unfixable entries are retained without making routine
  releases permanently red on base-image CVEs with no upstream remediation.
- **SARIF is non-gating.** If GitHub code-scanning output is desired, generate and upload SARIF in
  a separate best-effort reporting job. A SARIF document is never accepted by the evaluator and
  cannot substitute for authoritative JSON evidence.
- **Retain evidence on all outcomes.** Upload `trivy.json`, `scanner.exit`, and `verdict.json` with
  `if: always()` together with the SBOM. A release proceeds only on a validated `pass` verdict.
- **Schedule:** re-scan deployed digests weekly. Newly disclosed fixable CVEs should open or
  refresh a deduplicated remediation issue. Scanner/database/registry infrastructure errors are
  tracked separately and never described as application vulnerability findings.

### Application image publication and deployment (ratified 2026-07-13)

- A protected exact stable `vX.Y.Z` tag is the only publication authority. Pull requests and
  branch workflows are read-only and cannot publish.
- Release jobs build one `linux/amd64` OCI manifest, inspect every layer and image configuration,
  run the hardened container plus MCP conformance, scan it, and preserve SBOM/provenance evidence.
  AMD64 is the sole v1 platform. ARM64 requires native platform-specific build, content, scan,
  runtime, MCP, and data tests before a multi-platform index is permitted.
- Images carry complete OCI title, description, source, URL, documentation, version, full
  revision, creation time, license, and vendor labels plus
  `org.genefoundry.research-use-only=true` and `org.genefoundry.data-policy=code-only`.
- Production accepts a reviewed digest only, clears every inherited `build:`, and publishes no
  backend host port. The deployment verifier checks the immutable release, exact signer workflow
  and revision, provenance and SPDX SBOM attestations, labels, data identity, and Compose model.
- New GHCR packages require a one-time bootstrap before a real release: link the package to its
  source repository, set public package visibility, verify an anonymous pull, delete only the
  disposable bootstrap tag, and retain no standing package PAT. The controls ledger also proves
  protected tags, immutable releases, protected release environment, linkage, public visibility,
  anonymous access, and rollback-digest retention.
- The rollback tuple is image digest + application version/revision + data release/digest/schema
  (or explicit `none`) + MCP-definition digest. Preserve and test the previous-known-good tuple;
  an older image tag alone is not a rollback.
- A digest collision, partial publication, attestation/SBOM failure, anonymous-pull regression,
  mutable GitHub Release, or definition mismatch is an incident. Do not overwrite aliases or
  assets. Retain evidence and resume only the original protected-tag event after remediation.
- **Research use only. Not clinical decision support.** Origin and integrity evidence does not
  establish clinical validity, regulatory approval, or correctness of biomedical data.

## References

- **CIS Docker Benchmark** — Docker host & daemon/container hardening (user namespaces, caps,
  read-only, healthcheck, no privileged). <https://www.cisecurity.org/benchmark/docker>
- **NIST SP 800-190**, *Application Container Security Guide* — image/registry/runtime risks and
  countermeasures. <https://csrc.nist.gov/pubs/sp/800/190/final>
- **OWASP Docker Security Cheat Sheet** — non-root, `--cap-drop`, `no-new-privileges`, read-only,
  resource limits, secret handling.
  <https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html>
- **Docker docs** — *Build best practices*, multi-stage, `.dockerignore`, `HEALTHCHECK`.
  <https://docs.docker.com/build/building/best-practices/>
- **Docker Compose spec** — `deploy.resources` vs non-swarm limits; list-merge across overlays.
  <https://docs.docker.com/reference/compose-file/>
- **MCP transport security (2025-11-25)** — bind localhost / validate `Origin`; the network
  exposure rules this standard enforces at the container layer.
  <https://modelcontextprotocol.io/specification/2025-11-25/basic/transports>
- Sibling fleet standards: `TOOL-NAMING-STANDARD-v1.md`, `RESPONSE-ENVELOPE-STANDARD-v1.md`,
  Logging & CLI Standard v1.

## Definition of Done (per repo)

- [ ] Multi-stage build; production stage has **no** build toolchain; runs as a **non-root fixed
      UID**; `HEALTHCHECK` on `/health`.
- [ ] Base image **pinned by digest**; deps installed `--frozen` from `uv.lock`.
- [ ] `.dockerignore` excludes `.env`/`.env.*`/`.git`/tests/docs/caches; **no secret in any image
      layer** (verified).
- [ ] `prod` overlay sets `read_only: true` + explicit `tmpfs`/volume writable mounts,
      `cap_drop: ALL`, `no-new-privileges:true`, `init: true`.
- [ ] `mem`/`cpus`/`pids` limits set **and verified live** under the deploy tooling (Compose v2 or
      non-swarm keys).
- [ ] `npm` overlay is **expose-only** (`ports: !reset []` + `expose`), on the proxy network;
      TLS terminated at the proxy; no host port published in prod; per-stack Compose `name:`.
- [ ] Secrets injected at runtime via `env_file`; logs carry **no PII / no query payloads**; log
      rotation (`max-size`/`max-file`) and a `restart:` policy set.
- [ ] CI **image scan** (Trivy/Grype) fails on fixable HIGH/CRITICAL; **SBOM** generated and
      retained; base/deps watched for patch bumps.
- [ ] This standard's tracking issue closed with a one-line `CHANGELOG` note.

## Open: Standard v1.1 (pending decision)

1. **Distroless or `chainguard`-style base** — drop `curl`/shell from the runtime entirely
   (healthcheck via a static binary or the orchestrator's HTTP probe). Smaller CVE surface,
   at some debuggability cost.
2. **Rootless runtime** — Podman/rootless Docker on the host as defense-in-depth beyond
   in-container non-root.
3. **Runtime security monitoring** — Falco/eBPF anomaly detection on the fleet host (egress,
   unexpected exec). Pairs with the router's tool-definition drift detection.
