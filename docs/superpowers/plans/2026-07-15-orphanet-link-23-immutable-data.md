# Orphanet #23 — Immutable Reference Data Init-Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Orphanet’s runtime `latest` release selection, checksum-sidecar trust, and local XML-build fallback; serve only an exact data release verified and atomically selected by a hardened init sidecar with SQLite opened `mode=ro&immutable=1`.

**Architecture:** The application image remains code-only. A typed immutable requirement fixes the published tag, gzip asset, committed compressed digest, expanded-tree digest, expected metadata identity, and ceilings. `orphanet-data-init` downloads that one HTTPS asset, hashes the bytes against the committed digest before reading release-provided metadata, expands and probes SQLite under a lock, fsyncs immutable files under `reference/a8af3fc39cca2acedd12c188cb0e1f907ac320e73d2b965c17ad5a28c5f5fe38/`, and atomically selects `current`. The app receives only a read-only mount and has no bootstrap, refresh, GitHub-release discovery, Orphadata XML download, or source-build code path.

**Tech Stack:** Python 3.12, Pydantic settings, SQLite URI immutable mode, gzip, httpx, Docker Compose v2, GitHub Actions/Releases, pytest/respx.

---

**Repository and branch:** `/home/bernt-popp/development/orphanet-link`, new branch `fix/immutable-data-init-23` from current `origin/main`. All paths below are relative to that repository.

## Reviewed production identity

| Field | Exact value |
|---|---|
| GitHub release tag | `data-1.3.42-4.1.8-2025-03-03` |
| Asset | `orphanet.sqlite.gz` |
| Download URL | `https://github.com/berntpopp/orphanet-link/releases/download/data-1.3.42-4.1.8-2025-03-03/orphanet.sqlite.gz` |
| Compressed SHA-256 trust root | `a8af3fc39cca2acedd12c188cb0e1f907ac320e73d2b965c17ad5a28c5f5fe38` |
| Expanded SQLite SHA-256 | `f691bbbfc053f317b8f425cb99f9f1142b1553f218a9ade2077aa0724a28a7ba` |
| Expanded SQLite bytes | `47677440` |
| Canonical expanded-tree SHA-256 | `94da7d8a26961de893f7ded253f8c4a468602bc79c30636bb58342b0614a2a43` |
| Expected `meta` row | schema `1`; version `1.3.42 / 4.1.8 [2025-03-03]`; date `2025-12-09 07:06:32` |
| Compressed/expanded ceilings | `268435456` / `2147483648` bytes |

The canonical expanded-tree digest is SHA-256 of `orphanet.sqlite\0` + `0444\0` + decimal expanded size + `\0` + the expanded SQLite SHA-256. `orphanet.sqlite.gz.sha256` remains a published convenience asset but must not be fetched or trusted by runtime code: a modified release sidecar can only be diagnostic evidence, never authorization for different artifact bytes.

## File map

| File | Change | Responsibility |
|---|---|---|
| `orphanet_link/config.py` | Modify | Replace mutable release/bootstrap settings with a complete immutable deployment requirement. |
| `orphanet_link/immutable_data.py` | Create | Bounded fixed-asset download, verify, decompress, probe, lock/fsync, versioned materialization, atomic selection. |
| `orphanet_link/ingest/cli.py` | Modify | Add the `materialize-data` sidecar command and remove runtime-facing `fetch` guidance. |
| `orphanet_link/data/repository.py` | Modify | Add `immutable=1` to all application SQLite opens. |
| `orphanet_link/app.py`, `orphanet_link/server_manager.py`, `docker/entrypoint.sh` | Modify | Eliminate lifecycle/stdio bootstrap and source/data network work. |
| `orphanet_link/services/data_resolver.py`, `orphanet_link/services/refresh.py` | Delete | Eliminate latest API, checksum-sidecar, prebuilt and source-build fallbacks from serving code. |
| `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`, `docker/docker-compose.npm.yml` | Modify | Express same-image hardened init writer/read-only application reader. |
| `container-release.json`, `.env.docker.example`, `docs/configuration.md`, `docs/data.md`, `docs/deployment.md`, `README.md` | Modify | Record immutable release facts and remove operational bootstrap claims. |
| `.github/workflows/build-data.yml` | Modify | Publish raw and canonical expanded identities alongside the gzip artifact without treating the checksum sidecar as a runtime authority. |
| `tests/unit/test_immutable_data.py`, `tests/unit/test_compose_hardening.py` | Create | Regression tests for no latest/fallback/sidecar trust, atomic materialization, and rendered sidecar separation. |
| `tests/unit/test_data_resolver.py`, obsolete refresh/start tests | Delete/Modify | Remove tests for behavior that #23 intentionally forbids. |

