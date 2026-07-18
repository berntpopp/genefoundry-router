# HPO #23 — Immutable Reference Data Init-Sidecar Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace HPO’s serving-time latest-release/bootstrap/source-build behavior with an exact, digest-verified HPO bundle materialized by a hardened init sidecar and opened by the application using SQLite `mode=ro&immutable=1`.

**Architecture:** The data-release workflow produces immutable HPO SQLite bundles. A production-only immutable requirement fixes full tag `db-v2026-06-23`, asset name, compressed SHA-256, canonical expanded-tree SHA-256, schema version, HPO/HPOA identities, and byte ceilings. `hpo-data-init` is the sole process allowed to download that fixed artifact; under a lock it verifies, expands, schema-probes, fsyncs, moves it to `reference/d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6/`, and atomically replaces `current`. The serving app depends on successful completion, mounts the same volume read-only, never bootstraps or refreshes, and reads only the selected SQLite path.

**Tech Stack:** Python 3.12, Pydantic settings, SQLite URI immutable mode, zstandard, httpx, Docker Compose v2, GitHub Releases/Actions, pytest/respx.

---

**Repository and branch:** `/home/bernt-popp/development/hpo-link`, new branch `fix/immutable-data-init-23` from current `origin/main`. All paths below are relative to that repository.

## Reviewed production identity

The initial code/configuration commit must bind exactly this already published, non-draft release—not `latest`, an inferred date, or a checksum sidecar:

| Field | Exact value |
|---|---|
| GitHub release tag | `db-v2026-06-23` |
| Asset | `hpo-2026-06-23.sqlite.zst` |
| Download URL | `https://github.com/berntpopp/hpo-link/releases/download/db-v2026-06-23/hpo-2026-06-23.sqlite.zst` |
| Compressed SHA-256 | `d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6` |
| Expanded SQLite SHA-256 | `03764e576a27c19a67ef5834b74b4dcff750b8199edfdc5b71e2581d53ae5d45` |
| Expanded SQLite bytes | `136249344` |
| Canonical expanded-tree SHA-256 | `41297047271b59c0d02933a39a7e5e9b6d51c0116002512c04d95a65b5967af0` |
| Schema/HPO/HPOA identity | `1` / `2026-06-23` / `2026-06-23` |
| Compressed ceiling | `134217728` bytes |
| Expanded ceiling | `536870912` bytes |

The canonical expanded-tree digest is SHA-256 of the UTF-8 record `hpo.sqlite\0` + `0444\0` + decimal byte count + `\0` + expanded-file SHA-256. No artifact-provided manifest or `.sha256` sidecar is a trust root; both may be used only as diagnostic evidence after the committed compressed digest has passed.

## File map

| File | Change | Responsibility |
|---|---|---|
| `hpo_link/config.py` | Modify | Define/validate immutable deployment requirement and selected database path. |
| `hpo_link/immutable_data.py` | Create | Fixed-URL bounded download, integrity/schema/identity verification, lock, fsync, atomic selection. |
| `hpo_link/ingest/cli.py` | Modify | Add `materialize-data`; keep source `build`/`refresh` as explicit release-authoring commands only. |
| `hpo_link/data/repository.py` | Modify | Open selected SQLite through `mode=ro&immutable=1`. |
| `hpo_link/app.py`, `hpo_link/server_manager.py`, `docker/entrypoint.sh` | Modify | Remove HTTP/unified/stdio bootstrap and refresh behavior; serve only. |
| `hpo_link/services/refresh.py`, `hpo_link/ingest/release.py` | Delete | Remove serving-time bootstrap/latest/fallback surfaces. |
| `hpo_link/ingest/builder.py` | Modify | Remove `_try_prebuilt`/`ensure_database`; retain offline builder/rebuild APIs used by release authoring only. |
| `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/docker-compose.prod.yml`, `docker/docker-compose.npm.yml` | Modify | Run app default command separately from init command; model hardened writer/read-only app dependency in every deployment profile. |
| `container-release.json`, `.env.docker.example`, `docs/configuration.md`, `docs/data.md`, `docs/deployment.md`, `README.md` | Modify | Declare immutable-bundle sidecar, exact pins, and no application bootstrap/refresh. |
| `.github/workflows/build-data.yml` | Modify | Emit the canonical expanded-tree identity from the data publication pipeline. |
| `tests/unit/test_immutable_data.py`, `tests/unit/test_compose_hardening.py` | Create | Test materialization failures and rendered app/init isolation. |
| Existing `tests/unit/test_refresh.py`, `tests/unit/test_server_manager.py`, `tests/unit/test_release.py` | Modify/Delete | Remove obsolete bootstrap/latest assertions and replace them with no-download start-path assertions. |

