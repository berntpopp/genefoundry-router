# Fleet Ingest Artifact Hardening Implementation Plan

> Historical record — this document records the design or plan as of its date. Current behavior is
> defined by implemented code, standards, release evidence, and tests.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all eight remaining fleet ingest paths fail closed on unsafe redirects, oversized or corrupt artifacts, decompression bombs, and partial writes without slowing normal streamed downloads.

**Architecture:** Every repository implements the same behavioral contract locally: fixed API endpoints reject redirects, legitimate release/PURL flows follow a small manually validated HTTPS allowlist, and downloads stream through counted same-directory temporary files before atomic replacement. Compressed artifacts are hashed and expanded through bounded loops; MaveDB additionally preflights and enforces archive-member limits. No shared runtime package is introduced, so repositories remain independently deployable and releasable.

**Tech Stack:** Python 3.12+, HTTPX 0.28.x, Pydantic v2 settings, `hashlib`, `gzip`, `tarfile`, `zipfile`, `tempfile`, `zstandard`, pytest, respx, Ruff, mypy, uv.

---

## Global Constraints

- Work from each repository's current `origin/main`; create branch `fix/ingest-artifact-hardening-2026-07-10` only after confirming the worktree is clean.
- TDD is mandatory: add a failing property test, run it and record the expected failure, implement the minimum change, rerun targeted tests, then run `make ci-local`.
- One atomic implementation commit per repository. Do not combine repositories in a commit or PR.
- Do not add a fleet-shared Python dependency, forward authorization headers, change MCP transport, change response envelopes, or touch production/npm Compose overlays.
- Keep existing stream chunk sizes. Count and hash in the same pass; never add a second full-file read when the digest can be computed during download.
- `Content-Length` and release metadata size are prechecks only. The streamed byte count is authoritative.
- Download to a unique temporary file in the destination directory, validate it fully, then call `os.replace()`. On every exception, remove temporary files and preserve the previous destination.
- HTTPX read timeouts bound inactivity between chunks, not total transfer duration. Retain current timeouts and add `max_download_seconds` where the repository already exposes download settings.
- Fixed JSON APIs and currently direct sources use `follow_redirects=False`.
- Legitimate cross-host flows use `follow_redirects=False` plus a manual loop that validates the initial URL and every `Location` before sending the next request: HTTPS only, no userinfo, port absent or 443, exact normalized hostname, relative `Location` resolution, and at most five hops.
- GitHub release flows may allow `api.github.com`, `github.com`, and `release-assets.githubusercontent.com`. Add `objects.githubusercontent.com` only if a live or documented supported flow requires it. Do not allow `*.githubusercontent.com`.
- A checksum obtained from the same release or metadata record is corruption detection, not independent publisher authentication. Documentation and logs must not call it a signature, attestation, or proof of authenticity.
- Limits are Pydantic settings, not literals in stream loops. Set defaults to the next practical binary boundary above at least twice the largest artifact measured on 2026-07-10. Descriptions must include the measured size/date and explain the environment override.
- Every modified module must remain below the fleet 600-LOC budget.

## Canonical Local Interfaces

Repositories with more than one redirecting or compressed flow create a local `ingest/download_security.py` (or `services/download_security.py` when the flow is service-owned) with these interfaces. Single-flow repositories keep equivalent private functions in their existing downloader.

```python
@dataclass(frozen=True)
class DownloadPolicy:
    allowed_hosts: frozenset[str]
    max_redirects: int = 5
    max_bytes: int = 128 * 1024 * 1024
    max_seconds: float | None = None


_SAFE_REDIRECT_HEADERS = frozenset(
    {"accept", "user-agent", "if-none-match", "if-modified-since"}
)


def validate_https_url(url: httpx.URL, policy: DownloadPolicy) -> None:
    host = (url.host or "").lower()
    if url.scheme != "https":
        raise DownloadError(f"download URL must use HTTPS: {url}")
    if url.userinfo:
        raise DownloadError("download URL must not contain user information")
    if url.port not in (None, 443):
        raise DownloadError(f"download URL port {url.port} is not allowed")
    if host not in policy.allowed_hosts:
        raise DownloadError(f"download host {host} is not allowed")


@contextmanager
def open_validated_stream(
    client: httpx.Client,
    url: str,
    *,
    headers: Mapping[str, str],
    policy: DownloadPolicy,
) -> Iterator[httpx.Response]:
    current = httpx.URL(url)
    safe_headers = {
        name: value
        for name, value in headers.items()
        if name.lower() in _SAFE_REDIRECT_HEADERS
    }
    for hop in range(policy.max_redirects + 1):
        validate_https_url(current, policy)
        request = client.build_request("GET", current, headers=safe_headers)
        response = client.send(request, stream=True, follow_redirects=False)
        if response.status_code not in {301, 302, 303, 307, 308}:
            try:
                yield response
            finally:
                response.close()
            return
        location = response.headers.get("Location")
        response.close()
        if location is None:
            raise DownloadError("redirect response is missing Location")
        if hop == policy.max_redirects:
            raise DownloadError(f"download exceeded {policy.max_redirects} redirects")
        current = current.join(location)
    raise AssertionError("redirect loop exhausted unexpectedly")


def stream_atomic(
    response: httpx.Response,
    destination: Path,
    *,
    max_bytes: int,
    expected_size: int | None = None,
    hasher: Any | None = None,
    max_seconds: float | None = None,
) -> int:
    raw_length = response.headers.get("Content-Length")
    try:
        content_length = int(raw_length) if raw_length is not None else None
    except ValueError:
        content_length = None
    if content_length is not None and content_length > max_bytes:
        raise DownloadError(
            f"download Content-Length {content_length} exceeds {max_bytes} bytes"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=destination.parent, suffix=".download.tmp")
    tmp_path = Path(tmp_name)
    started = time.monotonic()
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_bytes():
                written += len(chunk)
                if written > max_bytes:
                    raise DownloadError(f"download exceeded {max_bytes} bytes")
                if max_seconds is not None and time.monotonic() - started > max_seconds:
                    raise DownloadError(f"download exceeded {max_seconds:g} seconds")
                handle.write(chunk)
                if hasher is not None:
                    hasher.update(chunk)
        if expected_size is not None and written != expected_size:
            raise DownloadError(
                f"download size mismatch: expected {expected_size}, received {written}"
            )
        os.replace(tmp_path, destination)
        return written
    finally:
        tmp_path.unlink(missing_ok=True)


def copy_bounded(source: BinaryIO, destination: BinaryIO, *, max_bytes: int) -> int:
    written = 0
    while chunk := source.read(min(1 << 20, max_bytes - written + 1)):
        written += len(chunk)
        if written > max_bytes:
            raise DownloadError(f"expanded artifact exceeded {max_bytes} bytes")
        destination.write(chunk)
    return written
```

Response hooks or post-hoc `response.history` inspection are forbidden because the untrusted request has already occurred. Each repository substitutes its existing `DownloadError` import and retains its current `_CHUNK_SIZE`/`_CHUNK` value in `iter_bytes()` and `copy_bounded()`.