### Task 1: Define the no-latest/no-sidecar trust contract in failing tests

**Files:**
- Create: `tests/unit/test_immutable_data.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Create a test artifact factory.**

  Build a small SQLite database containing the real `meta` columns, insert schema/version/date values, force `journal_mode=DELETE`, gzip the bytes, and compute both compressed and canonical expanded-tree digests. The tree function must be:

  ```python
  def canonical_tree(path: Path) -> str:
      digest = hashlib.sha256(path.read_bytes()).hexdigest()
      record = f"orphanet.sqlite\0{0o444:o}\0{path.stat().st_size}\0{digest}"
      return hashlib.sha256(record.encode("utf-8")).hexdigest()
  ```

- [ ] **Step 2: Add complete failing behavior coverage.**

  ```python
  @respx.mock
  def test_init_materializes_only_the_committed_gzip_digest(tmp_path: Path) -> None:
      requirement, gzip_bytes = _requirement_and_gzip(tmp_path)
      respx.get(str(requirement.bundle_url)).mock(return_value=httpx.Response(200, content=gzip_bytes))
      selected = materialize_immutable_data(requirement)
      assert selected == tmp_path / "reference" / requirement.compressed_sha256 / "orphanet.sqlite"
      assert (tmp_path / "current").resolve() == selected.parent
      assert selected.stat().st_mode & 0o777 == 0o444

  @pytest.mark.parametrize("tag", ["latest", "main", "master", "head", "stable", "current", ""])
  def test_requirement_rejects_mutable_or_empty_release_tag(tag: str) -> None:
      values = _requirement_values()
      values["release_tag"] = tag
      with pytest.raises(ValidationError):
          ImmutableDataRequirement(**values)

  @respx.mock
  def test_runtime_never_fetches_the_artifact_checksum_sidecar(tmp_path: Path) -> None:
      requirement, gzip_bytes = _requirement_and_gzip(tmp_path)
      artifact = respx.get(str(requirement.bundle_url)).mock(
          return_value=httpx.Response(200, content=gzip_bytes)
      )
      sidecar = respx.get(str(requirement.bundle_url) + ".sha256").mock(
          return_value=httpx.Response(200, content=b"0" * 64)
      )
      materialize_immutable_data(requirement)
      assert artifact.called
      assert not sidecar.called

  @respx.mock
  def test_tree_mismatch_preserves_last_known_current(tmp_path: Path) -> None:
      old_requirement, old_gzip = _requirement_and_gzip(tmp_path, version="1.3.41")
      respx.get(str(old_requirement.bundle_url)).mock(return_value=httpx.Response(200, content=old_gzip))
      old_selected = materialize_immutable_data(old_requirement)
      requirement, gzip_bytes = _requirement_and_gzip(tmp_path, version="1.3.42 / 4.1.8 [2025-03-03]")
      invalid = requirement.model_copy(update={"expanded_tree_sha256": "0" * 64})
      respx.get(str(invalid.bundle_url)).mock(return_value=httpx.Response(200, content=gzip_bytes))
      with pytest.raises(DataUnavailableError, match="expanded-tree"):
          materialize_immutable_data(invalid)
      assert (tmp_path / "current").resolve() == old_selected.parent
      assert not list((tmp_path / "reference").glob(".*.staging-*"))
  ```

  Add a source-surface test reading `orphanet_link/app.py` and `orphanet_link/server_manager.py`; it must assert none contains `ensure_database`, `fetch_prebuilt`, `local_build`, `download_files`, `materialize_immutable_data`, or `bootstrap_data`. Add a repository URI test that asserts `mode=ro&immutable=1`.

- [ ] **Step 3: Run the focused suite.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py -q
  ```

  Expected before implementation: import failure for `orphanet_link.immutable_data`; existing source surface still exposes bootstrap/fallback imports; repository lacks `immutable=1`.