### Task 1: Write the immutable materializer tests first

**Files:**
- Create: `tests/unit/test_immutable_data.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Build deterministic tiny SQLite test artifacts.**

  In `test_immutable_data.py`, create a helper that writes a SQLite `meta` table with `schema_version`, `hpo_version`, and `hpoa_version`, checkpoints it, compresses it with zstandard, and returns `bundle_bytes`, `compressed_sha256`, `expanded_sha256`, `expanded_size`, and `tree_sha256`. The tree helper must be:

  ```python
  def tree_sha256(path: Path) -> str:
      file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
      record = f"hpo.sqlite\0{0o444:o}\0{path.stat().st_size}\0{file_sha}"
      return hashlib.sha256(record.encode("utf-8")).hexdigest()
  ```

  Construct `ImmutableDataRequirement` with a pinned non-reserved tag `db-v2026-06-23`, explicit HTTPS URL, the generated hashes, exact schema/HPO/HPOA values, and ceilings one byte above actual sizes.

- [ ] **Step 2: Add the required failing tests.**

  ```python
  @respx.mock
  def test_materialize_verifies_and_selects_atomically(tmp_path: Path) -> None:
      requirement, bundle = _requirement_and_bundle(tmp_path)
      respx.get(str(requirement.bundle_url)).mock(return_value=httpx.Response(200, content=bundle))
      selected = materialize_immutable_data(requirement)
      assert selected == tmp_path / "reference" / requirement.compressed_sha256 / "hpo.sqlite"
      assert (tmp_path / "current").resolve() == selected.parent
      assert selected.stat().st_mode & 0o777 == 0o444
      assert json.loads(selected.with_name("identity.json").read_text()) == {
          "compressed_sha256": requirement.compressed_sha256,
          "expanded_tree_sha256": requirement.expanded_tree_sha256,
          "schema_version": 1,
          "hpo_version": "2026-06-23",
          "hpoa_version": "2026-06-23",
      }

  @pytest.mark.parametrize("field,value", [
      ("release_tag", "latest"), ("release_tag", ""),
      ("compressed_sha256", "bad"), ("expanded_tree_sha256", "f" * 63),
  ])
  def test_requirement_rejects_mutable_or_incomplete_pins(field: str, value: str) -> None:
      values = _requirement_values()
      values[field] = value
      with pytest.raises(ValidationError):
          ImmutableDataRequirement(**values)

  @respx.mock
  def test_tree_mismatch_preserves_existing_current(tmp_path: Path) -> None:
      old_requirement, old_bundle = _requirement_and_bundle(tmp_path, version="2026-06-22")
      respx.get(str(old_requirement.bundle_url)).mock(
          return_value=httpx.Response(200, content=old_bundle)
      )
      old_selected = materialize_immutable_data(old_requirement)
      requirement, bundle = _requirement_and_bundle(tmp_path, version="2026-06-23")
      invalid = requirement.model_copy(update={"expanded_tree_sha256": "0" * 64})
      respx.get(str(invalid.bundle_url)).mock(return_value=httpx.Response(200, content=bundle))
      with pytest.raises(DataUnavailableError, match="expanded-tree"):
          materialize_immutable_data(invalid)
      assert (tmp_path / "current").resolve() == old_selected.parent
      assert not list((tmp_path / "reference").glob(".*.staging-*"))

  @respx.mock
  def test_partial_materialization_never_becomes_current(tmp_path: Path) -> None:
      requirement, bundle = _requirement_and_bundle(tmp_path, version="2026-06-23")
      respx.get(str(requirement.bundle_url)).mock(return_value=httpx.Response(200, content=bundle[:-8]))
      with pytest.raises(DataUnavailableError, match="decompression"):
          materialize_immutable_data(requirement)
      assert not (tmp_path / "current").exists()
  ```

  Add a repository test creating a fixture DB then instantiating `HpoRepository`; monkeypatch `sqlite3.connect` and assert the URI includes both `mode=ro` and `immutable=1`.

- [ ] **Step 3: Run the failing tests.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py -q
  ```

  Expected: collection fails with `ModuleNotFoundError: No module named 'hpo_link.immutable_data'` and the URI assertion fails because the repository currently opens `?mode=ro` only.