Each repository substitutes its existing `DownloadError` type. MaveDB, which has no `DownloadError`, uses `DataUnavailableError` for invalid artifacts/policies and `ServiceUnavailableError` for network failures.

---

### Task 1: `clinvar-link` Raw Sources and Prebuilt Bundle

**Files:**
- Create: `../clinvar-link/clinvar_link/ingest/download_security.py`
- Modify: `../clinvar-link/clinvar_link/config.py:49-148`
- Modify: `../clinvar-link/clinvar_link/ingest/downloader.py:21-117`
- Modify: `../clinvar-link/clinvar_link/ingest/builder.py:106-116`
- Modify: `../clinvar-link/clinvar_link/ingest/bundle.py:41-324`
- Test: `../clinvar-link/tests/test_builder.py`
- Test: `../clinvar-link/tests/test_bundle.py`

- [ ] **Step 1: Add failing raw-download and decompression tests**

Add settings overrides and tests proving that a lying response cannot overwrite the old gzip and that expanded gzip is bounded:

```python
@respx.mock
def test_download_source_stream_limit_preserves_existing(tmp_path: Path) -> None:
    url = "https://ftp.ncbi.nlm.nih.gov/source.gz"
    destination = tmp_path / "source.gz"
    destination.write_bytes(b"old-valid")
    respx.get(url).mock(return_value=httpx.Response(200, content=b"123456789"))
    with pytest.raises(DownloadError, match="exceeded 8 bytes"):
        download_source(url, destination, cache_path=tmp_path / "cache.json", max_bytes=8)
    assert destination.read_bytes() == b"old-valid"
    assert list(tmp_path.glob("*.download.tmp")) == []


def test_open_source_rejects_expanded_gzip_over_limit(tmp_path: Path) -> None:
    source = tmp_path / "source.txt.gz"
    with gzip.open(source, "wb") as handle:
        handle.write(b"x" * 33)
    with pytest.raises(DownloadError, match="expanded source exceeded 32 bytes"):
        with _open_source(source, max_expanded_bytes=32) as handle:
            handle.read()
```

- [ ] **Step 2: Run the raw tests and verify failure**

Run: `cd ../clinvar-link && uv run pytest tests/test_builder.py -q -k 'stream_limit or expanded_gzip'`

Expected: FAIL because `download_source()` and `_open_source()` do not accept the limit arguments and the old destination is truncated during overflow.

- [ ] **Step 3: Implement atomic source streaming and bounded gzip reads**

Add `SOURCE_MAX_BYTES`, `SOURCE_MAX_EXPANDED_BYTES`, `BUNDLE_MAX_BYTES`, `BUNDLE_MAX_EXPANDED_BYTES`, `METADATA_MAX_BYTES`, and `MAX_DOWNLOAD_SECONDS` to `Settings`. Defaults: 1 GiB source, 8 GiB expanded source, 2 GiB bundle, 8 GiB expanded database, 1 MiB metadata, and 3600 seconds. Implement a counted temporary writer and a `RawIOBase` counting wrapper used beneath `gzip.GzipFile`/`TextIOWrapper`; raise `DownloadError` before returning bytes above the expanded limit. Set the NCBI client to `follow_redirects=False`.

```python
def _stream_to_file(response: httpx.Response, path: Path, *, max_bytes: int) -> str:
    length = _int_or_none(response.headers.get("Content-Length"))
    if length is not None and length > max_bytes:
        raise DownloadError(f"download Content-Length {length} exceeds {max_bytes} bytes")
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".download.tmp")
    digest = hashlib.sha256()
    written = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in response.iter_bytes(_CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise DownloadError(f"download exceeded {max_bytes} bytes")
                handle.write(chunk)
                digest.update(chunk)
        os.replace(tmp_name, path)
        return digest.hexdigest()
    finally:
        Path(tmp_name).unlink(missing_ok=True)
```

- [ ] **Step 4: Add failing bundle redirect, checksum, and expansion tests**

```python
@respx.mock
def test_bundle_rejects_unapproved_intermediate_redirect(tmp_path: Path) -> None:
    asset = "https://github.com/berntpopp/clinvar-link/releases/download/v1/clinvar.sqlite.zst"
    blocked = respx.get("https://evil.example/payload").mock(return_value=httpx.Response(200))
    respx.get(asset).mock(return_value=httpx.Response(302, headers={"Location": blocked.url}))
    with pytest.raises(DownloadError, match="host evil.example is not allowed"):
        download_verify_install(
            asset,
            db_path=tmp_path / "db.sqlite",
            staging_dir=tmp_path,
            expected_sha256="a" * 64,
            max_compressed_bytes=1024,
            max_expanded_bytes=1024,
        )
    assert blocked.called is False


@respx.mock
def test_checksum_sidecar_requires_sha256_hex() -> None:
    url = "https://github.com/owner/repo/releases/download/v1/db.zst"
    respx.get(f"{url}.sha256").mock(return_value=httpx.Response(200, text="not-a-digest db.zst"))
    with pytest.raises(DownloadError, match="invalid SHA-256"):
        fetch_sibling_sha256(url)


def test_bundle_expansion_limit_preserves_database(tmp_path: Path) -> None:
    db_path = tmp_path / "clinvar.sqlite"
    db_path.write_bytes(b"old-db")
    compressed = zstandard.ZstdCompressor().compress(b"x" * 65)
    with pytest.raises(DownloadError, match="expanded bundle exceeded 64 bytes"):
        _decompress_bundle_bytes_for_test(compressed, db_path, max_expanded_bytes=64)
    assert db_path.read_bytes() == b"old-db"
```

- [ ] **Step 5: Run bundle tests and verify failure**

Run: `cd ../clinvar-link && uv run pytest tests/test_bundle.py -q -k 'unapproved_intermediate or requires_sha256 or expansion_limit'`

Expected: FAIL because redirects are followed automatically, checksum syntax is not validated, and zstd output is unbounded.

- [ ] **Step 6: Implement the ClinVar bundle policy**

Create `download_security.py` with the canonical interfaces. Use a direct no-redirect client for `api.github.com`; allow only `github.com` and `release-assets.githubusercontent.com` for release assets and sidecars. Limit release JSON/checksum bodies before parsing, validate `re.fullmatch(r"[0-9a-fA-F]{64}", digest)`, stream zstd and SHA-256 together, then expand through `ZstdDecompressor().stream_reader()` and `copy_bounded()`. Keep `os.replace()` after checksum and expansion validation. Add `BUNDLE_EXPECTED_SHA256: str | None`; an explicit `BUNDLE_URL` must have this value or a valid mandatory sibling sidecar.

```python
with decompressor.stream_reader(zst_source) as reader, tmp_db.open("wb") as writer:
    bytes_db = copy_bounded(reader, writer, max_bytes=max_expanded_bytes)
os.replace(tmp_db, db_path)
```

- [ ] **Step 7: Run ClinVar verification**

Run: `cd ../clinvar-link && uv run pytest tests/test_builder.py tests/test_bundle.py -q`

Expected: PASS.

Run: `cd ../clinvar-link && make ci-local`