- [ ] **Step 4: Commit the red tests.**

  ```bash
  git add tests/unit/test_immutable_data.py tests/unit/test_config.py
  git commit -m "test: specify immutable Orphanet data contract"
  ```

### Task 2: Implement the typed, atomic, committed-digest materializer

**Files:**
- Modify: `orphanet_link/config.py`
- Create: `orphanet_link/immutable_data.py`
- Modify: `orphanet_link/data/repository.py`

- [ ] **Step 1: Add `ImmutableDataRequirement` and production defaults.**

  Define a frozen model containing `reference_root`, `release_tag`, `bundle_url`, `compressed_sha256`, `expanded_tree_sha256`, `schema_version`, `orphanet_version`, `orphanet_date`, `max_compressed_bytes`, and `max_expanded_bytes`. Validate HTTPS, exactly 64 lower-normalized hex, positive limits, non-reserved release tag, and that the URL ends in `/{release_tag}/orphanet.sqlite.gz`. Add `immutable_data` to `ServerSettings` with the exact values in the production identity table. Remove `prefer_prebuilt`, `release_repo`, `release_tag`, and `auto_bootstrap` from configuration used by the server.

- [ ] **Step 2: Implement `materialize_immutable_data`.**

  `orphanet_link/immutable_data.py` must be under 500 lines and export `materialize_immutable_data(requirement) -> Path`. It must use only the one pinned `bundle_url` and allowed GitHub asset hosts. Its exact ordered operations are:

  1. `fcntl.flock` `reference_root/.materialize.lock`; create private `reference_root / f".a8af3fc39cca2acedd12c188cb0e1f907ac320e73d2b965c17ad5a28c5f5fe38.staging-{os.getpid()}"`.
  2. Stream artifact bytes to a temp file while enforcing `max_compressed_bytes` and computing SHA-256; compare it to `requirement.compressed_sha256` before gzip expansion.
  3. Decompress with `gzip.GzipFile` to `staging/orphanet.sqlite`, enforce `max_expanded_bytes`, require `b"SQLite format 3\x00"`, and calculate the canonical tree digest.
  4. Query the staging DB using `file:{staging_db_path}?mode=ro&immutable=1` and require one `meta` row exactly matching `(1, "1.3.42 / 4.1.8 [2025-03-03]", "2025-12-09 07:06:32")`.
  5. Write `identity.json` with compressed/tree digest plus schema/version/date; chmod SQLite and identity `0444`; fsync files and directories; atomically rename to `reference/a8af3fc39cca2acedd12c188cb0e1f907ac320e73d2b965c17ad5a28c5f5fe38` and atomically replace `current` with a symlink to it.
  6. Clean all temporary paths on any error and never change an existing `current` until every check and fsync succeeds.

  It must not call `_fetch_release`, `_find_asset`, `_download_sidecar`, `fetch_prebuilt`, `local_build`, or any GitHub `/releases/latest` endpoint.

- [ ] **Step 3: Update the application repository URI.**

  In `orphanet_link/data/repository.py`, change the connection string to:

  ```python
  f"file:{self._path}?mode=ro&immutable=1"
  ```

