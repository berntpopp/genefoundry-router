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
   is byte-reproducible and not silently re-pointed. Tag-only pinning (`python:3.12-slim`) is the
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
9. **Bundled reference data is read-only.** SQLite/OBO/parquet artifacts baked at build time are
   served read-only; their dataset version is recorded (ties to the Response-Envelope
   `data_version`/`snapshot_version`). No runtime mutation of reference data.

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

### Canonical Trivy gate block (ratified 2026-06-30, copy-paste fleet reference)

Every repo's `.github/workflows/container-security.yml` MUST use the **2-step "scan-is-the-gate"
form** below (Category B). Rationale: `severity: CRITICAL,HIGH` + `ignore-unfixed: true` limits
the gate to *actionable* CVEs only (no unfixable base-image noise); `exit-code: "1"` enforces the
policy from L129/L168/L208; `if: always()` on SBOM + upload ensures evidence is retained even when
the gate fires; one image scan per run (no redundant report step).

```yaml
      - name: Trivy scan (fail on fixable CRITICAL/HIGH)
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: <repo>:scan          # e.g. gnomad-link:scan
          format: table
          severity: CRITICAL,HIGH
          ignore-unfixed: true
          exit-code: "1"

      - name: Generate SBOM (CycloneDX)
        if: always()                       # SBOM is non-gating; produce it even when the scan fails
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25 # v0.36.0
        with:
          image-ref: <repo>:scan
          format: cyclonedx
          output: <repo>-sbom.cdx.json
          exit-code: "0"

      - name: Upload scan artifacts
        if: always()                       # keep evidence on a failing gate
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: container-security-artifacts
          path: <repo>-sbom.cdx.json
```

**Key implementation notes:**

- **`exit-code` default is `0`.** The `trivy-action` does **not** fail by default —
  omitting `exit-code: "1"` makes the step always green, which is the root cause of 12
  Category A repos not gating despite having a scan step.
- **`ignore-unfixed: true` is load-bearing.** Without it, flipping `exit-code` to `"1"` on a
  Category A scan (no `severity` / `ignore-unfixed`) gates on *all* severities including
  unfixable base-image CVEs (`libc`/`zlib` with no upstream fix), sending every backend
  red on CVEs nobody can action — a self-inflicted outage. Always pair the three fields
  (`severity: CRITICAL,HIGH`, `ignore-unfixed: true`, `exit-code: "1"`) as a unit.
- **SARIF + `exit-code` gotcha:** `trivy-action` issue #309 documents that `exit-code` does
  not respect `severity` when `format: sarif` is used — the step always exits `0` regardless.
  If a SARIF upload to GitHub code-scanning is desired (e.g. `genereviews-link`), keep it as
  a *separate non-gating step* and use a plain `format: table` step for the actual gate.
- **Schedule:** the workflow runs on a weekly schedule too. A fresh Trivy DB can turn a
  previously-green `main` red when a new fixable CVE is disclosed; that is the intended
  forcing function (L129: "re-scan on a schedule"). Runbook for a red scheduled run: bump the
  base image digest and/or lockfile, then re-run. If scheduled red runs become noisy, mirror
  the router's `drift.yml` heartbeat pattern (open/refresh a tracking issue instead of only a
  red ✗) — not required for initial conformance.

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

1. **Image signing / provenance** — adopt `cosign`/Sigstore signatures + build provenance
   attestations so the host (and an auditor) can verify image authenticity, not just integrity.
2. **Distroless or `chainguard`-style base** — drop `curl`/shell from the runtime entirely
   (healthcheck via a static binary or the orchestrator's HTTP probe). Smaller CVE surface,
   at some debuggability cost.
3. **Rootless runtime** — Podman/rootless Docker on the host as defense-in-depth beyond
   in-container non-root.
4. **Runtime security monitoring** — Falco/eBPF anomaly detection on the fleet host (egress,
   unexpected exec). Pairs with the router's tool-definition drift detection.