Expected: all format, lint, LOC, mypy, unit, and integration gates pass.

- [ ] **Step 8: Commit ClinVar atomically**

```bash
cd ../clinvar-link
git add clinvar_link/config.py clinvar_link/ingest/download_security.py clinvar_link/ingest/downloader.py clinvar_link/ingest/builder.py clinvar_link/ingest/bundle.py tests/test_builder.py tests/test_bundle.py
git commit -m "fix(security): bound and validate ingest artifacts"
```

---

### Task 2: `gencc-link` Plain Export

**Files:**
- Modify: `../gencc-link/gencc_link/config.py:24-84`
- Modify: `../gencc-link/gencc_link/ingest/downloader.py:182-300`
- Test: `../gencc-link/tests/test_downloader.py`

- [ ] **Step 1: Add failing redirect, overflow, and preservation tests**

```python
@respx.mock
def test_download_rejects_redirect_without_following(tmp_path: Path) -> None:
    cfg = GenCCDataConfigModel(data_dir=tmp_path, max_download_bytes=1024)
    target = respx.get("https://evil.example/export.tsv").mock(return_value=httpx.Response(200))
    respx.get(EXPORT_URL).mock(return_value=httpx.Response(302, headers={"Location": target.url}))
    with pytest.raises(DownloadError, match="302"):
        download_export(cfg)
    assert target.called is False


@respx.mock
def test_chunked_overflow_preserves_existing_export(tmp_path: Path) -> None:
    cfg = GenCCDataConfigModel(data_dir=tmp_path, max_download_bytes=8)
    destination = tmp_path / EXPORT_FILENAME
    destination.write_text("old", encoding="utf-8")
    respx.get(EXPORT_URL).mock(return_value=httpx.Response(200, content=b"123456789"))
    with pytest.raises(DownloadError, match="exceeded 8 bytes"):
        download_export(cfg)
    assert destination.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob("*.download.tmp")) == []
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd ../gencc-link && uv run pytest tests/test_downloader.py -q -k 'rejects_redirect or chunked_overflow'`

Expected: FAIL because redirects are followed and the final export is written directly without a limit.

- [ ] **Step 3: Implement direct no-redirect atomic streaming**

Add `max_download_bytes: int = Field(default=128 * 1024 * 1024, ge=1, le=4 * 1024**3)` and `max_download_seconds: int = Field(default=900, ge=30, le=7200)` to `GenCCDataConfigModel`. Set both HEAD and GET clients to `follow_redirects=False`; allow only 200/304 for GET and 200 for HEAD. Replace `_stream_to_file()` with a same-directory `mkstemp()` counted writer using the Task 1 atomic pattern and `config.max_download_bytes`. Check `Content-Length` first and call `os.replace()` only after a successful stream.

```python
content_length = _int_or_none(response.headers.get("Content-Length"))
if content_length is not None and content_length > config.max_download_bytes:
    raise DownloadError(
        f"GenCC export Content-Length {content_length} exceeds "
        f"{config.max_download_bytes} bytes"
    )
_stream_to_file(response, export_path, max_bytes=config.max_download_bytes)
```

- [ ] **Step 4: Verify and commit GenCC**

Run: `cd ../gencc-link && uv run pytest tests/test_downloader.py tests/integration/test_live_download.py -q`

Expected: PASS; the live HEAD remains direct 200.

Run: `cd ../gencc-link && make ci-local`

Expected: all gates pass.

```bash
cd ../gencc-link
git add gencc_link/config.py gencc_link/ingest/downloader.py tests/test_downloader.py
git commit -m "fix(security): cap GenCC export downloads"
```

---

### Task 3: `hpo-link` PURL Sources and Prebuilt Database

**Files:**
- Create: `../hpo-link/hpo_link/ingest/download_security.py`
- Modify: `../hpo-link/hpo_link/config.py:26-104`
- Modify: `../hpo-link/hpo_link/ingest/downloader.py:43-206`
- Modify: `../hpo-link/hpo_link/ingest/release.py:24-240`
- Modify: `../hpo-link/hpo_link/ingest/builder.py:395-416`
- Test: `../hpo-link/tests/unit/test_downloader.py`
- Test: `../hpo-link/tests/unit/test_release.py`

- [ ] **Step 1: Add failing every-hop PURL tests**

```python
@respx.mock
def test_purl_chain_allows_github_asset_host(tmp_path: Path) -> None:
    purl = "https://purl.obolibrary.org/obo/hp/releases/2026-06-06/hp.json"
    github = "https://github.com/obophenotype/human-phenotype-ontology/releases/download/v1/hp.json"
    asset = "https://release-assets.githubusercontent.com/asset?id=1"
    respx.get(purl).mock(return_value=httpx.Response(302, headers={"Location": github}))
    respx.get(github).mock(return_value=httpx.Response(302, headers={"Location": asset}))
    respx.get(asset).mock(return_value=httpx.Response(200, content=b"{}"))
    cfg = _settings(tmp_path)
    cfg.data.max_source_bytes = 8
    with httpx.Client(follow_redirects=False) as client:
        result = download_file(
            client,
            purl,
            tmp_path / "hp.json",
            force=True,
            cached_validators={},
            config=cfg,
        )
    assert result.path == tmp_path / "hp.json"


@respx.mock
def test_purl_chain_blocks_intermediate_host_before_request(tmp_path: Path) -> None:
    purl = "https://purl.obolibrary.org/start"
    blocked = respx.get("https://169.254.169.254/latest/meta-data").mock(
        return_value=httpx.Response(200)
    )
    respx.get(purl).mock(return_value=httpx.Response(302, headers={"Location": blocked.url}))
    with httpx.Client(follow_redirects=False) as client, pytest.raises(
        DownloadError, match="not allowed"
    ):
        download_file(
            client,
            purl,
            tmp_path / "hp.json",
            force=True,
            cached_validators={},
            config=_settings(tmp_path),
        )
    assert blocked.called is False
```

- [ ] **Step 2: Run PURL tests and verify failure**

Run: `cd ../hpo-link && uv run pytest tests/unit/test_downloader.py -q -k 'purl_chain'`

Expected: FAIL because the injected client does not follow the chain and manual every-hop validation does not exist.

- [ ] **Step 3: Implement HPO source policies and limits**

Add `max_source_bytes=128 MiB`, `max_manifest_bytes=64 KiB`, `max_bundle_bytes=128 MiB`, `max_database_bytes=512 MiB`, and `max_download_seconds=1800` to `HPODataConfig`. Create the canonical local helper. The GitHub latest-release API uses a direct no-redirect request. PURL files use exact hosts `purl.obolibrary.org`, `github.com`, and `release-assets.githubusercontent.com`; stream each into an atomic counted temp file.

```python
HPO_SOURCE_POLICY = DownloadPolicy(
    allowed_hosts=frozenset(
        {"purl.obolibrary.org", "github.com", "release-assets.githubusercontent.com"}
    ),
    max_redirects=5,
)
```

- [ ] **Step 4: Add failing prebuilt manifest and expansion tests**