- [ ] **Step 4: Run tests and commit.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py tests/unit/test_repository.py -q
  git add orphanet_link/config.py orphanet_link/immutable_data.py orphanet_link/data/repository.py tests/unit
  git commit -m "feat: materialize pinned Orphanet reference data"
  ```

  Expected: only the committed gzip digest reaches expansion; the sidecar is never requested; all mismatch/partial cases retain prior `current`; selected SQLite is immutable.

### Task 3: Excise application bootstrap, dynamic release lookup, and source fallback

**Files:**
- Modify: `orphanet_link/ingest/cli.py`
- Delete: `orphanet_link/services/data_resolver.py`
- Delete: `orphanet_link/services/refresh.py`
- Modify: `orphanet_link/app.py`
- Modify: `orphanet_link/server_manager.py`
- Modify: `tests/unit/test_server_manager.py`
- Delete: `tests/unit/test_data_resolver.py`

- [ ] **Step 1: Add only the explicit init command.**

  Add this Typer command to `orphanet_link/ingest/cli.py`:

  ```python
  @app.command("materialize-data")
  def materialize_data() -> None:
      """Download, verify, and atomically select the configured immutable bundle."""
      from orphanet_link.config import ServerSettings
      from orphanet_link.immutable_data import materialize_immutable_data

      selected = materialize_immutable_data(ServerSettings().immutable_data)
      print(f"Materialized immutable Orphanet data: {selected.name}")
  ```

  Remove the `fetch` command. Keep `build`/`refresh` strictly as offline data-release authoring operations; their help text must say they are not runtime recovery mechanisms.

- [ ] **Step 2: Delete resolver/refresh paths and simplify lifecycles.**

  Delete `data_resolver.py` and `refresh.py`. In `app.py`, lifespan only configures/logs server startup and shutdown. In `server_manager.py`, remove stdio bootstrap imports/calls. Replace all deleted resolver tests with the source-surface tests from Task 1. Do not catch an immutable-data failure and attempt local XML build.

- [ ] **Step 3: Verify absence and behavior.**

  ```bash
  rg -n "releases/latest|release_tag.?=.latest|fetch_prebuilt|local_build|ensure_database|bootstrap_data" orphanet_link docker docs .env.docker.example
  uv run pytest tests/unit/test_immutable_data.py tests/unit/test_server_manager.py tests/unit/test_cli.py -q
  ```

  Expected: `rg` finds no serving/deployment paths. The remaining `build` and `refresh` CLI authoring commands are permitted only under `orphanet_link/ingest/cli.py`; no app startup imports or executes them.

- [ ] **Step 4: Commit.**

  ```bash
  git add orphanet_link tests/unit
  git rm orphanet_link/services/data_resolver.py orphanet_link/services/refresh.py tests/unit/test_data_resolver.py
  git commit -m "fix: remove Orphanet runtime bootstrap fallback"
  ```

### Task 4: Build the hardened init/app boundary in each Compose deployment

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `docker/docker-compose.npm.yml`
- Create: `tests/unit/test_compose_hardening.py`

- [ ] **Step 1: Permit the image to run a distinct one-shot command.**

  Change Dockerfile `ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]` to `CMD ["/usr/local/bin/entrypoint.sh"]`. Make entrypoint only `exec python server.py --transport "${ORPHANET_LINK_TRANSPORT:-unified}" --host "${ORPHANET_LINK_HOST:-0.0.0.0}" --port "${ORPHANET_LINK_PORT:-8000}"`; it cannot contain any database ensure/download/build fallback.

- [ ] **Step 2: Add base `orphanet-data-init`.**

  The service uses `command: ["orphanet-link-data", "materialize-data"]`, shares `orphanet-reference:/data` writable, and sets every immutable `ORPHANET_LINK_IMMUTABLE_DATA__*` field to the reviewed table. It has `read_only: true`, `/tmp:rw,noexec,nosuid,size=64m,mode=1777`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `init: true`, `restart: "no"`, resources `memory: 1g`, `cpus: "1.0"`, `pids: 256`, log rotation, and neither ports nor healthcheck/expose. It attaches only to the approved default network to obtain the exact GitHub asset.

  Make application `orphanet-link` wait on `service_completed_successfully`, mount `orphanet-reference:/data:ro`, set `ORPHANET_LINK_DATA__DB_FILENAME: current/orphanet.sqlite`, and remove all `AUTO_BOOTSTRAP`, `PREFER_PREBUILT`, `RELEASE_TAG`, refresh, or XML source URL environment values. Remove the writable old `orphanet-data` volume.

- [ ] **Step 3: Apply the same split to prod and standalone NPM.**

  Prod uses the exact required digest image for both services, app has `ports: !reset []` and `expose: ["8000"]`, and init remains non-serving. Self-contained NPM declares `orphanet_data_init` and `orphanet_link`; init is only on `orphanet_link_internal_net`, app waits for init and is on both internal/proxy networks, and no service maps a host port.

- [ ] **Step 4: Write rendered Compose assertions.**

  Assert base+prod and NPM each have exactly the init/application pair, explicit init argv, non-restarting init, hardened controls/limits, writable init and read-only app `/data` mounts, `service_completed_successfully`, no app data URL, and no NPM port. Assert full reference volume path is mounted and current selected SQLite is the only application data path.

- [ ] **Step 5: Run and commit.**

  ```bash
  docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config --format json >/tmp/orphanet-compose.json
  docker compose -f docker/docker-compose.npm.yml --env-file .env.docker.example config --format json >/tmp/orphanet-npm.json
  uv run pytest tests/unit/test_compose_hardening.py -q
  git add docker tests/unit/test_compose_hardening.py
  git commit -m "feat: initialize immutable Orphanet data before serving"
  ```

  Expected: rendered init containers are hardened non-serving writers; apps cannot write data or start until success.

### Task 5: Align data publication, release declaration, docs, and smoke

**Files:**
- Modify: `.github/workflows/build-data.yml`
- Modify: `container-release.json`
- Modify: `.env.docker.example`
- Modify: `docs/configuration.md`, `docs/data.md`, `docs/deployment.md`, `README.md`
- Modify: `tests/unit/test_docs_and_ci_contracts.py`

- [ ] **Step 1: Publish identities needed for reviewed promotion.**

  In the data workflow, compute and include `compressed_sha256`, raw SQLite SHA-256, expanded size, canonical `expanded_tree_sha256`, schema/version/date, and exact asset name in `manifest.json`. Continue publishing `.sha256` for humans, but add a workflow/unit assertion that no application runtime module references it.

- [ ] **Step 2: Update `container-release.json`.**

  Retain `data.mode: "external-reference"`, release tag, and compressed digest; add `orphanet-data-init` auxiliary with `role: "init"`, `egress: "approved-networks"`, writable `/data` and `/tmp`; set `smoke.profile: "immutable-bundle"`. `immutable-bundle` is the smoke profile, not a replacement for the external-reference data mode.

- [ ] **Step 3: Rewrite docs/environment values to no-fallback operations.**

  Remove instructions/comments for latest, prebuilt preference, runtime `fetch`, sidecar checksum verification, auto bootstrap, in-process refresh, and host `docker compose exec orphanet-link python -c 'from orphanet_link.services.data_resolver import ensure_database'`. Document the exact initial tag/digest tuple and reviewed promotion flow: publish/inspect data release, verify full artifact digest and metadata, update code/Compose/container declaration pins together, pass CI immutable-bundle smoke, then deploy an attested image/data tuple.

- [ ] **Step 4: Complete repository verification.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py tests/unit/test_compose_hardening.py tests/unit/test_docs_and_ci_contracts.py -q
  make ci-local
  make docker-build
  ```

  Expected: tests prove no latest/sidecar/fallback route; full local gate passes; central container CI immutable-bundle profile materializes the fixed asset before health/MCP smoke.