- [ ] **Step 4: Commit the red specification.**

  ```bash
  git add tests/unit/test_immutable_data.py tests/unit/test_config.py
  git commit -m "test: specify immutable HPO data materialization"
  ```

### Task 2: Implement the fail-closed requirement and materializer

**Files:**
- Modify: `hpo_link/config.py`
- Create: `hpo_link/immutable_data.py`
- Modify: `hpo_link/data/repository.py`

- [ ] **Step 1: Add `ImmutableDataRequirement` to `hpo_link/config.py`.**

  It must be a frozen Pydantic model with `reference_root: Path`, `release_tag`, `bundle_url: AnyHttpUrl`, `compressed_sha256`, `expanded_tree_sha256`, `schema_version: int`, `hpo_version`, `hpoa_version`, `max_compressed_bytes`, and `max_expanded_bytes`. Reject `latest`, `main`, `master`, `head`, `stable`, and `current`; reject non-HTTPS URLs, non-64-hex digests, nonpositive ceilings, and a URL not ending in `/{release_tag}/{asset_filename}`. Give `ServerSettings` an `immutable_data` field whose production Compose defaults are the exact table above. Do not make a release tag optional and do not retain `prefer_prebuilt`, `prebuilt_db_url`, or `auto_bootstrap` in the serving configuration.

- [ ] **Step 2: Implement `hpo_link/immutable_data.py` under the 500-line cap.**

  Export only `ImmutableDataError`, `canonical_tree_sha256`, and `materialize_immutable_data(requirement: ImmutableDataRequirement) -> Path`. The function must:

  1. create `reference_root`, take exclusive `fcntl.flock` on `.materialize.lock`, and use `reference_root / f".d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6.staging-{os.getpid()}"`;
  2. stream the one fixed `bundle_url` through `open_validated_stream` with allowed hosts `github.com` and `release-assets.githubusercontent.com`, five redirects at most, the declared compressed ceiling, and no discovery endpoint;
  3. hash exact compressed bytes while writing a private temp file, reject before decompression on a digest mismatch;
  4. stream-decompress only to `staging/hpo.sqlite`, cap bytes, reject a non-SQLite header, verify the canonical expanded tree digest, then query `meta WHERE id=1` using `mode=ro&immutable=1` and require exactly `(1, "2026-06-23", "2026-06-23")` for this initial pin;
  5. write the five-field identity JSON atomically with mode `0444`, chmod the SQLite file `0444`, fsync every file plus staging/root directory, `os.replace(staging, reference/d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6)`, then atomically replace `current` with a symlink to that exact directory;
  6. on every exception, delete temporary bundle/staging material and leave an existing `current` unchanged.

  Return static `DataUnavailableError` messages; do not echo URLs, filesystem paths, response bytes, or upstream exception text into the MCP-visible failure path.