```python
@respx.mock
def test_manifest_rejects_invalid_digest() -> None:
    releases = _mock_releases_list("bad", b"x")
    manifest = _mock_manifest("bad", b"x")
    manifest["sha256"] = "not-sha256"
    respx.get(_GH_RELEASES_URL).mock(return_value=httpx.Response(200, json=releases))
    respx.get(_MANIFEST_URL).mock(return_value=httpx.Response(200, json=manifest))
    with httpx.Client(follow_redirects=False) as client:
        assert find_prebuilt_asset(client) is None


@respx.mock
def test_prebuilt_expansion_limit_preserves_old_database(tmp_path: Path) -> None:
    compressed = zstandard.ZstdCompressor().compress(b"x" * 65)
    sha256 = hashlib.sha256(compressed).hexdigest()
    respx.get(_ZST_URL).mock(return_value=httpx.Response(200, content=compressed))
    destination = tmp_path / "hpo.sqlite"
    destination.write_bytes(b"old")
    asset = PrebuiltAsset(
        hpo_version=_DATE,
        download_url=_ZST_URL,
        sha256=sha256,
        zst_bytes=len(compressed),
        sqlite_bytes=65,
    )
    with httpx.Client() as client, pytest.raises(DataUnavailableError, match="exceeded 64"):
        fetch_prebuilt_db(client, asset, destination, max_compressed_bytes=1024, max_db_bytes=64)
    assert destination.read_bytes() == b"old"
```

- [ ] **Step 5: Implement bounded manifest and database installation**

Extend `PrebuiltAsset` with `sqlite_bytes`. Read at most `max_manifest_bytes + 1`, require a 64-hex digest and positive `zst_bytes`/`sqlite_bytes` within configured limits, and reject mismatched streamed compressed size. Use the validated GitHub asset chain and bounded zstd reader. Validate the SQLite header (`b"SQLite format 3\x00"`) before `os.replace()`.

```python
if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
    logger.warning("manifest_invalid_sha256")
    return None
if zst_bytes <= 0 or sqlite_bytes <= 0:
    logger.warning("manifest_invalid_sizes")
    return None
```

- [ ] **Step 6: Verify and commit HPO**

Run: `cd ../hpo-link && uv run pytest tests/unit/test_downloader.py tests/unit/test_release.py -q`

Expected: PASS.

Run: `cd ../hpo-link && make ci-local`

Expected: all gates pass.

```bash
cd ../hpo-link
git add hpo_link/config.py hpo_link/ingest/download_security.py hpo_link/ingest/downloader.py hpo_link/ingest/release.py hpo_link/ingest/builder.py tests/unit/test_downloader.py tests/unit/test_release.py
git commit -m "fix(security): validate and bound HPO artifacts"
```

---

### Task 4: `hgnc-link` Bulk Files and REST Client

**Files:**
- Modify: `../hgnc-link/hgnc_link/config.py:30-165`
- Modify: `../hgnc-link/hgnc_link/ingest/downloader.py:87-154`
- Modify: `../hgnc-link/hgnc_link/api/client.py:33-81`
- Test: `../hgnc-link/tests/unit/test_downloader.py`
- Test: `../hgnc-link/tests/unit/test_client.py`

- [ ] **Step 1: Add failing REST redirect and bulk preservation tests**

```python
@respx.mock
async def test_rest_redirect_is_not_followed() -> None:
    target = respx.get("https://evil.example/info").mock(return_value=httpx.Response(200, json={}))
    respx.get(f"{_BASE}/info").mock(return_value=httpx.Response(302, headers={"Location": target.url}))
    client = _client()
    with pytest.raises(ServiceUnavailableError, match="302"):
        await client.info()
    assert target.called is False
    await client.aclose()


@respx.mock
def test_bulk_overflow_preserves_old_file(tmp_path: Path) -> None:
    cfg = HgncDataConfig(data_dir=tmp_path, complete_set_url=_URL, max_download_bytes=8)
    destination = tmp_path / "complete.json"
    destination.write_bytes(b"old")
    respx.get(_URL).mock(return_value=httpx.Response(200, content=b"123456789"))
    with pytest.raises(DownloadError, match="exceeded 8 bytes"):
        downloader.download_file(cfg, _URL, "complete.json")
    assert destination.read_bytes() == b"old"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd ../hgnc-link && uv run pytest tests/unit/test_client.py tests/unit/test_downloader.py -q -k 'redirect_is_not_followed or overflow_preserves'`

Expected: FAIL because both clients follow redirects and bulk files are written directly.

- [ ] **Step 3: Implement HGNC direct policies and atomic cap**

Add `max_download_bytes=128 MiB` and `max_download_seconds=900` to `HgncDataConfig`. Set the REST `AsyncClient` and ingest `Client` to `follow_redirects=False`. Map any 3xx REST response to `ServiceUnavailableError`. Replace `_stream_to_file()` with the counted atomic pattern; enforce the `Content-Length` precheck and actual byte count.

```python
elif 300 <= response.status_code < 400:
    raise ServiceUnavailableError(
        f"HGNC REST returned unexpected redirect {response.status_code}."
    )
```

- [ ] **Step 4: Verify and commit HGNC**

Run: `cd ../hgnc-link && uv run pytest tests/unit/test_client.py tests/unit/test_downloader.py -q`

Expected: PASS.

Run: `cd ../hgnc-link && make ci-local`

Expected: all gates pass.

```bash
cd ../hgnc-link
git add hgnc_link/config.py hgnc_link/api/client.py hgnc_link/ingest/downloader.py tests/unit/test_client.py tests/unit/test_downloader.py
git commit -m "fix(security): reject redirects and cap HGNC downloads"
```

---

### Task 5: `mgi-link` Reports and MouseMine Client

**Files:**
- Modify: `../mgi-link/mgi_link/config.py:49-190`
- Modify: `../mgi-link/mgi_link/ingest/downloader.py:103-166`
- Modify: `../mgi-link/mgi_link/api/mousemine.py:63-122`
- Test: `../mgi-link/tests/unit/test_downloader.py`
- Test: `../mgi-link/tests/unit/test_mousemine.py`

- [ ] **Step 1: Add failing client and report tests**

```python
@respx.mock
def test_mousemine_redirect_is_not_followed() -> None:
    config = MouseMineConfig(base_url=_BASE, max_retries=0, rate_limit_per_s=0)
    target = respx.get("https://evil.example/query").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    respx.get(f"{config.base_url}/query/results").mock(
        return_value=httpx.Response(302, headers={"Location": target.url})
    )
    client = MouseMineClient(config)
    with pytest.raises(ServiceUnavailableError, match="redirect"):
        client.get_marker("MGI:1")
    assert target.called is False
    client.close()


@respx.mock
def test_report_content_length_limit_preserves_existing(config: MgiDataConfig) -> None:
    config.max_download_bytes = 8
    destination = config.data_dir / REPORT_FILENAMES["markers"]
    destination.write_bytes(b"old")
    respx.get(config.report_url("markers")).mock(
        return_value=httpx.Response(200, content=b"x", headers={"Content-Length": "9"})
    )
    with pytest.raises(DownloadError, match="Content-Length"):
        download_file(config, "markers")
    assert destination.read_bytes() == b"old"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd ../mgi-link && uv run pytest tests/unit/test_mousemine.py tests/unit/test_downloader.py -q -k 'redirect_is_not_followed or content_length_limit'`