- [ ] **Step 5: Commit.**

  ```bash
  git add .github/workflows/build-data.yml container-release.json .env.docker.example docs README.md tests
  git commit -m "docs: bind Orphanet deployment to immutable data release"
  ```

### Task 6: PR, release, deployment, and issue-close evidence

- [ ] **Step 1:** Add the #23 entry to `CHANGELOG.md`; push `fix/immutable-data-init-23`; open a draft PR containing `Fixes #23`, exact tag/digest table, and local test/Compose output.
- [ ] **Step 2:** Merge only after independent review, `make ci-local`, full container CI, immutable-bundle smoke, and every required check pass for the exact head SHA. Record PR, head/merge SHA, checks, review, and smoke artifact URLs.
- [ ] **Step 3:** Create the required protected application release from merged main, capture the attested GHCR digest/SBOM/scan evidence, and deploy the digest plus exact data tuple through the supported Compose profile. Verify health revision and MCP initialize/list-tools through the proxy/router.
- [ ] **Step 4:** Capture runtime evidence: `current -> reference/a8af3fc39cca2acedd12c188cb0e1f907ac320e73d2b965c17ad5a28c5f5fe38`; `sqlite3 'file:/data/current/orphanet.sqlite?mode=ro&immutable=1' 'select schema_version,orphanet_version,orphanet_date from meta'`; read-only app mount; init hash success; and no application GitHub/Orphadata download logs. Post merge SHA, image digest, data tag/digests, commands/output summary to #23, then close it.