- [ ] **Step 3: Change HPO read-only access.**

  In `HpoRepository.__init__`, replace the connect URI with:

  ```python
  f"file:{self._path}?mode=ro&immutable=1"
  ```

  Keep `uri=True`, `check_same_thread=False`, and path-free error messages unchanged.

- [ ] **Step 4: Run the unit proof.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py tests/unit/test_repository_ontology.py -q
  ```

  Expected: valid materialization selects exactly one digest directory; latest/missing pins, compressed/tree/schema/HPO/HPOA mismatch, and partial data fail without moving `current`; repository opens immutable read-only.

- [ ] **Step 5: Commit.**

  ```bash
  git add hpo_link/config.py hpo_link/immutable_data.py hpo_link/data/repository.py tests/unit
  git commit -m "feat: materialize immutable HPO reference data"
  ```

### Task 3: Remove every serving-time bootstrap, latest release, and source fallback

**Files:**
- Modify: `hpo_link/ingest/cli.py`
- Modify: `hpo_link/ingest/builder.py`
- Modify: `hpo_link/app.py`
- Modify: `hpo_link/server_manager.py`
- Delete: `hpo_link/services/refresh.py`
- Delete: `hpo_link/ingest/release.py`
- Delete: `tests/unit/test_refresh.py`
- Delete: `tests/unit/test_release.py`
- Modify: `tests/unit/test_server_manager.py`

- [ ] **Step 1: Add the one allowed application-image materialization command.**

  Add `@app.command("materialize-data")` in `hpo_link/ingest/cli.py`:

  ```python
  @app.command("materialize-data")
  def materialize_data() -> None:
      """Download and atomically select the exact configured immutable HPO bundle."""
      from hpo_link.config import ServerSettings
      from hpo_link.immutable_data import materialize_immutable_data

      selected = materialize_immutable_data(ServerSettings().immutable_data)
      print(f"Materialized immutable HPO data: {selected.name}")
  ```

  `build` and `refresh` remain explicit data-authoring commands for the data-release workflow; neither app startup nor sidecar fallback calls them.

- [ ] **Step 2: Delete dynamic release resolution from serving code.**

  Delete `_try_prebuilt` and `ensure_database` from `hpo_link/ingest/builder.py`, delete `hpo_link/ingest/release.py`, and remove their unit tests. No function reachable from `create_app`, `create_unified_app`, or `start_stdio_server` may import `httpx`, `download_bulk`, `build_database`, `rebuild`, or `materialize_immutable_data`.

- [ ] **Step 3: Make all server starts non-materializing.**

  In `hpo_link/app.py`, make lifespan configure and log startup/shutdown only—remove `bootstrap_data`, `start_refresh_scheduler`, and `stop_refresh_scheduler`. In `hpo_link/server_manager.py`, delete the stdio `bootstrap_data` import/call. Replace obsolete tests with:

  ```python
  def test_server_start_modules_do_not_import_or_call_materialization() -> None:
      root = Path(__file__).resolve().parents[2]
      for relative in ("hpo_link/app.py", "hpo_link/server_manager.py"):
          source = (root / relative).read_text(encoding="utf-8")
          for forbidden in ("ensure_database", "download_bulk", "build_database", "materialize_immutable_data"):
              assert forbidden not in source
  ```

  Add an AST/text reachability test that imports app/server-manager modules and asserts `"ensure_database"`, `"download_bulk"`, `"build_database"`, and `"materialize_immutable_data"` are absent from the app/server start modules.

- [ ] **Step 4: Verify and commit.**

  ```bash
  uv run pytest tests/unit/test_server_manager.py tests/unit/test_cli.py tests/unit/test_immutable_data.py -q
  git add hpo_link tests/unit
  git commit -m "fix: remove HPO serving-time data bootstrap"
  ```

### Task 4: Make Compose express the hardened writer/read-only reader boundary

**Files:**
- Modify: `docker/Dockerfile`
- Modify: `docker/entrypoint.sh`
- Modify: `docker/docker-compose.yml`
- Modify: `docker/docker-compose.prod.yml`
- Modify: `docker/docker-compose.npm.yml`
- Create: `tests/unit/test_compose_hardening.py`

- [ ] **Step 1: Separate the image default server command from its entrypoint.**

  In `docker/Dockerfile`, replace `ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]` with `CMD ["/usr/local/bin/entrypoint.sh"]`. Replace `docker/entrypoint.sh` with `set -euo pipefail` followed by `exec python server.py --transport "${HPO_LINK_TRANSPORT:-unified}" --host "${HPO_LINK_HOST:-0.0.0.0}" --port "${HPO_LINK_PORT:-8000}"`; it must contain none of `ensure_database`, `bootstrap`, `build`, `refresh`, `curl`, or `httpx`.

- [ ] **Step 2: Add `hpo-data-init` to base Compose.**

  It uses the same code-only image, `command: ["hpo-link-data", "materialize-data"]`, writable `hpo-reference:/data`, `read_only: true`, `/tmp:rw,noexec,nosuid,size=64m,mode=1777`, `cap_drop: [ALL]`, `security_opt: [no-new-privileges:true]`, `init: true`, `restart: "no"`, bounded `memory: 1g`, `cpus: "1.0"`, `pids: 256`, and no `ports`, `expose`, or healthcheck. It attaches only to the default approved egress network because it must fetch one GitHub HTTPS asset.

  Configure it with the exact table values as `HPO_LINK_IMMUTABLE_DATA__*` fields. The full tag must appear literally as `HPO_LINK_IMMUTABLE_DATA__RELEASE_TAG: db-v2026-06-23`; do not derive `2026-06-23`.

  Make `hpo-link` depend on `hpo-data-init: {condition: service_completed_successfully}`, set `HPO_LINK_DATA__DB_FILENAME: current/hpo.sqlite`, set all old auto-bootstrap/prebuilt/refresh flags false or remove them, and replace its writable `hpo-data:/data` mount with read-only `hpo-reference:/data:ro`.

- [ ] **Step 3: Carry the same role split into prod and self-contained NPM.**

  In the prod overlay, clear build for both services, require the same digest image, retain `ports: !reset []` and `expose: ["8000"]` for the app, mount the app reference volume read-only, and preserve the init’s network access only on default. In NPM, add `hpo_data_init` with identical immutable settings and make `hpo_link.depends_on.hpo_data_init.condition` `service_completed_successfully`; attach init only to `hpo_link_internal_net`, app to both internal and proxy networks, and publish no host port.

- [ ] **Step 4: Add rendered Compose tests.**

  Assert for base+prod and standalone NPM models: init exists, has explicit argv, non-root image user, no ports/expose, `restart: no`, hardened controls and limits, writable `/data` plus `/tmp`; app waits for success, has read-only `/data`, has no bootstrap environment values, and carries no data-release URL. Assert `container-release.json` declares the same auxiliary service with `role: init`, `egress: approved-networks`, writable targets `/data` and `/tmp`, `smoke.profile: immutable-bundle`, and `data.mode: external-reference`.

- [ ] **Step 5: Run compose and smoke gates.**

  ```bash
  docker compose -f docker/docker-compose.yml -f docker/docker-compose.prod.yml config --format json >/tmp/hpo-compose.json
  docker compose -f docker/docker-compose.npm.yml --env-file .env.docker.example config --format json >/tmp/hpo-npm.json
  uv run pytest tests/unit/test_compose_hardening.py -q
  ```

  Expected: both models show a hardened init/app boundary; no app mount is writable and no NPM host port is rendered.

- [ ] **Step 6: Commit.**

  ```bash
  git add docker docker/Dockerfile tests/unit/test_compose_hardening.py
  git commit -m "feat: initialize HPO immutable data before serving"
  ```

### Task 5: Bind release publication, metadata, docs, and smoke to immutable data

**Files:**
- Modify: `.github/workflows/build-data.yml`
- Modify: `container-release.json`
- Modify: `.env.docker.example`
- Modify: `docs/configuration.md`, `docs/data.md`, `docs/deployment.md`, `README.md`
- Modify: `tests/unit/test_build_data_workflow.py`

- [ ] **Step 1: Make the data workflow emit expanded identity.**

  In `build-data.yml` package step, calculate raw SQLite SHA-256 and the exact canonical tree record above. Write `expanded_tree_sha256`, `sqlite_sha256`, and the byte ceilings into `manifest.json`. Keep the existing asset sidecar for operator convenience but never consume it in runtime code.

- [ ] **Step 2: Change `container-release.json` exactly.**

  Preserve `data.mode: "external-reference"`, `release_tag: "db-v2026-06-23"`, and its compressed digest; add `service.auxiliary` for `hpo-data-init` with role/egress/paths from Task 4, set `smoke.profile` to `immutable-bundle`, and add no image data allowlist beyond code modules. Do not write `data.mode: immutable-bundle`: that string is the smoke profile, while the authoritative data mode remains `external-reference`.

- [ ] **Step 3: Rewrite operator documentation.**

  Remove every recommendation to set `PREBUILT_DB_URL`, `PREFER_PREBUILT`, `AUTO_BOOTSTRAP`, `latest`, `docker compose exec hpo-link hpo-link-data refresh`, or an in-app scheduler. Document only the exact release tuple and that data refresh is a reviewed data-release promotion followed by a `container-release.json` pin change and image deployment; the running app never downloads or builds HPO data.

- [ ] **Step 4: Verify end-to-end before PR.**

  ```bash
  uv run pytest tests/unit/test_immutable_data.py tests/unit/test_compose_hardening.py tests/unit/test_build_data_workflow.py -q
  make ci-local
  make docker-build
  ```

  Expected: all unit/format/lint/LOC/mypy/coverage gates pass; Docker builds a code-only image; container CI’s immutable-bundle smoke starts `hpo-data-init`, verifies the exact bundle, and responds to health/MCP only after `current` exists.

- [ ] **Step 5: Commit.**

  ```bash
  git add .github/workflows/build-data.yml container-release.json .env.docker.example docs README.md tests
  git commit -m "docs: bind HPO deployment to immutable data release"
  ```

### Task 6: PR, release, deployment, and issue-close evidence

- [ ] **Step 1:** Add the #23 change to `CHANGELOG.md`, push `fix/immutable-data-init-23`, open a draft PR with `Fixes #23`, attach the exact release tuple and all local command outputs, and obtain review.
- [ ] **Step 2:** Merge only after `make ci-local`, container CI, immutable-bundle smoke, all required GitHub checks, and independent review are green for the exact PR SHA. Record PR URL, head SHA, merge SHA, check URLs, and container-CI artifact URL.
- [ ] **Step 3:** Tag/release the merged application according to the versioning standard, capture the attested image digest/SBOM/scan evidence, and deploy that digest with base+prod or NPM Compose. Verify the deployed health revision and run MCP `initialize` and `tools/list` through the router/proxy.
- [ ] **Step 4:** From the deployed init/app containers, record `current -> reference/d677a96efd8c274045241934c33b25dfb6fc9a6414c27bed7ae3334d05d4c9f6`, `sqlite3 'file:/data/current/hpo.sqlite?mode=ro&immutable=1' 'select schema_version,hpo_version,hpoa_version from meta'`, read-only app mount, and absence of app download logs. Post SHA, image digest, data tag/digests, smoke/deploy outputs to #23, then close it.