Expected: FAIL because redirects are followed and report sizes are not enforced.

- [ ] **Step 3: Implement MGI policies**

Add `max_download_bytes=1 GiB` and `max_download_seconds=1800` to `MgiDataConfig`. Set MouseMine `follow_redirects=False` and reject 3xx. Set report downloads to `follow_redirects=False`; MGI reports were direct 200 on 2026-07-10. Stream reports through a same-directory temporary file, enforce header and actual limits, then replace atomically.

```python
if response.is_redirect:
    raise ServiceUnavailableError(
        f"MouseMine returned unexpected redirect {response.status_code}."
    )
```

- [ ] **Step 4: Verify and commit MGI**

Run: `cd ../mgi-link && uv run pytest tests/unit/test_mousemine.py tests/unit/test_downloader.py -q`

Expected: PASS.

Run: `cd ../mgi-link && make ci-local`

Expected: all gates pass.

```bash
cd ../mgi-link
git add mgi_link/config.py mgi_link/api/mousemine.py mgi_link/ingest/downloader.py tests/unit/test_mousemine.py tests/unit/test_downloader.py
git commit -m "fix(security): harden MGI report acquisition"
```

---

### Task 6: `mondo-link` PURL and SSSOM Downloads

**Files:**
- Create: `../mondo-link/mondo_link/ingest/download_security.py`
- Modify: `../mondo-link/mondo_link/config.py:37-120`
- Modify: `../mondo-link/mondo_link/ingest/downloader.py:112-201`
- Test: `../mondo-link/tests/unit/test_downloader.py`

- [ ] **Step 1: Add failing legitimate-chain and blocked-hop tests**

```python
@respx.mock
def test_mondo_purl_follows_only_reviewed_chain(config: ServerSettings) -> None:
    purl = config.data.obo_url
    latest = "https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.obo"
    versioned = "https://github.com/monarch-initiative/mondo/releases/download/v1/mondo.obo"
    asset = "https://release-assets.githubusercontent.com/mondo?id=1"
    respx.get(purl).mock(return_value=httpx.Response(302, headers={"Location": latest}))
    respx.get(latest).mock(return_value=httpx.Response(302, headers={"Location": versioned}))
    respx.get(versioned).mock(return_value=httpx.Response(302, headers={"Location": asset}))
    respx.get(asset).mock(return_value=httpx.Response(200, content=b"format-version: 1.2\n"))
    config.data.obo_url = purl
    result = download_file(config, "obo", force=True)
    assert result.path is not None


@respx.mock
def test_mondo_purl_rejects_http_downgrade(config: ServerSettings) -> None:
    blocked = respx.get("http://github.com/mondo.obo").mock(return_value=httpx.Response(200))
    respx.get(config.data.obo_url).mock(
        return_value=httpx.Response(302, headers={"Location": blocked.url})
    )
    with pytest.raises(DownloadError, match="HTTPS"):
        download_file(config, "obo", force=True)
    assert blocked.called is False
```

- [ ] **Step 2: Run tests and verify failure**

Run: `cd ../mondo-link && uv run pytest tests/unit/test_downloader.py -q -k 'reviewed_chain or http_downgrade'`

Expected: FAIL because HTTPX follows all redirects without validating each target.

- [ ] **Step 3: Implement separate Mondo source policies**

Add `max_download_bytes=1 GiB` and `max_download_seconds=1800` to `MondoDataConfig`. Create the canonical helper. The OBO policy allows `purl.obolibrary.org`, `github.com`, and `release-assets.githubusercontent.com`; the SSSOM policy allows only `raw.githubusercontent.com`. Use manual redirects and atomic capped streaming. If the optional SSSOM fails, remove only its temporary file and retain any prior valid SSSOM file.

```python
MONDO_OBO_HOSTS = frozenset(
    {"purl.obolibrary.org", "github.com", "release-assets.githubusercontent.com"}
)
MONDO_SSSOM_HOSTS = frozenset({"raw.githubusercontent.com"})
```

- [ ] **Step 4: Add and pass overflow preservation test**

```python
@respx.mock
def test_optional_sssom_overflow_preserves_previous_file(config: ServerSettings) -> None:
    config.data.max_download_bytes = 8
    destination = config.data.data_dir / "mondo.sssom.tsv"
    destination.write_bytes(b"old")
    respx.get(config.data.sssom_url).mock(return_value=httpx.Response(200, content=b"123456789"))
    result = download_bulk(config, force=True)
    assert result["sssom"].path == destination
    assert destination.read_bytes() == b"old"
```

Run: `cd ../mondo-link && uv run pytest tests/unit/test_downloader.py -q`

Expected: PASS.

- [ ] **Step 5: Verify and commit Mondo**

Run: `cd ../mondo-link && make ci-local`

Expected: all gates pass.

```bash
cd ../mondo-link
git add mondo_link/config.py mondo_link/ingest/download_security.py mondo_link/ingest/downloader.py tests/unit/test_downloader.py
git commit -m "fix(security): validate Mondo release redirects"
```

---

### Task 7: `orphanet-link` XML and Prebuilt Gzip Database

**Files:**
- Create: `../orphanet-link/orphanet_link/ingest/download_security.py`
- Modify: `../orphanet-link/orphanet_link/config.py:31-126`
- Modify: `../orphanet-link/orphanet_link/ingest/downloader.py:109-252`
- Modify: `../orphanet-link/orphanet_link/services/data_resolver.py:36-173`
- Test: `../orphanet-link/tests/unit/test_downloader.py`
- Test: `../orphanet-link/tests/unit/test_data_resolver.py`

- [ ] **Step 1: Add failing XML atomicity test**

```python
@respx.mock
def test_xml_stream_overflow_preserves_previous_file(config: OrphanetDataConfig) -> None:
    config.max_source_bytes = 8
    filename = "en_product1.xml"
    destination = config.data_dir / filename
    destination.write_bytes(b"old")
    respx.get(f"{config.base_url}{filename}").mock(
        return_value=httpx.Response(200, content=b"123456789")
    )
    with pytest.raises(DownloadError, match="exceeded 8 bytes"):
        download_file(config, "product1", filename, force=True)
    assert destination.read_bytes() == b"old"
    assert list(config.data_dir.glob("*.download.tmp")) == []
```

- [ ] **Step 2: Run XML test and verify failure**

Run: `cd ../orphanet-link && uv run pytest tests/unit/test_downloader.py -q -k xml_stream_overflow`

Expected: FAIL because the XML destination is written directly and no size limit exists.

- [ ] **Step 3: Implement Orphadata source policy**

Add `max_source_bytes=1 GiB`, `max_bundle_bytes=256 MiB`, `max_database_bytes=2 GiB`, `max_metadata_bytes=64 KiB`, and `max_download_seconds=1800` to `OrphanetDataConfig`. Use a manual policy whose default exact host set is `{config.base_url.host}` and whose redirect hosts come from a new `allowed_source_redirect_hosts: list[str] = []` setting. Require HTTPS and atomic capped streaming.

```python
source_hosts = frozenset(
    {httpx.URL(config.base_url).host, *config.allowed_source_redirect_hosts}
)
```

- [ ] **Step 4: Add failing prebuilt redirect, gzip-bomb, and schema-preservation tests**

```python
@respx.mock
def test_prebuilt_rejects_unapproved_asset_redirect(config: OrphanetDataConfig) -> None:
    gz_url = "https://github.com/owner/repo/releases/download/v1/orphanet.sqlite.gz"
    sha_url = f"{gz_url}.sha256"
    respx.get(_GH_LATEST).mock(
        return_value=httpx.Response(200, json=_release_json(gz_url, sha_url))
    )
    blocked = respx.get("https://evil.example/db.gz").mock(return_value=httpx.Response(200))
    respx.get(gz_url).mock(
        return_value=httpx.Response(302, headers={"Location": blocked.url})
    )
    with pytest.raises(DataUnavailableError, match="not allowed"):
        fetch_prebuilt(config)
    assert blocked.called is False


@respx.mock
def test_gzip_expansion_limit_preserves_valid_database(
    config: OrphanetDataConfig, tmp_path: Path
) -> None:
    source_db = _make_tiny_db(tmp_path / "source")
    payload, digest = _gz_and_sha(source_db)
    config.max_database_bytes = source_db.stat().st_size - 1
    config.db_path.write_bytes(b"old-db")
    gz_url = "https://github.com/owner/repo/releases/download/v1/orphanet.sqlite.gz"
    sha_url = f"{gz_url}.sha256"
    respx.get(_GH_LATEST).mock(
        return_value=httpx.Response(200, json=_release_json(gz_url, sha_url))
    )
    respx.get(gz_url).mock(return_value=httpx.Response(200, content=payload))
    respx.get(sha_url).mock(return_value=httpx.Response(200, text=digest))
    with pytest.raises(DataUnavailableError, match="exceeded"):
        fetch_prebuilt(config)
    assert config.db_path.read_bytes() == b"old-db"


@respx.mock
def test_schema_is_checked_before_replace(config: OrphanetDataConfig, tmp_path: Path) -> None:
    invalid_db = _make_tiny_db(tmp_path / "invalid", schema_version=SCHEMA_VERSION + 1)
    payload, digest = _gz_and_sha(invalid_db)
    config.db_path.write_bytes(b"old-db")
    gz_url = "https://github.com/owner/repo/releases/download/v1/orphanet.sqlite.gz"
    sha_url = f"{gz_url}.sha256"
    respx.get(_GH_LATEST).mock(
        return_value=httpx.Response(200, json=_release_json(gz_url, sha_url))
    )
    respx.get(gz_url).mock(return_value=httpx.Response(200, content=payload))
    respx.get(sha_url).mock(return_value=httpx.Response(200, text=digest))
    with pytest.raises(DataUnavailableError, match="schema"):
        fetch_prebuilt(config)
    assert config.db_path.read_bytes() == b"old-db"
```

- [ ] **Step 5: Implement streamed verified prebuilt installation**

Use a direct no-redirect request for `api.github.com`, then the validated GitHub asset chain for `.gz` and `.sha256`. Read no more than `max_metadata_bytes + 1` for the sidecar and require 64-hex SHA-256. Stream gzip plus hash to a temporary compressed file; compare the digest; use `gzip.GzipFile(fileobj=compressed_handle)` plus `copy_bounded()` into a second temporary database. Open and validate the temporary SQLite schema before replacement.

```python
with gzip.GzipFile(fileobj=compressed_handle) as source, tmp_db.open("wb") as destination:
    copy_bounded(source, destination, max_bytes=config.max_database_bytes)
validate_database_schema(tmp_db)
os.replace(tmp_db, config.db_path)
```

- [ ] **Step 6: Verify and commit Orphanet**

Run: `cd ../orphanet-link && uv run pytest tests/unit/test_downloader.py tests/unit/test_data_resolver.py -q`

Expected: PASS.

Run: `cd ../orphanet-link && make ci-local`

Expected: all gates pass.

```bash
cd ../orphanet-link
git add orphanet_link/config.py orphanet_link/ingest/download_security.py orphanet_link/ingest/downloader.py orphanet_link/services/data_resolver.py tests/unit/test_downloader.py tests/unit/test_data_resolver.py
git commit -m "fix(security): stream and validate Orphanet artifacts"
```

---

### Task 8: `mavedb-link` API, Zenodo Dump, GitHub Bundle, and Archives

**Files:**
- Create: `../mavedb-link/mavedb_link/ingest/download_security.py`
- Modify: `../mavedb-link/mavedb_link/config.py:24-111`
- Modify: `../mavedb-link/mavedb_link/api/client.py:105-118`
- Modify: `../mavedb-link/mavedb_link/ingest/downloader.py:24-150`
- Modify: `../mavedb-link/mavedb_link/ingest/bundle.py:50-139`
- Modify: `../mavedb-link/mavedb_link/ingest/builder.py:55-126`
- Test: `../mavedb-link/tests/unit/test_client.py`
- Test: `../mavedb-link/tests/unit/test_ingest_acquire.py`
- Test: `../mavedb-link/tests/unit/test_ingest_builder.py`

- [ ] **Step 1: Add failing API and Zenodo acquisition tests**

```python
@pytest.mark.asyncio
@respx.mock
async def test_api_redirect_is_not_followed() -> None:
    target = respx.get("https://evil.example/api").mock(return_value=httpx.Response(200, json={}))
    respx.get("https://api.mavedb.org/api/v1/version").mock(
        return_value=httpx.Response(302, headers={"Location": target.url})
    )
    client = MaveDBClient(MaveDBApiConfig(max_retries=0))
    with pytest.raises(ServiceUnavailableError, match="redirect"):
        await client.get_version()
    assert target.called is False
    await client.aclose()


@respx.mock
def test_zenodo_missing_checksum_fails_closed(tmp_path: Path) -> None:
    payload = _zenodo_versions()
    payload["hits"]["hits"][-1]["files"][0]["checksum"] = None
    with respx.mock(base_url="https://zenodo.org/api") as mock:
        mock.get("/records").mock(return_value=httpx.Response(200, json=payload))
        with pytest.raises(DataUnavailableError, match="missing a valid md5 checksum"):
            resolve_latest_dump("11201736")


@respx.mock
def test_zenodo_overflow_preserves_existing_dump(tmp_path: Path) -> None:
    destination = tmp_path / "dump.tar.gz"
    destination.write_bytes(b"old")
    url = "https://zenodo.org/records/1/files/dump.tar.gz"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"123456789"))
    with pytest.raises(DataUnavailableError, match="exceeded 8 bytes"):
        download_file(
            url,
            destination,
            expected_md5="0" * 32,
            expected_size=9,
            max_bytes=8,
        )
    assert destination.read_bytes() == b"old"
```

- [ ] **Step 2: Run acquisition tests and verify failure**

Run: `cd ../mavedb-link && uv run pytest tests/unit/test_client.py tests/unit/test_ingest_acquire.py -q -k 'redirect_is_not_followed or missing_checksum or overflow_preserves'`

Expected: FAIL because the API follows redirects, absent Zenodo checksums are accepted, and the dump writes directly.

- [ ] **Step 3: Implement API and Zenodo policies**

Set the API `AsyncClient` to `follow_redirects=False` and map 3xx to `ServiceUnavailableError`. Add these `MirrorConfig` fields: `max_dump_bytes=4 GiB`, `max_bundle_bytes=2 GiB`, `max_database_bytes=8 GiB`, `max_archive_entries=10_000`, `max_archive_member_bytes=2 GiB`, `max_archive_expanded_bytes=16 GiB`, `max_metadata_bytes=1 MiB`, and `max_download_seconds=7200`. Zenodo allows only `zenodo.org`, requires metadata matching `md5:[0-9a-fA-F]{32}`, requires positive metadata size within the configured limit, and atomically streams/hash-checks/counts the dump. Record both the verified upstream MD5 and a locally computed SHA-256 in build provenance; label MD5 as corruption detection.

```python
match = re.fullmatch(r"md5:([0-9a-fA-F]{32})", checksum or "")
if match is None:
    raise DataUnavailableError("Zenodo dump is missing a valid md5 checksum")
```

- [ ] **Step 4: Add failing GitHub bundle checksum and expansion tests**

```python
@respx.mock
def test_bundle_missing_checksum_fails_closed(tmp_path: Path) -> None:
    url = "https://github.com/berntpopp/mavedb-link/releases/download/v1/db.zst"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"zstd"))
    respx.get(f"{url}.sha256").mock(return_value=httpx.Response(404))
    with pytest.raises(DataUnavailableError, match="expected SHA-256 is required"):
        bundle.pull("berntpopp/mavedb-link", "db.zst", url, tmp_path / "mavedb.sqlite")


def test_bundle_expansion_limit_preserves_database(tmp_path: Path) -> None:
    destination = tmp_path / "mavedb.sqlite"
    destination.write_bytes(b"old")
    compressed = zstandard.ZstdCompressor().compress(b"x" * 65)
    zst_path = tmp_path / "mavedb.sqlite.zst"
    zst_path.write_bytes(compressed)
    with pytest.raises(DataUnavailableError, match="exceeded 64 bytes"):
        bundle._decompress_replace(zst_path, destination, max_expanded_bytes=64)
    assert destination.read_bytes() == b"old"
```

- [ ] **Step 5: Implement GitHub bundle policy**

Use a direct no-redirect GitHub API request and the validated GitHub asset chain. Prefer release-asset `digest` matching `sha256:[0-9a-fA-F]{64}` and positive `size`; otherwise require a strict sibling sidecar. Add `bundle_expected_sha256: str | None` for explicit URLs and fail if neither it nor a valid sidecar is present. Stream/count/hash the zstd to temp and expand with `ZstdDecompressor().stream_reader()` plus `copy_bounded()` before atomic replacement.

```python
expected_sha256 = configured_digest or release_asset_digest or fetch_required_sidecar(asset_url)
if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
    raise DataUnavailableError("a valid expected SHA-256 is required for the MaveDB bundle")
```

- [ ] **Step 6: Add failing tar and ZIP archive-policy tests**

```python
def _limits(**overrides: int) -> ArchiveLimits:
    values = {
        "max_entries": 10,
        "max_member_bytes": 1024,
        "max_expanded_bytes": 4096,
    }
    values.update(overrides)
    return ArchiveLimits(**values)


def _make_tar(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "test.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for name, data in members:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def _make_zip(tmp_path: Path, members: list[tuple[str, bytes]]) -> Path:
    path = tmp_path / "test.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


@pytest.mark.parametrize("name", ["../escape.json", "/absolute.json"])
def test_archive_rejects_unsafe_member_name(tmp_path: Path, name: str) -> None:
    archive = _make_tar(tmp_path, [(name, b"{}"), ("main.json", b"{}")])
    with pytest.raises(DataUnavailableError, match="unsafe archive member"):
        with _open_dump(archive, limits=_limits()):
            pytest.fail("unsafe archive was opened")


def test_archive_rejects_duplicate_normalized_names(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, [("a/../main.json", b"{}"), ("main.json", b"{}")])
    with pytest.raises(DataUnavailableError, match="duplicate archive member"):
        with _open_dump(archive, limits=_limits()):
            pytest.fail("duplicate archive was opened")


def test_archive_rejects_total_expansion(tmp_path: Path) -> None:
    archive = _make_zip(tmp_path, [("main.json", b"12345"), ("scores.csv", b"67890")])
    with pytest.raises(DataUnavailableError, match="expanded size exceeds 8 bytes"):
        with _open_dump(archive, limits=_limits(max_expanded_bytes=8)):
            pytest.fail("oversized archive was opened")


def test_archive_rejects_link_member(tmp_path: Path) -> None:
    archive = tmp_path / "link.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        link = tarfile.TarInfo("main.json")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../etc/passwd"
        tf.addfile(link)
    with pytest.raises(DataUnavailableError, match="links are not allowed"):
        with _open_dump(archive, limits=_limits()):
            pytest.fail("link archive was opened")
```

- [ ] **Step 7: Run archive tests and verify failure**

Run: `cd ../mavedb-link && uv run pytest tests/unit/test_ingest_builder.py -q -k archive_rejects`

Expected: FAIL because current tar/ZIP handling has no entry-count, duplicate, member, or total expansion policy.

- [ ] **Step 8: Implement archive preflight and enforced reads**

Create this `ArchiveLimits` dataclass from `MirrorConfig`. For every tar or ZIP entry, normalize with `PurePosixPath`, reject absolute paths and `..`, reject links/special files, reject duplicate normalized names, and accumulate entry count and declared uncompressed bytes with checked limits. Require exactly one normalized `main.json`. Keep tar `filter="data"`, extract only approved members into `TemporaryDirectory`, and verify actual bytes while copying. Replace `_ZipArchive.read()` with a counted chunk loop instead of `ZipFile.read()`.

```python
@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int
    max_member_bytes: int
    max_expanded_bytes: int


def _safe_member_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise DataUnavailableError(f"unsafe archive member: {name}")
    return path.as_posix()


def _bounded_zip_read(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    output = bytearray()
    with archive.open(info) as source:
        while chunk := source.read(min(1 << 20, limit - len(output) + 1)):
            output.extend(chunk)
            if len(output) > limit:
                raise DataUnavailableError(
                    f"archive member {info.filename} exceeds {limit} bytes"
                )
    return bytes(output)
```

- [ ] **Step 9: Verify and commit MaveDB**

Run: `cd ../mavedb-link && uv run pytest tests/unit/test_client.py tests/unit/test_ingest_acquire.py tests/unit/test_ingest_builder.py -q`

Expected: PASS.

Run: `cd ../mavedb-link && make ci-local`

Expected: all gates pass.

```bash
cd ../mavedb-link
git add mavedb_link/config.py mavedb_link/api/client.py mavedb_link/ingest/download_security.py mavedb_link/ingest/downloader.py mavedb_link/ingest/bundle.py mavedb_link/ingest/builder.py tests/unit/test_client.py tests/unit/test_ingest_acquire.py tests/unit/test_ingest_builder.py
git commit -m "fix(security): harden MaveDB artifact ingestion"
```

---

### Task 9: Cross-Repository Adversarial Review and Release Gates

**Files:**
- Modify only files identified by concrete review findings in Tasks 1-8; any correction is committed to that repository's existing hardening commit with `git commit --amend` before its PR opens.

- [ ] **Step 1: Run the static fleet property audit**

```bash
for repo in clinvar gencc hpo hgnc mgi mondo orphanet mavedb; do
  root="../${repo}-link"
  printf '%s\n' "== $repo =="
  rg -n 'follow_redirects=True|gzip\.decompress\(|\.extractall\(' "$root" \
    --glob '*.py' --glob '!tests/**' || true
done
```

Expected: no `follow_redirects=True` remains in audited production clients; no `gzip.decompress()` remains in Orphanet; MaveDB extraction uses an explicit approved-member list plus `filter="data"`.

- [ ] **Step 2: Run Claude Code adversarial review per PR diff**

From each repository, run:

```bash
claude -p "Adversarially review git diff origin/main...HEAD for SSRF through every redirect hop, HTTPS downgrade, host matching bugs, Content-Length trust, streamed overflow, decompression bombs, archive traversal or link extraction, temp-file leaks, replacement before validation, checksum overclaims, and regressions to legitimate GitHub/PURL/CDN flows. Report only actionable findings with file and line."
```

Expected: no unresolved HIGH or MEDIUM finding. Fix valid findings with a failing regression test, rerun that repository's `make ci-local`, and amend its commit.

- [ ] **Step 3: Push one branch and open one PR per repository**

Run in each repository after its local gate is green:

```bash
git push -u origin fix/ingest-artifact-hardening-2026-07-10
gh pr create --title "fix(security): harden ingest artifact handling" \
  --body "Implements router issue #35 Phase 5: every-hop redirect validation, configurable streamed limits, atomic replacement, truthful checksum enforcement, and bounded decompression. Repository-specific tests and make ci-local pass."
```

Expected: eight PR URLs, each containing exactly one repository's hardening commit.

- [ ] **Step 4: Require green GitHub checks and resolved review threads**

For each PR:

```bash
pr_number=$(gh pr view --json number --jq .number)
gh pr checks --watch "$pr_number"
gh pr view "$pr_number" --json reviewDecision,mergeStateStatus,statusCheckRollup
```

Expected: all required checks succeed, `mergeStateStatus` is `CLEAN`, and no actionable review thread remains.

- [ ] **Step 5: Merge in dependency order**

Merge direct/plain flows first (`gencc`, `hgnc`, `mgi`), then redirect/bundle flows (`clinvar`, `hpo`, `mondo`, `orphanet`), then MaveDB:

```bash
pr_number=$(gh pr view --json number --jq .number)
gh pr merge "$pr_number" --squash --delete-branch
```

Expected: every PR reports merged and each repository's default branch contains its security tests.

- [ ] **Step 6: Bump versions after merge**

For each repository, pull `main`, use its existing single-source version mechanism and Make/release conventions, and make one version-only commit. Use a patch bump unless the repository's documented policy classifies new configuration fields as minor:

```bash
git switch main
git pull --ff-only origin main
git switch -c chore/ingest-hardening-version
uv version --bump patch
uv lock
uv sync --group dev
make ci-local
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore(release): bump version for ingest hardening"
git push -u origin chore/ingest-hardening-version
gh pr create --title "chore(release): bump version for ingest hardening" \
  --body "Post-merge patch release for the Phase 5 ingest artifact hardening. Version single-source and make ci-local gates pass."
```

Expected: `uv version` updates the single source in `pyproject.toml`, `uv.lock` carries the same package version, `make ci-local` passes, and a version PR is open. Add a `CHANGELOG.md` `Fixed` entry before `git add`; `clinvar-link`, which does not yet have a changelog, creates `CHANGELOG.md` with a `# Changelog` heading and the new version section.

Use the version printed by `uv version --short` as the `2026-07-10` release-section heading and add this exact `Fixed` bullet with `apply_patch`: “Hardened ingest downloads with validated redirects, configurable size limits, atomic replacement, and bounded decompression.”

- [ ] **Step 7: Publish and verify releases**

Merge each green version PR. For `gencc-link`, whose `.github/workflows/release.yml` is triggered by `v*`, tag the merged version and require the release-validation workflow. The other seven repositories have no package-release workflow; their package release gate is the merged version commit on `main` plus the version-single-source test, and their independent data-bundle tags are not repurposed as package releases.

```bash
version_pr=$(gh pr view chore/ingest-hardening-version --json number --jq .number)
gh pr checks --watch "$version_pr"
gh pr merge "$version_pr" --squash --delete-branch
git switch main
git pull --ff-only origin main
uv sync --group dev
uv run pytest tests/unit/test_version_single_source.py -q
```

Expected: the version PR is merged, installed metadata equals `pyproject.toml`, and the version-single-source test passes. In `gencc-link` only, run `version=$(uv version --short); git tag "v${version}"; git push origin "v${version}"; gh run watch --exit-status` and require the `Release validation` workflow to pass. Do not create package GitHub releases for repositories that have no package-release workflow.

- [ ] **Step 8: Verify closure evidence for router issue #35**

Create a table in the issue comment containing repository, merged PR, released version, `make ci-local` result, redirect policy, compressed limit, expanded limit, and integrity source. Confirm all eight rows from current default branches/releases before closing:

```bash
gh issue comment 35 --repo berntpopp/genefoundry-router --body-file /tmp/issue-35-phase5-evidence.md
gh issue close 35 --repo berntpopp/genefoundry-router --reason completed
```

Expected: issue #35 has authoritative PR/release evidence for all eight repositories and is closed as completed.

---

## Completion Audit

Before declaring Phase 5 complete, verify all of the following from current remote state:

- Eight hardening PRs are merged and eight post-merge versions are published.
- Every repository's required CI and `make ci-local` passed at the released commit.
- Fixed APIs do not follow redirects.
- Legitimate PURL/GitHub flows validate every hop before sending and still pass an allowed-chain test.
- Every downloader enforces both `Content-Length` precheck and actual streamed byte count.
- Every write preserves an existing artifact on HTTP, overflow, checksum, decompression, schema, and filesystem failure.
- ClinVar, HPO, Orphanet, and MaveDB decompression is bounded while output is produced.
- MaveDB tar and ZIP paths, types, links, duplicates, entry count, member size, and total expansion are enforced.
- Missing or malformed mandatory checksums fail closed.
- Documentation calls same-release hashes corruption detection and leaves publisher authentication as explicit follow-up work.
- No module exceeds 600 LOC, no production/npm Compose overlay changed, no new shared dependency was introduced, and no authorization header is forwarded.
