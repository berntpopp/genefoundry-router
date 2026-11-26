# Changelog

All notable changes to genefoundry-router are documented here.

## [Unreleased]

## [0.14.16] - 2026-11-26

### Added

- **Federation Standard v1 §6: SSRF-safe transport for community backends (#409).** `genefoundry_router/egress.py` gives `trust=community` backends their own client: every A/AAAA answer is resolved at connect time and must be a global address (IPv4-mapped, 6to4 and NAT64 forms unwrapped), the connection goes to the validated IP with SNI, `Host` and certificate verification kept, only `https` on port 443 is allowed, redirects are never followed (a 3xx is `invalid_response`), and proxies are ignored. A literal IP host or a failed resolution at mount time skips that community backend with a warning. Core backends keep the shared pool unchanged.
- **Federation Standard v1 §3: community loader, forced trust and privacy guards (#410).** `GF_COMMUNITY_SERVERS_FILE` loads operator-hosted backends with `trust=community` forced; the loader rejects `trust`, `entrypoints`, `transform`, `url_env`, `service_token_env` and `enabled`, any URL other than `https://<host>/mcp` on port 443, core or tombstoned (`registry/tombstones.yaml`) names and namespaces, and duplicates; `operator`, `contact`, `ownership`, `server_name`, `source_name` and `data_egress` are required. Disabling uses the file's `disabled:` list or `GF_DISABLED_BACKENDS`. Startup fails unless `GF_ALLOW_COMMUNITY_BACKENDS=true` and `GF_DRIFT_MODE=enforce`; `GF_PRIVACY_PROFILE=patient` refuses community backends and a reranker URL that is not loopback or private. A core instance refuses community entries. The README inventory lists community backends in a separate generated table. Core behaviour is unchanged.

## [0.14.15] - 2026-11-26

### Changed

- **Router data follows the metadome-link v1.0.0 and pubtator-link v8.0.0 tool renames.** Search expansions (`tool-expansions.json`), the discoverability catalog and held-out tasks, and the `tests/fixtures/fleet_manifest.json` snapshot use the new names. Migration (old -> new): metadome `request_tolerance_landscape` -> `submit_tolerance_landscape`, `summarize_intolerant_regions` -> `find_intolerant_regions`; pubtator `suggest_corpus` -> `find_review_corpus`, `convert_article_ids` -> `map_article_ids`, `build_topic_literature_map` -> `generate_topic_literature_map`, `estimate_publication_context` -> `compute_publication_context`, `inspect_review_index` -> `get_review_index_status`, `preflight_review_sources` -> `get_review_source_coverage`. Pinned entry points are unchanged. The attested drift baseline (`fleet-baseline.json` and its companions) still pins the deployed fleet, which serves the old names; it is re-pinned after the renamed backends are deployed. No `transform.rename` bridge was added: the runtime drift guard would quarantine the renamed names because they are not in the pinned baseline.

### Added

- **Owner-approved `B2` exception for pubtator-link.** Its whole-server tool surface may reach a hard cap of 13,000 tokens (12,815 measured at v8.0.0) because of the per-property documentation the schema-documentation standard requires on its literature-review surface. Documented in `docs/TOOL-SURFACE-BUDGET-STANDARD-v1.md`, enforced by `SERVER_TOKEN_CAPS` in `scripts/check_tool_surface.py`, and guarded by tests (cap value, a surface above the cap fails, no other server is exempt).

## [0.14.14] - 2026-11-26

### Changed

- **Search expansions follow the current backend release descriptions.** The backends' Response-Envelope v1.2 releases rewrote 37 tool descriptions. Their entries in `genefoundry_router/data/tool-expansions.json` have been regenerated, and the expansion for pubtator `get_text_annotation_results`, which pubtator-link 7.11.0 removed, has been dropped. `hgnc_get_gene_cross_references` gets a hand-curated keyword overlay that names the identifiers it returns. `make bench-discoverability` scores 9.31/10, the same as before.
- **Test snapshots match the current backend releases.** `tests/fixtures/fleet_manifest.json` and `tests/discoverability/catalog.json` were captured from the latest released image of all 30 backends (344 tools). The attested drift baseline (`fleet-baseline.json` and its companions) still pins the deployed fleet. It will be re-pinned after the new releases are deployed (#402).
- **The untrusted-text inventory lists the surfaces pubtator-link fenced in 7.10.0.** The `pubtator` row of `docs/conformance/untrusted-text-inventory.yml` named `get_publication_passages` only. It now also names the `get_variant_evidence` literature snippets and, for `get_publication_annotations` and `get_pmc_annotations`, the passage text, `infons.license`, `infons.xml` and the BioC XML and PubTator-format document content. `get_publication_metadata` `bibtex` is recorded as not fenced: the server renders it from structured citation fields.

## [0.14.13] - 2026-11-26

### Fixed

- **The identifier gate reads `resolved_from` and the flagged keys inside `result` too.** A backend that carries its payload as an object under `result` (Response-Envelope v1.2) puts `resolved_from` and keys such as `unsupported_identifiers` there. `identifiers.py` read them at the envelope top level only and reported such an answer as `wrong_entity` or `silent_empty`. Both places are now read; a top-level value wins.

## [0.14.12] - 2026-11-26

### Fixed

- **The behaviour gate no longer reads a record's sub-object as a row list.** v0.14.11 looked for a grouped collection (a dict of lists) inside `result`. In spliceailookup-link this matched `result.consequence.aberrations`, record detail that `response_mode=minimal` rightly omits, and the gate reported the payload as destroyed. A grouped collection is now read at the envelope top level only, where `result` itself can be the dict of lists (`map_cross_ontology` in mondo-link, orphanet-link and hpo-link). Counted lists inside `result` are still read.

## [0.14.11] - 2026-11-26

### Changed

- **The behaviour gate reads the payload inside `result`.** Backends that adopted Response-Envelope v1.2 carry their payload under `result` (object) or `results` (array). `docs/conformance/behaviour.py` now finds rows, grouped collections, counts (`total`, `count`, …) and `truncated`/`has_more` inside `result` as well as at the envelope top level, so its filter, `response_mode` and truncation checks still gate those tools. A top-level value wins over one inside `result`. Envelopes with a flat payload are read as before.
- **OpenAI deep-research compatibility tools are exempt from the envelope and provenance rules.** `search` and `fetch` must return OpenAI's fixed document shape for a ChatGPT connector. Response-Envelope v1.2 lists them under "Exemptions" beside the ops/meta tools, MCP Behaviour Standard B9 names them, and the behaviour gate skips its provenance check for these two names.
- **Skills name the new result paths**: `result.location` (`hgnc_resolve_symbol`), `result.somatic_clinical_impact`, `result.oncogenicity` and `result.citations` (`clinvar_get_variant`), `result.summary.faf95…grpmax` (`gnomad_get_variant_frequencies`) and `result.simple` (`civic_search_molecular_profiles`).

## [0.14.10] - 2026-11-26

### Added

- **The transport probe tests `Origin` validation** (#394). A `POST /mcp` with a foreign `Origin` must return `403`; a request without `Origin` must return `200`; with `--allowed-origin`, a request with that `Origin` must return `200`. `fleet-probe` passes each backend's own public origin. MCP Transport Standard v1 §2.5 and §7.1 state the three cases.
- **Response-Envelope Standard v1.2: provenance is a core rule** (#393). Every successful result must carry non-empty `_meta.source`, `_meta.data_version` and `_meta.retrieved_at`. A cached result reports the time of the upstream fetch that filled the cache. The behaviour gate fails a tool whose successful result lacks a field (MCP Behaviour Standard B9).
- **The response envelope as a JSON Schema with test vectors** (`envelope/`). `envelope-v1.2.schema.json` (draft 2020-12, `$id` `https://genefoundry.org/schemas/envelope/1.2.0/envelope.schema.json`) covers the success envelope with its provenance, the error envelope and the closed v1.1 `untrusted_text` object at any depth. `envelope/vectors/` holds valid and invalid instances (one violation per invalid file, named by the rule it breaks) and `sanitize.json`, in/out vectors for the v1.1 text sanitation and `raw_sha256`. The README gives one-line validation in Python, JavaScript, R, Go and on the command line; CI runs the command lines over every vector (`make envelope-check`). Response-Envelope v1.2 now states that `source` stays a SHOULD on errors.

### Changed

- **The envelope schema's canonical home is [berntpopp/genefoundry-envelope](https://github.com/berntpopp/genefoundry-envelope)** (release v1.2.0). `envelope/README.md` and Response-Envelope v1.2 point there; the router keeps its copy, and a unit test pins `envelope/envelope-v1.2.schema.json` to the release's sha256.

## [0.14.9] - 2026-11-23

### Added

- **One listed `skill` tool serves the workflow skills to tools-only clients** (#384). This is the Agent Skills "skill tool" pattern used by Claude Code, Codex CLI and inspect_ai. It takes one free-text `command` parameter: `The skill name (no arguments). E.g., "germline-variant-classification"`. Its description is a fixed header adapted from inspect_ai 0.3.260 (MIT) plus an `<available_skills>` list of every served skill (name and SKILL.md `description`), sorted by name; a golden test pins it byte for byte to the benchmarked text. `skill(command="<name>")` returns exactly what `call_tool(name="skill_<name>")` returns (SKILL.md plus the router footer); both use the same code path. An unknown command returns a fixed `not_found` envelope that lists the real skill names and never echoes the input. The tool is absent from tools/list, and its call is rejected as unknown, under `X-GF-Skills: off` or when no skills are served. Benchmark (70 development classification cases): gpt-oss 120B scored .971 with this tool vs .857 with skills off (p=.008) and loaded a skill 44/70 times; the description pointers loaded one 0 times.

### Changed

- The server instructions' WORKFLOW SKILLS line now says ``load the matching one with `skill(command="<name>")` ``.

### Removed

- The five pinned-description pointers (` Workflow skill: call_tool(name="skill_<name>").`, `SKILL_POINTERS`, #376). They were loaded 0 times in the benchmark. `call_tool(name="skill_<name>")` and the skill hits in `search_tools` stay for back-compat.

- **Tool-Surface Budget Standard: two narrow, owner-approved exceptions (2026-11-23, #384).** The router's `skill` meta-tool may exceed the 1,200-token `B1` limit up to a hard cap of 1,600 tokens (1,273 today, 7 skills); the skill list must be trimmed or split before that cap is passed. Rule 5's 20,000-token router-surface limit applies to the default listing; the opt-in strict listing may reach 20,500. New guard tests in `tests/integration/test_strict_pinned_catalog.py` fail when `skill` exceeds 1,600 tokens, the default listing 20,000, or the strict listing 20,500.

Listing ceilings (chars/4 estimator): pinned listing 18,250 -> 19,431 (+1,181 = `skill` +1,273, pointers -92), ceiling 18,400 -> 19,500; strict 19,107 -> 20,288, ceiling 19,200 -> 20,400; slim 7,013 -> 8,194, ceiling 7,100 -> 8,300. Router-side only: `skill` is not in the drift fingerprint, so the drift baseline stays `fleet-2026-11-22b`.

## [0.14.8] - 2026-11-23

### Changed

- **`call_tool`'s parameter descriptions say `name` is an exact tool name, not a search** (#382). Clients such as inspect_ai validate `call_tool` arguments against the listed inputSchema before sending, so #375's server-side hint never reaches them; a benchmark run had Mistral Small send 240 `call_tool` calls with `{"query": "..."}` and no `name`. `name` now reads `Exact tool name, e.g. "hgnc_resolve_symbol". Not a search: to find a tool, call search_tools(query=...) first.`; `arguments` reads `The tool's own arguments as an object, e.g. {"query": "BRCA1"}.` (matching `hgnc_resolve_symbol`'s real `query` parameter). Strict and slim listings carry the same text. Pinned listing +31 tokens (18,250), strict +30 (19,107), slim +30 (7,013); all under their ceilings.

Router-side schema change only (`call_tool` is not in the drift fingerprint); drift baseline still `fleet-2026-11-22b`.

## [0.14.7] - 2026-11-23

### Fixed

- **An unknown tool name now names the nearest real tool** (#380). Invented names are the top cause of failed calls for weaker models. When the requested name's namespace is a served namespace and its tool part is close to a real tool there (`difflib`, cutoff 0.6), the `not_found` envelope's first suggestion names it; e.g. `hgnc_search_gene` gets ``Did you mean `hgnc_search_genes`? call_tool(name="hgnc_search_genes", arguments={...}).``; the generic search_tools hint follows. The suggestion is a catalog name only, never the requested string; it comes from the in-memory catalog of the last harvest and is confirmed with `get_tool` (quarantined or hidden tools are never suggested); a down or forbidden backend still answers `upstream_unavailable` first (#325). No backend calls.
- **A skill-file miss suggests the skill's file list** (#374). Its `suggestions` was the generic search_tools hint; it is now the fixed `Call call_tool(name="skill_<name>") to see the skill's listed files.`
- **`call_tool` without `name`, or with the target's arguments at top level, shows the call shape** (#375). Both now return `invalid_input` "call_tool takes `name` (required) and `arguments` only." with `allowed_values: ["arguments", "name"]` and the fixed hint `Use call_tool(name="<namespace>_<tool>", arguments={...}) — put the tool's own arguments inside `arguments`.` A missing `name` used to return FastMCP's raw pydantic dump, which echoed the caller's input.

### Changed

- The not-found guard's Layer 5 log-scrub filter moved from `notfound_guard.py` to `notfound_log_scrub.py` (600-LOC budget); no behaviour change.

No tool schema or description change; drift baseline still `fleet-2026-11-22b`.

## [0.14.6] - 2026-11-23

### Fixed

- **The private drift probe runs on the VPS host's Python 3.12 again.** The scheduled drift job has reported every new backend (cbioportal, civic, mitomap, moalmanac, opentargets, pgscatalog, strchive, ucsc) as unreachable since 2026-11-20: the host still runs the 2026-10-04 copy of `scripts/readonly_drift_probe.py`, which allowlists only 22 URLs, and the current copy could not be installed because its `except OSError, ProbeError, json.JSONDecodeError:` (PEP 758) is a `SyntaxError` on 3.12. The clause is parenthesized; `pyproject.toml` sets ruff `per-file-target-version = py312` for that file so the formatter no longer strips the parentheses, and a unit test parses it with `ast.parse(..., feature_version=(3, 12))`. `docs/deployment.md` states the 3.12 constraint and the allowlist count (30). No tool schema or description change; drift baseline unchanged. Operator: reinstall the script at `/usr/local/libexec/genefoundry/readonly_drift_probe.py` after this release.

## [0.14.5] - 2026-11-23

### Fixed

- **Agents now see the workflow skills without searching** (#376). In a 70-case germline benchmark the model never opened a skill and called `search_tools` in 3 of 70 cases, so skills reachable only as search hits went unseen. The first paragraph of the server instructions now names every served skill with its `call_tool(name="skill_<name>")` call and a few trigger words taken from its SKILL.md description (no skills served: no line). Pinned primary entries for a skill (`clinvar_get_variant`, `autopvs1_get_variant_pvs1_data` -> germline-variant-classification; `hpo_score_genes_for_phenotypes` -> phenotype-to-candidate-genes; `pubtator_search_literature` -> literature-evidence-review; `hgnc_resolve_symbol` -> identifier-resolution-and-lookup) end their **listed** description with ` Workflow skill: call_tool(name="skill_<name>").` The mapping (`SKILL_POINTERS`) is tested against `skills/` and the `servers.yaml` pins. `X-GF-Skills: off` drops both the instructions line and the pointers for that request. Step 2 of the instructions now says a `skill_<name>` hit is run via its `call` or read via `read.uri`. The search catalog, BM25 index, hit descriptions, call routing and SKILL.md files are unchanged; the drift baseline fingerprints backend definitions only, so it stays `fleet-2026-11-22b`.

## [0.14.4] - 2026-11-22

### Fixed

- **A missed skill read no longer says the tool is missing** (#372). `call_tool(name="read_resource" | "resources/read", ...)` with a URI that is not a served skill file, or `call_tool(name="skill_<unknown>")`, returned `not_found` "The requested tool is not available.", so a model concluded `read_resource` did not exist and searched for another tool. It now returns `not_found` with the fixed message "No skill file at that URI; only skill:// files listed in a skill's router note can be read via call_tool." The message never echoes the caller's URI or name; an ordinary unknown tool keeps the old message. No tool schema or description change; drift baseline still `fleet-2026-11-22b`.

## [0.14.3] - 2026-11-22

### Fixed

- **Tools-only clients can open a skill's supporting files** (#370). `call_tool(name="read_resource", arguments={"uri": "skill://<name>/references/<file>"})` (or `resources/read`) now returns any file the skills provider serves for a known skill, byte-identical to `resources/read`; before, only `SKILL.md` opened, so tools-only models ran e.g. `germline-variant-classification` without the references it says to read. A `SKILL.md` returned through `call_tool` ends with a short router note listing each supporting file's exact `call_tool` call (only for skills that have supporting files); `resources/read` output and the SKILL.md texts are unchanged. Traversal, unknown files, `_manifest` and other schemes stay `not_found`. `call_tool` skill reads are audited under `upstream_namespace="skill"` instead of `_unknown`. No tool schema or description change; drift baseline still `fleet-2026-11-22b`.

## [0.14.2] - 2026-11-22

### Fixed

- **Skill hits no longer take tool slots, open for tools-only clients, and can be hidden** (#368). `search_tools` returns up to `limit` tool hits plus at most one skill hit (the best, if it ranks within the top `limit`), so tool recall at a given `limit` matches 0.13.x. `call_tool(name="skill_<name>")` returns the SKILL.md text (same as `resources/read`) for clients without resources support, and so do the names tools-only agents guess, `read_resource` / `resources/read` with a `skill://<name>/SKILL.md` `uri` (skill URIs only: no general resource proxy). An unknown skill or any other URI stays `not_found`. Skill hits carry a `call` field shaped like a tool hit's, and the instructions say: read its `read.uri`, or call its `call` if you cannot read resources. The `X-GF-Skills: off` request header hides skill hits for that request (a gateway convenience for no-skill baselines; host policy remains the SEP-2640 opt-out). Pinned listing unchanged; no backend change, drift baseline still `fleet-2026-11-22b`.

## [0.14.1] - 2026-11-22

### Changed

- **Drift baseline re-pinned to `fleet-2026-11-22b`** from the deployed fleet (strict release-candidate capture; every live definition digest equals its signed release manifest): ucsc-link 0.6.3, gtex-link 3.2.5, stringdb-link 4.5.2, clinvar-link 0.10.6 (data `bundle-2026-11-16-r2`), spliceailookup-link 4.3.4, gnomad-link 9.3.6, vep-link 1.2.7, mondo-link 0.11.3. Still 30 backends and 345 tools: 6 tools changed, none added or removed. Router v0.14.0 must not be deployed against this fleet: its baseline pins the earlier definitions and `GF_DRIFT_MODE=enforce` refuses to boot.
- Definition changes (description only): gtex `get_gene_information` names its `not_found` list and both `get_gene_information`/`get_transcript_information` say `genome_build` GRCh37 returns no rows; gnomad `resolve_variant_id`/`search_variants` enrich the top 3 hits (was 5); mondo `resolve_disease`/`get_disease` say an umbrella term lists its descendants' genes. ucsc, stringdb, clinvar, spliceai and vep changed behaviour only.
- BM25 expansions regenerated for the 6 changed tools. Regenerated catalog snapshot, fleet provenance and `server.json`.

## [0.14.0] - 2026-11-22

### Added

- **Research workflow skills over MCP** (SEP-2640, closes #336). The router serves `skills/` as `skill://<name>/SKILL.md` resources and, on the 2026-07-28 protocol, through `skills/list` / `skills/get` (`fastmcp.skills.provider.SkillsDirectoryProvider`; `GF_SKILLS_DIR`, default `skills`; a missing directory fails at startup). `search_tools` returns a matching skill as a pointer (`skill`, `read.uri`, no `call`), never a callable tool; the pinned listing is unchanged.
- Six new skills next to `somatic-variant-interpretation`: `phenotype-to-candidate-genes`, `identifier-resolution-and-lookup`, `germline-variant-classification`, `literature-evidence-review`, `genetic-report-review`, `lay-summary`. Every skill carries one shared evidence-rules block (tool-returned ids only; confirmed / contradicted / not confirmable / not checked; build and transcript first; stop rules; research use only).
- `make lint-skills` (in `ci-local`): agentskills.io frontmatter, size cap, shared block identical, every `<namespace>_<tool>` a skill names exists in the catalog.
- The Docker image ships `skills/` and fails the build if no `SKILL.md` is present.

## [0.13.20] - 2026-11-22

### Fixed

- **Deploy Contract C3 image form works with `GF_IMAGE` alone** (#365). Compose v2 evaluates a nested `:?` even when the outer default is not used, so `${GF_IMAGE:-${<REPO>_IMAGE:?…}}` failed whenever only `GF_IMAGE` was set. The transitional form is now `${GF_IMAGE:-${<REPO>_IMAGE}}` (end state `${GF_IMAGE:?…}` unchanged): either variable alone renders, and with neither set the image is empty and every Compose command fails. `validate-deployed-overlay` accepts only these two forms, rejects the nested-`:?` form, and refuses `build:` on the application service (with a build context an empty image would build instead of failing). The gate's render now always supplies a `GF_IMAGE` placeholder, since the new form marks no variable required. The router's `docker/docker-compose.npm.yml` uses the new form.

## [0.13.19] - 2026-11-22

### Fixed

- **Offline deploy verification (`verify-deployment --bundle`) works on real release assets** (#362). The `attestation-bundle.json` asset holds one sigstore bundle per line (JSONL), but `gh attestation verify --bundle` picks its parser by file extension and reads a `.json` file as a single bundle, so every offline check failed with `proto: syntax error`. The verifier now copies the bundle into a private temp directory as `attestation-bundle.jsonl`, checks the release-manifest digest on that copy (the exact bytes gh reads) and removes it afterwards. A tampered or truncated bundle still fails the digest check before gh runs, and gh rejects any line that does not parse. Existing releases verify without being re-cut.

## [0.13.18] - 2026-11-22

### Fixed

- **The deploy verifier no longer reports a valid SPDX SBOM attestation as failed** (#359). `gh attestation verify --format json` prints the whole SBOM statement (~6.8 MB for a `-link` image), more than the 1 MiB command-output cap, and the capped result was read as a failed verification. Attestation checks now pass `--jq '[.[].verificationResult.statement.predicateType]'`, so gh prints only the verified predicate types; the verifier requires a nonempty list naming exactly the requested type. The output cap is unchanged, and a nonzero `gh` exit still fails closed as a policy violation. Online and offline (`--bundle`) modes.

## [0.13.17] - 2026-11-22

### Changed

- **Drift baseline re-pinned to `fleet-2026-11-22`** from the deployed fleet (strict release-candidate capture; every live definition digest equals its signed release manifest): autopvs1-link 4.2.3, civic-link 0.1.2, clingen-link 4.2.2, clinpgx-link 0.1.20, clinvar-link 0.10.5, genereviews-link 5.4.1, gnomad-link 9.3.5, litvar-link 6.3.2, mavedb-link 0.7.3, moalmanac-link 0.2.2, pubtator-link 7.7.4, spliceailookup-link 4.3.3, vep-link 1.2.6. Still 30 backends and 345 tools: 27 tools changed, none added or removed. Router v0.13.16 must not be deployed against this fleet: its baseline pins the earlier definitions and `GF_DRIFT_MODE=enforce` refuses to boot.
- Definition changes: clinvar `get_variant`/`get_variants` accept a GRCh38 CHROM-POS-REF-ALT (`id_type="coordinate"`); gnomad's eight variant-id fields no longer publish their `pattern` (still enforced by the server, which now answers a wrong shape with `invalid_input` and a recovery call), and `CHROM:POS` is no longer advertised; spliceai `genome_build` accepts `hg19`/`hg38`; pubtator `filters` accepts an object and `Retracted Publication`; description fixes in clingen `search_dosage`, clinpgx `search_records`, genereviews `search_genereviews`, mavedb `current_only`/`only_current` and moalmanac `NOT <name>`. autopvs1, civic, litvar and vep changed behaviour only.
- Pinned listing ceilings raised (approved by the owner's delegate in this release wave): default 18,000 → 18,200 tokens (measured 17,930 → 18,098), strict 18,800 → 19,000 (18,790 → 18,955). Mostly `clinvar_get_variant`'s coordinate `id_type` (+529 chars, clinvar-link#154).
- BM25 expansions regenerated for the 18 tools whose expansion inputs changed. Regenerated catalog snapshot, fleet provenance and `server.json`.

## [0.13.16] - 2026-11-21

### Fixed

- **A followed hint no longer names a tool the client cannot call** (#354). `_meta.next_commands` steps whose target is a fleet tool that is not pinned (listed) for the request are rewritten to `{"tool": "call_tool", "arguments": {"name": "<ns>_<tool>", "arguments": {...}}}`, so a model that follows the step never makes the direct call a listed-tools-only client rejects with "Tool not found". Pinned targets and the meta-tools stay direct; a bare `search_tools`/`call_tool` reference is no longer namespace-prefixed.
- **Prose calls with an argument list** (`get_gene_score_sets(symbol)`) become `call_tool(name="mavedb_get_gene_score_sets", arguments={"symbol": ...})`, keeping any value the backend gave, instead of the uncallable `call_tool(name=...)(symbol)`.
- **The unknown-tool error names the ready call form**: `call_tool(name="<namespace>_<tool>", arguments={...})` from the `search_tools` hit's `call` field. A client that sends an unlisted full name to the router is routed to the backend (now covered by an integration test); the rejection by a listed-tools-only client happens before the router and cannot be changed here.

## [0.13.15] - 2026-11-21

### Fixed

- **OAuth RFC 9207 `iss` mismatch broke fresh claude.ai sign-ins** ("Claude could not confirm the identity of the GeneFoundry sign-in service"). FastMCP appended `iss=str(issuer_url)`, which a plain `AnyHttpUrl` renders as `https://genefoundry.org/`, while the authorization-server metadata publishes the slashless canonical `https://genefoundry.org`. The proxy now passes a `SlashlessIssuerUrl`, so every redirect carries the metadata issuer exactly. Existing grants and the bounded legacy slash-form token issuer are unchanged.

## [0.13.14] - 2026-11-20

### Added

- **Cancer/somatic backends enabled** (search-only, no pinned entry points: pinned listing budget): `civic` (civic-link 0.1.1), `cbioportal` (0.2.0), `opentargets` (0.1.1), `moalmanac` (0.2.0), deployed at `<name>-link.genefoundry.org`. Fleet 30 backends, 345 tools. Closes #347, #348, #349, #350.

### Changed

- **Drift baseline re-pinned to `fleet-2026-11-20b`** from the deployed fleet: clinvar-link 0.10.0 (somatic classifications, `search_somatic_classifications`), vep-link 1.2.3, mondo-link 0.11.2, hpo-link 0.12.0, ucsc-link 0.6.0, plus the four new backends. Router v0.13.13 must not be deployed: its baseline pins the earlier definitions and `GF_DRIFT_MODE=enforce` refuses to boot against this fleet.
- Held-out discoverability: pharmacogenomics bar 6.0 → 5.0 (owner-approved; the new tools shift BM25 word weights, one German clinpgx query moves #1 → #2). Overall held-out score 9.15.
- Strict-mode pinned listing ceiling 18,700 → 18,800 tokens (owner-approved): `clinvar_get_variant` grew ~480 chars with the somatic pointers. The default listing stays within 18,000.
- BM25 expansions generated for the 36 new or changed tools; four discoverability tasks for the cancer backends.
- Regenerated catalog snapshot, fleet provenance, README inventory, `server.json`; `ci/fleet-urls.env` and the read-only drift probe list the four new URLs.

## [0.13.13] - 2026-11-20

### Added

- **Cancer/somatic backends staged** (`enabled: false`; plan `docs/plans/2026-11-20-cancer-somatic-expansion.md`, #347–#350): `civic` (CIViC GraphQL, CC0), `cbioportal` (api.cbioportal.org + Cancer Hotspots snapshot, ODbL), `opentargets` (Open Targets Platform 26.09, CC0, datasource allowlist) and `moalmanac` (MOAlmanac 2.0 snapshot v.2026-11-05, CC BY 4.0), each released v0.1.0. Registry entries with entry points (`search_molecular_profiles`, `get_gene_alteration_frequency`, `resolve_target`, `search_approvals`), env vars, control-ledger rows, control-audit repo list, untrusted-text inventory rows and fleet metadata. Nothing changes in the served catalog until an entry is enabled after deploy and the baseline is re-pinned.
- **Research skill `skills/somatic-variant-interpretation/`**: chains ClinVar somatic, CIViC, MOAlmanac, cBioPortal, hotspots, Open Targets and the germline fleet; keeps oncogenicity, actionability and germline classification apart.

### Changed

- Search vocabulary: `manual_keywords` for somatic intents on `clinvar_get_variant` (somatic, oncogenicity, AMP/ASCO/CAP tier) and OncoTree on `mondo_map_cross_ontology` / `mondo_resolve_xref`.
- Untrusted-text inventory: vep fences `clinvar_somatic_classification` (vep-link 1.2.3); clinvar fences somatic tumour types and therapies (clinvar-link 0.9.0).
- `check_fleet_metadata.py` accepts metadata for staged backends; provenance counts enabled backends only.

## [0.13.12] - 2026-11-20

### Added

- **Foreign-identifier conformance check** (`docs/conformance/identifiers.py`, `identifiers.json`, `test_identifiers_v1.py`; refs #343): for every gene parameter of a backend, sends one gene as symbol, Ensembl, HGNC and NCBI Gene id and requires each answer to resolve to that gene or be an `invalid_input` with `field`, `allowed_values` and a non-looping `next_commands`. The first form answered with a non-empty success is the positive control, and other forms are compared against its rows. Silent empties, wrong entities, absence claims, `internal`, self-loops and rejections whose only next step is a meta tool fail; a parameter with no working control is UNGATED and fails unless declared out with a reason. Gene parameters are derived from the tool schema; `identifiers.json` seeds per-backend corrections and a strict expected-fail list (a listed key that passes is an error), and a backend's own `identifiers.local.json` replaces its seed. Runs like the behaviour gate (`CONFORMANCE_MCP_URL`, `CONFORMANCE_NAME`); `make identifiers MCP_URL=... NAME=...`.
- **Batch-pointer lint** (`scripts/check_batch_pointers.py`, `make lint-batch-pointers`, in `ci-local`): a pinned tool must name its batch or set-level sibling in its description (Tool-Schema Documentation Standard rule 8, `D2`). Runs offline on the packaged baseline; known failures are listed as expected-fail (11 when introduced).
- Response-Envelope Standard §2b: an unaccepted identifier form is `invalid_input` with the converter in `next_commands`, never an absence; a bare number states the namespace it was read in; batch results are per item.

### Changed

- **`hpo_score_genes_for_phenotypes` and `hgnc_resolve_symbols_batch` are pinned in the default listing** (#341). Unpinned, the set-based scorer was called in 0 of 75 gene-ranking samples: models looped `hpo_get_genes_for_phenotype` / `hpo_get_phenotypes_for_gene` per term and rarely searched. The batch resolver, with only a pointer in `hgnc_resolve_symbol`'s description, got 8 calls against 928 single resolves over 75 samples (14 out of turns); listed, 75 batch and 2 single calls, 2.1 tool calls per sample instead of 13.8, none out of turns. 31 → 33 pinned tools; `minimal` and `none` modes keep their tool sets.
- **Drift baseline re-pinned to `fleet-2026-11-20`** (26 backends, 313 tools): strict live capture of nine re-released backends — hgnc-link 2.5.0, hpo-link 0.11.0, clingen-link 4.2.0, orphanet-link 0.7.0, uniprot-link 5.5.0, gnomad-link 9.3.1, litvar-link 6.3.0, stringdb-link 4.5.0, genereviews-link 5.4.0 — each checked against its signed release's definitions digest; the other 17 are unchanged. The changes are descriptions and parameter schemas (accepted identifier forms, batch pointers); one new tool, `hpo_resolve_terms_batch`; gnomad `search_structural_variants` drops `response_mode`. Catalog snapshot, 39 stale search expansions, provenance and the README inventory regenerated.
- **Listing sizes on the new schemas** (33 pins): default 71,239 chars (17,809 tokens, ceiling 18,000); slim 27,502 chars (6,875 tokens, ceiling 6,900, raised from 6,600 for the two pins); strict 74,679 chars (18,669 tokens). The opt-in strict listing gets its own CI ceiling of 18,700 (owner decision 2026-11-20): the backend releases added about 3,000 chars of description to pinned tools. Slim hides `offset` on `hpo_search_terms` and `hpo_get_phenotypes_for_gene` and shows `hpo_ids` + `candidate_genes` on the scorer (`slim_keep`); calls still accept every parameter.
- Search vocabulary for the scorer: `manual_keywords` ("causal gene", "causative gene", "prioritise genes", …) so "causal gene from phenotypes" returns it first (was outside the top 5); three regression queries added to `tests/discoverability/bench_regressions.yaml`.
- Batch-pointer lint: six pinned tools now name their batch or set-level sibling upstream (genereviews, hgnc, hpo ×2, stringdb, uniprot) and leave the expected-fail list; 5 remain.

## [0.13.11] - 2026-11-19

### Added

- **`X-GF-Schema-Mode: slim`** (and `GF_SCHEMA_MODE=slim`; default stays `standard`): a token-lean `tools/list` for the pinned tools, independent of `X-GF-Pinned-Mode`. A slim tool lists its required params plus at most 2 optional ones (a per-tool `slim_keep` allowlist in `servers.yaml`, else the first two non-`response_mode` optionals), 80-char param descriptions without `examples`/`title`/`default`, no `additionalProperties: false`, the description's first sentence and no `outputSchema`; the listing is name-ordered and byte-stable. `search_tools`/`call_tool` and search hits keep full schemas, and calls validate against the backend's full schema. Default pins: ~62.4k → ~24.7k chars; minimal pins: ~12.4k → ~7.6k chars. CI budget: default + slim ≤ 6.6k tokens (chars/4).
- Slim-mode observability for the bench: a call that used a hidden param gets `_meta.unadvertised_params_used: [names]` (router-owned), an `unadvertised_params_used` log event (names only, never values) and `genefoundry_unadvertised_params_used_total{tool}`; a failing slim call missing a required param or using a hidden one logs `slim_hidden_param_validation_error`.

## [0.13.10] - 2026-11-19

### Fixed

- **`tools/list` serves standard schemas again; OpenAI strict mode is opt-in.** Since v0.12.0 (#293) every listed `inputSchema` was projected to OpenAI strict mode for all clients: every optional parameter required + nullable, and `call_tool.arguments` a required JSON string. Clients that validate a model's arguments against the listed schema (Inspect AI, the python `mcp` client, most frameworks) rejected ordinary calls (`call_tool` with an object `arguments`, `'offset' is a required property`), and the bench tool-error rate rose from 8–15 % to 35–61 %. The default listing is now the backends' own schemas, with `call_tool` requiring only `name` and `arguments` an optional object, as MCP specifies (`inputSchema` is plain JSON Schema).
- Compact `search_tools` hits' `call` example passes `arguments` as an object in standard mode.

### Added

- **`GF_SCHEMA_MODE=standard|strict`** (default `standard`) and the per-request **`X-GF-Schema-Mode: strict`** header, mirroring `X-GF-Pinned-Mode`. Strict mode keeps the v0.13.9 listing exactly, including `call_tool.arguments` as a JSON string. `call_tool` accepts an object or a JSON-encoded string in both modes, strict `null` sentinels are still stripped, and `search_tools(detail="strict")` is unchanged.

## [0.13.9] - 2026-11-19

### Added

- **Four new backends federated (26 total, 312 tools):** `strchive` (STRchive tandem-repeat disease loci, v0.3.0), `ucsc` (UCSC Genome Browser tracks, sequence, BLAT & in-silico PCR, v0.5.1), `mitomap` (MITOMAP mtDNA variants, v0.3.0) and `pgscatalog` (PGS Catalog polygenic scores, v0.3.3). All four are **search-only** — no entrypoints, so the pinned listing budget does not grow; they are reached through `search_tools`. Consolidates draft PRs #281, #282, #283 and #289.

### Changed

- **Drift baseline re-pinned to `fleet-2026-11-19b`** — strict live capture of the 26 verified, immutable backend releases. The 22 existing backends' definitions are byte-identical to the `fleet-2026-11-19` pin; only the four new backends are added. Discovery catalog recaptured and BM25 expansions regenerated for the new tools; proper-name keywords lost to the 40-keyword carry-over cap (e.g. `STRINGdb`, `PubTator3`, `CADD score`, `BioC JSON`, `MeSH headings`) restored as `manual_keywords`.
- **No-expansion golden discoverability bar 8.9 → 8.7:** the four new search-only backend tasks were added (no-expansion ranks 1/1/2/4); the old 54 tasks are unchanged at 8.90, and the with-expansion bar of 9.0 is still enforced.

## [0.13.8] - 2026-11-19

### Fixed

- **ChatGPT apps created before v0.10.4 can connect again.** `/authorize` rewrites exactly the legacy doubled resource (`…/mcp/mcp`, advertised by the PRM up to v0.10.3) to the canonical resource before FastMCP's RFC 8707 check, so the canonical value goes upstream and the token audience is unchanged; every other resource is still `invalid_target`. Each rewrite logs `oauth_resource_alias` and increments `genefoundry_oauth_resource_alias_total`.
- **Per-account rate-limit overrides keyed on a client id now apply.** FastMCP 4's `MultiAuth` rewrites `AccessToken.client_id` into a source-qualified JSON tuple, so `GF_RATE_LIMIT_OVERRIDES={"gfbench": 600}` never matched and service accounts got the default quota; the limiter now matches the verified `original_client_id`.

## [0.13.7] - 2026-11-19

### Changed

- Pin `gnomad_get_variant_frequencies` again; clinpgx is no longer pinned and is reached via `search_tools` (pinned-listing budget, until clinpgx-link#56 slims `search_records`).
- Discoverability bars adjusted for the un-pinned clinpgx: golden no-expansion 9.0 → 8.9, held-out pharmacogenomics minimum 7.0 → 6.0.

### Added

- **Deploy Contract Standard v1 (`docs/DEPLOY-CONTRACT-STANDARD-v1.md`).** One deployed
  Compose file per repository, a literal project `name:`, the fleet-wide `GF_IMAGE` variable,
  explicitly named volumes, and registry subnets for shared networks — the fixes for the
  2026-11-19 thin-deploy failures. `validate-deployed-overlay` gains the rules
  `single-deployed-file`, `project-name`, `volume-name` and `internal-network`;
  `application-image` now requires `${GF_IMAGE:-${<OLD>_IMAGE:?…}}` (or `${GF_IMAGE:?…}`),
  and `service.deployed_compose_files` must be declared explicitly. A repository gets the new
  rules when it bumps its shared-workflow revision.

### Changed

- **The router deploys one standalone file.** `docker/docker-compose.npm.yml` is now the whole
  production stack (no longer layered on the base and prod files), reads `GF_IMAGE` with
  `GENEFOUNDRY_IMAGE` as a one-release fallback, and pins its OAuth state volume to the
  existing name `genefoundry-router_fastmcp_data`, so no state moves.

## [0.13.6] - 2026-11-19

### Changed

- Re-pin the drift baseline to the live fleet `fleet-2026-11-19`.
- Un-pin `gnomad_get_variant_frequencies` (still reached via `search_tools`) to keep the pinned listing within its 18k-token budget after the re-pin; clinpgx's `search_records` grew to ~1.46k tokens.

## [0.13.5] - 2026-11-19

### Changed

- **`X-GF-Pinned-Mode` also selects the instructions and the hint call form (#320).** The
  header filtered `tools/list` only, so a `none`-mode client was still told about pinned tools
  it could not see, and got bare pinned names in rewritten hints. `initialize` and
  `server/discover` now return that mode's instructions, hint rewriting uses that mode's pinned
  set (bare name vs `call_tool(name=…)`), and the tool-response cache keys on an overriding
  header. Auth, routing and rate limits still never read it.

### Fixed

- **A backend that answers 403/5xx now reads as unavailable, not as an unknown tool (#325).**
  The MCP SDK drops the HTTP status from a non-2xx answer; the router now classifies the
  SDK's stand-in as `upstream_unavailable` / `error_subcode: backend_http_error` and logs
  `backend_http_error` with `namespace` and `status` from a per-backend response hook, so a
  proxy ACL change (the 46 h 403 incident) can be alerted on.
- **Audit `error_code` for in-band error results (#325).** The router's own `not_found` /
  `upstream_unavailable` envelopes are returned as `isError` results and were logged without a
  code; `tool_call` now carries it.
- **`mcp_unknown_tool` carries `requested_sha256` (#325):** a 16-hex SHA-256 prefix of the
  requested name, so repeats can be counted and a suspected name confirmed without the raw
  caller text reaching the log.
- **Startup harvest retries for ~60 s before logging ERROR (#325).** Backends booting alongside
  the router were logged as `backend_unreachable` / `backend_list_failed` ERRORs; the startup
  harvest now logs those at WARNING and re-harvests at 5/10/15/30 s; only a backend still down
  after the last retry is an ERROR.

## [0.13.4] - 2026-11-19

### Added

- **Pinned mode (#320).** `GF_PINNED_MODE=default|minimal|none` (default unchanged) picks
  which pinned tools `tools/list` shows; `minimal` lists `GF_PINNED_MINIMAL` (6 front doors),
  `none` only `search_tools` + `call_tool`. The request header `X-GF-Pinned-Mode` overrides
  it per request (allowed in CORS); it filters the listing only. Instructions follow the mode.
  Strict listing: ~17.1k / ~3.2k / ~0.8k tokens.

### Changed

- **Search-first pins (#320).** Resolvers were the top production error class. Pins now:
  `hpo_search_terms` (was `hpo_resolve_term`), `orphanet_search_diseases` (was
  `orphanet_resolve_disease`), `mondo_search_diseases` added before `mondo_resolve_disease`,
  `gencc_get_gene_curations` (was `gencc_resolve_identifier`); dropped `clinpgx_get_record`,
  `clinpgx_get_related_records`, `clinpgx_search_dataset` (each needs an id from another
  tool). 32 → 30 pins; listing ~18.1k → ~17.1k tokens; offline discoverability unchanged.

## [0.13.3] - 2026-11-19

Deploy candidate. Summarises everything since v0.13.2, including three changes that shipped
in the v0.13.2 tag without a changelog line (#312, #314, the Dependabot pins).

### Fixed

- **Low-confidence fallback serves up to `GF_SEARCH_MAX_RESULTS` hits (was 2).** When the
  rerank floor drops every candidate, the BM25 top `GF_SEARCH_MAX_RESULTS` (or the caller's
  `limit`) come back flagged `low_confidence`. genefoundry-bench found the right tool at BM25
  rank 4 for "get full text of PMC article", which the 2-hit fallback hid.
- **Recommended Jev 1.13 floor is now `GF_SEARCH_RERANK_MIN_SCORE=0.25` (was 0.20).**
  Re-measured on the current candidate text: the highest out-of-scope score is now 0.21, so
  0.20 rejects 64/65 negatives (the miss: "drug-drug interactions"); 0.25 rejects 65/65 and
  empties 6.8% of agent queries, each of which still gets the low-confidence fallback. The
  code default stays `0.0`; re-measure when the model or the candidate text changes.
- **Expansion regeneration no longer deletes vocabulary.** The D1 rollout changed 126 tool
  descriptions, so `make expansions` regenerated those entries from scratch; generation is
  stochastic, and German queries fell from 45 to 30, which pushed e.g. a German
  variant-frequency question out of the reranker's 20-tool BM25 pool. Now:
  - `gen_tool_expansions.py` carries a stale entry's previous **keywords** over after the new
    ones (case-insensitive dedupe, capped at 40); `--fresh` resets. Queries are not carried:
    doubling them dilutes BM25 length normalisation (lower held-out MRR, and a bench
    regression fell out of the top 5).
  - One-time offline repair: the same keyword carry-over applied to the 126 entries using the
    pre-regeneration file (source hashes unchanged, so the entries stay fresh).
  - Prompt v2 asks for one German **and** one French query (was "German or French").
  - 52 core tools get hand-curated lay + German/French/Spanish terms in `manual_keywords`.
  - Held-out pure search: hit@5 0.933 → 0.944, MRR 0.832 → 0.858, crosslingual 0.64 → 0.73.
- Discovery catalog: re-captured clinpgx-link v0.1.17 (search_records lead says gene–drug,
  not drug–drug) and metadome-link v0.5.2 (resolve_transcript lead names the GRCh37/38
  tolerance-landscape transcript); their 2 expansion entries are regenerated (#318).
- Tests: `bench_regressions.yaml` is checked in pure search (a pin never enters the BM25
  pool, so pins no longer satisfy a regression); adds German and Spanish variant-frequency
  tasks and an out-of-scope drug–drug interaction negative in the held-out set.
- **Hand-curated `manual_keywords` overlay survives expansion regeneration (#316).**
  Regenerating `pubtator_get_publication_passages` had dropped the v0.12.5 full-text terms
  (article-full-text fell to rank 9). The generator carries the overlay over and the loader
  puts it ahead of the generated keywords, so the 40-item cap never drops it.
- CI: `check_action_pin_versions` resolves each pinned tag with one commits-API call; a
  throttled annotated-tag lookup had flagged the correct codeql-action v4.39.3 pin as a lie
  (#316).

### Added

- Discovery catalog and fleet manifest re-captured from every backend's current release tag
  (22 backends run locally, no fallbacks), with snapshot-capture fixes (#315).
- `docs/RESPONSE-ENVELOPE-STANDARD-v1.md` §2a: a `resolve_*` miss is an honest `not_found`
  with scored candidates and `next_commands`; `docs/conformance/behaviour.py` gains a
  WARN-level probe for it (#314, #301).
- Tests: CI fails on a catalog tool without a packaged expansion; second STRING
  pair-question regression task (#312, #292, #297).

### Changed

- CI: Dependabot action pins — setup-python 7.1.0, setup-uv 10.3.3, docker/login-action
  4.8.0, codeql-action 4.39.3 (d4f898a, #287).

### Documentation

- **Response-Envelope §2b: accept the identifier the user has** (#319). Entry-point tools accept
  every identifier form they can already convert, echo `resolved_from: {input, form,
  resolved_to}`, return candidates instead of guessing on ambiguity, and keep `invalid_input` +
  the converter in `next_commands` where conversion needs another backend. DoD item added.

## [0.13.2] - 2026-11-19

### Fixed

- **Unexpected arguments return a structured `invalid_input` envelope (#158).** FastMCP
  answered an unknown argument with a raw pydantic dump (no `error_code`, caller input
  reflected). The new `ArgumentGuard` middleware rejects keys outside a closed
  `inputSchema` (`additionalProperties: false`) for `search_tools`, `call_tool`, the
  `call_tool` target and every federated tool. The envelope lists only the accepted
  parameters in `allowed_values`. Re-verified on current backend releases: no backend's
  diagnostics tool advertises `response_mode`, and all reject unknown keys with
  `invalid_input`.
- Tests: the structlog isolation fixture moves to `tests/conftest.py`, so `capture_logs`
  works in integration tests regardless of collection order.

## [0.13.1] - 2026-11-19

### Added

- **Tool-Schema Documentation Standard v1.1, rule `D1` (description lead):** a tool
  description's first sentence must say what the tool returns, before usage advice.
  `scripts/surface.py` gains `lead_states_return` (vendored to backends), and
  `scripts/check_tool_surface.py` reports `D1` as a warning (`--strict-lead` fails on it;
  `--rule` lists every finding). `make lint-description-lead` reports it over the fleet
  baseline in `ci-local`: 96 of 285 tools fail today. The standard has fleet examples,
  sources, and the per-backend rollout and measurement steps.

### Changed

- `docs/discovery.md`, `docs/configuration.md`: the rerank-floor note now uses the v0.12.8
  bench re-measurement (64 out-of-scope negatives). The recommended floor for Jev 1.13 is
  `0.20` (0.20–0.25 defensible), not 0.3. The code default is unchanged.

## [0.13.0] - 2026-11-19

### Added

- **Per-account rate limit.** With auth on, `GF_RATE_LIMIT_RPM` now limits each verified
  account instead of each client IP. The bucket is the verified token's `sub`, or its client
  id when it has no `sub`. A new middleware inside FastMCP's authentication layer reads only
  the `AccessToken` the verifier accepted, so a header can never choose the account. One
  account shares one bucket across all its IPs.
- `GF_RATE_LIMIT_OVERRIDES`: a JSON object mapping a token subject or client id to a positive
  requests/min value or `"unlimited"`, e.g. `'{"gfbench": 600, "<owner-sub>": "unlimited"}'`.
  It is validated at startup. Startup rejects an override keyed on `GF_OAUTH_CLIENT_ID`,
  because every interactive OAuth user shares that client id.
- `GF_RATE_LIMIT_IP_RPM`: the pre-auth per-IP DoS floor. When unset it equals
  `GF_RATE_LIMIT_RPM`, so an upgrade changes nothing. An authenticated production router
  refuses an explicit `<=0`.
- Responses carry `RateLimit-Policy` and `RateLimit` headers (IETF
  draft-ietf-httpapi-ratelimit-headers-11), one `"ip"` line and one `"account"` line. No
  partition key is sent.

### Changed

- A 429's `Retry-After` and `error.data.retry_after` now give the seconds until the window
  resets. They used to give the full 60 s window. Logs record a 429 by `policy` and `limit`
  only; account and IP are never logged.

## [0.12.8] - 2026-11-19

### Fixed

- **Hint rewriting reaches every prose field:** bare sibling tool names were rewritten only
  in `message`/`hint`, so gnomad's `recovery` and `build_note` still named tools the router
  does not know. A key is now prose when it is, or ends in `_`, one of `message`, `hint(s)`,
  `note(s)`, `recovery`, `guidance`, `suggestion`, `warning(s)`, `caveat`, `help`; string
  values and lists of strings under it are rewritten. Identifiers, enums (`recovery_action`,
  `reason`) and backend data records are never regex-rewritten.
- **`call` examples match the advertised schema:** the strict listing advertises
  `call_tool.arguments` as a JSON string, so an unpinned hit's example now reads
  `{"tool": "call_tool", "arguments": {"name": "<ns>_<tool>", "arguments": "{}"}}`, and the
  integration test calls it verbatim.

### Changed

- **Discovery snapshot keeps parameter enum values** (`scripts/snapshot_catalog.py`,
  `load_catalog()`), so the offline bench gives the reranker the same parameter summary as
  production. `catalog.json` was backfilled offline from `tests/fixtures/fleet_manifest.json`
  (same 2026-10-04 capture; all 285 tools matched).
- Held-out discovery set: 40 more out-of-scope tasks (24 → 64).
- `docs/discovery.md`: the rerank floor is model-specific; re-measure on both eval sets
  before changing it.

## [0.12.7] - 2026-11-19

### Changed

- **Reranker candidate text (#306):** the System One reranker read only a tool's name and
  the first 300 description characters, so a correct tool whose lead does not say what it
  returns scored under the floor although BM25 ranked it near the top. Each candidate now
  adds a compact parameter summary (names, enum values) and the tool's expansion keywords,
  each part capped and the whole capped at 800 characters; catalog-only and deterministic.
  The floor is unchanged.

### Added

- `tests/discoverability/agent_queries.yaml`: the 858 genefoundry-bench agent search
  queries (synthetic, unlabeled) as an offline evaluation set;
  `scripts/search_rerank_report.py --tasks` on it reports the share emptied versus
  production. Not a CI gate or tuning target.

## [0.12.6] - 2026-11-19

### Added

- **Search analytics (#301):** every successful `search_tools` audit record carries
  `zero_results` (the zero-result rate is the discovery-gap metric to watch) next to
  `hit_count` and `low_confidence`. An `isError` result of any tool now logs
  `error_type: "tool_error"` (OTel `error.type`); a failed search has no hit fields. New
  counter `genefoundry_search_tools_results_total{result=hits|zero|low_confidence|error}`.
  Query text is still never logged. See `docs/discovery.md`.

## [0.12.5] - 2026-11-19

### Fixed

- **Reranker floor emptied in-scope searches (#303):** with `GF_SEARCH_RERANK_MIN_SCORE`
  set, a query whose every candidate scored under the floor returned a bare `[]` (24% of
  searches in genefoundry-bench agent traces, e.g. full-text / figure / table requests the
  passages tool answers). Now the top 2 BM25 candidates are served with
  `"low_confidence": true` and their `relevance`; `[]` only means BM25 found no candidate.
  The reranker returns every scored candidate and the search stage applies the floor. The
  `search_tools` audit record gains `low_confidence`, and `scripts/search_rerank_report.py`
  counts a low-confidence result as an abstention.
- Server instructions and the `search_tools` description no longer call search "BM25" and
  tell the model what a `low_confidence` or empty result means.
- `pubtator_get_publication_passages` expansion keywords gain general full-text vocabulary
  (PubMed Central, figure captions/legends, tables, supplementary material). Hand-edited:
  the generator needs an OpenRouter key that was not available. Three general phrasings
  joined the discovery regression set.

## [0.12.4] - 2026-11-19

### Added

- **Search outcome in the audit log (#301, part b):** a `search_tools` audit record now
  carries `hit_count` and `top_hits` (the first three hit names, each confirmed against the
  catalog and bucketed to `_unknown` otherwise), so "did search find the right tool" is
  answerable from production logs. The query text is never logged.

## [0.12.3] - 2026-11-19

### Fixed

- **Unlisted tools called by name failed client-side (#299):** clients that validate calls
  against `tools/list` reject a tool name the router does not list. The server instructions
  no longer claim a namespaced name is "directly callable": unpinned tools are invoked only
  through `call_tool`; pinned entry points stay directly callable.
- Each compact `search_tools` hit now carries a ready `call` example —
  `{"tool": "call_tool", "arguments": {"name": "<ns>_<tool>", "arguments": {}}}`, or the
  pinned name directly.
- Backend prose hints (`message`, `hint`) that name a bare sibling tool ("Try search_genes.")
  are rewritten to `call_tool(name="<ns>_search_genes")`, or to the bare namespaced name when
  that tool is pinned. Structured tool-reference fields keep the namespaced name.

## [0.12.2] - 2026-11-18

### Fixed

- **Regression from 0.12.0 (do not deploy 0.12.0/0.12.1):** the strict `tools/list` projection
  closed every open map (an object with arbitrary keys) into an empty object, so a strict-mode
  client could not pass `call_tool.arguments` (i.e. could not call any unpinned tool), nor
  `clinpgx_search_records.filters`, `clinpgx_search_dataset.filters`,
  `vep_annotate_variant.vep_options`. Open maps are now advertised as a JSON-encoded string;
  `StrictNullMiddleware` decodes them back into objects against each tool's original schema, so
  backends and the meta `call_tool` receive real objects. Found by the genefoundry-bench audit
  (#297). Tests prove a strict client can call an unpinned tool through `call_tool` and pass a
  pinned open map, and that no listed schema contains an unusable closed empty object.

## [0.12.1] - 2026-11-18

### Fixed

- 429 (rate limit) and 413 (body cap) rejections now carry a JSON-RPC error body
  (`{"jsonrpc":"2.0","id":null,"error":{"code":-32000,"message":...}}`; the 429 adds
  `data.retry_after` alongside the `Retry-After` header). MCP clients parse a non-2xx body as
  JSON-RPC; the old `{"error": ...}` body surfaced as the opaque "Server returned an error
  response", which hid the cause of intermittent `initialize` failures (#294). The rate limit is
  keyed per client IP, so parallel clients behind one address share one budget.

## [0.12.0] - 2026-11-18

### Changed

- `tools/list` now advertises every listed tool (pinned entry points, `search_tools`,
  `call_tool`) with an OpenAI-strict input schema: every property required, optional ones
  nullable, `additionalProperties: false` (#293). OpenAI with `strict: true` rejected the
  whole request before ("Missing 'dataset'"). Only the listing changes: a new
  `StrictNullMiddleware` strips the `null` sentinels strict clients send against each tool's
  ORIGINAL schema, so backends and the synthetic tools receive what a non-strict client sends;
  properties the backend itself declares nullable keep their `null`.
- Server instructions ask for ONE intent per `search_tools` call: a multi-part question
  (a disease's genes and their chromosome location) is one search per part (#292).

### Added

- `tests/integration/test_strict_pinned_catalog.py`: the real pinned set (servers.yaml over the
  fleet baseline) must satisfy the strict invariants and stay under a listing budget
  (~18.1k est. tokens today; ceiling 19k — lower it as pinned schemas are trimmed).
- `tests/discoverability/bench_regressions.yaml`: failure classes from genefoundry-bench as a
  recall@5 regression list, phrased generally (not the held-out benchmark wording).

### Not in this release

- Gemini "too many states" (#293) is driven by long enums in backend schemas (ClinPGx);
  trimming them belongs in the backend and is tracked there. The token budget now blocks growth.

## [0.11.1] - 2026-11-18

### Security

- MCP SDK 2 echoes an unknown JSON-RPC method name verbatim in the `METHOD_NOT_FOUND` error
  `data` (legacy and 2026-07-28 eras). An outermost low-level middleware now strips it; the
  SDK 2 runner's "dropped %r" / "no handler for notification" log lines are scrubbed and
  `mcp.server.runner` joins the scrubbed source loggers. Regression test drives a hostile method
  over the real app in both eras.

## [0.11.0] - 2026-11-18

### Changed

- Upgrade FastMCP 3.4.11 → 4.2.2 and the MCP Python SDK 1.30 → 2.3 (prerequisite for serving
  Skills over MCP, SEP-2640). The router now also speaks the sessionless 2026-07-28 protocol
  era (`server/discover`, `mcp-method`/`mcp-name` headers) alongside the legacy handshake;
  backend proxy connections stay on the handshake era, so 3.x and 4.x backends both work.
- The proxy HTTP pool and transport-error classification moved to `httpx2` (FastMCP 4's client
  stack); `httpx2` is now a direct dependency. The `beartype<0.23` constraint is dropped.

### Security

- No-token-passthrough: FastMCP 4 moved `forward_incoming_headers` from the transport to the
  client and defaults it to `True`; the old attribute-probe became a silent no-op. Forwarding is
  now disabled at `ProxyClient(..., forward_incoming_headers=False)` construction.
- Not-found reflection guard: Layer 3 ported to SDK 2's `get/add_request_handler`; Layer 1 now
  also preflights the `call_tool` meta-tool's target, which FastMCP 4 rejects (echoing the name)
  before re-entering middleware. The resource/prompt backstop now raises
  `MCPError(INVALID_PARAMS)` with its fixed message (SDK 2 would send a plain exception as code 0).
- OAuth log redaction: markers re-pinned to FastMCP 4.2.2 (`Upstream OAuth token refresh
  failed`) plus five new 4.x OAuth log lines (refresh claim extraction, cookie decode, ID-JAG).
- CORS allows the modern-era `mcp-method` and `mcp-name` request headers.

### Fixed

- Backend timeouts are classified again: SDK 2 signals them as `REQUEST_TIMEOUT` (-32001), not
  HTTP 408, and FastMCP raises `httpx2` (not `httpx`) transport errors.
- OAuth refresh observability classifies FastMCP 4's raw upstream `OAuthError` as
  `upstream_other` instead of `internal_error`.

### Deploy notes

- Rolling order is free (router proxies use the handshake era). OAuthProxy 4.x adds RFC 9207
  `iss` and asks for consent once more per client with `require_authorization_consent="remember"`.

## [0.10.17] - 2026-11-13

### Fixed

- Container-release re-runs of a fully published release no longer stay red (mitomap-link v0.1.2,
  run 43409703573):
  - `finalize` retries (bounded, 6 tries with linear backoff) only GHCR's read-after-write
    `not found` for the just-copied version tag; any other registry error and any digest mismatch
    still fail at once. Attempt 1 failed here although the copy had succeeded.
  - Cross-job evidence artifacts are addressed through the producing job's `artifact_name` output
    instead of the consumer's own `run_attempt`, so "Re-run failed jobs" finds the sealed evidence
    the succeeded jobs produced rather than looking for `release-final-<run>-2`.
  - The full-re-run recovery probe in `prepare` verified attestations with `--repo` only, which
    demands a signer workflow in the caller repo; every attestation is signed by this reusable
    workflow, so the probe failed with `missing attestation` for every fleet image. It now verifies
    provenance and SPDX with the same identity `publish-attest` proves (signer workflow + digest,
    tag ref, source commit, hosted runners) and no longer hides the verifier's error.

## [0.10.16] - 2026-11-13

### Fixed

- `validate-config` (and every command that parses `container-release.json`) now applies the exact
  predicate `assemble-manifest` applies to the sealed data requirements, so a config the evidence
  assembler would reject fails before the image is built or pushed. strchive-link v0.1.0 declared
  `upstream-live` without `release_tag`/`digest`: the config gate passed, then assembly rejected it
  after the image was published, burning the version. Every non-`none` data mode now needs
  `release_tag` + `digest`, and `upstream-live` needs an explicit `reproducible_rollback: false`.
- The reusable release workflow's `prepare` job runs `validate-config` unconditionally (the
  deployed-overlay gate, the only earlier config parse, can be waived), and its data-requirements
  projection treats an omitted `data` block as `none`, matching the config model's default.
- A table-driven test runs the workflow's own jq projection and the real assembler for every data
  mode, contract and adoption, and proves `validate-config` accepts exactly what assembly accepts.
  All fleet `container-release.json` files still validate.

## [0.10.15] - 2026-11-13

### Added

- `search_tools` takes an optional `limit` (1 to max(`GF_SEARCH_MAX_RESULTS`,
  `GF_SEARCH_RERANK_POOL`), so 1–20 by default; default unchanged at `GF_SEARCH_MAX_RESULTS`=5).
  Each compact hit is ~1–3 kB, mostly the `inputSchema` needed to call it, so the hit count is the
  caller's lever on discovery cost (a 5-hit search is 6–11 kB; `limit=1` 1–3 kB). Out-of-range
  values are rejected with an error naming `limit` and its bound. `limit=k` returns the exact top-k
  of the default ranking. The instructions mention it; a payload-budget test guards the default.

## [0.10.14] - 2026-11-13

### Fixed

- The shared container-release recovery path (re-run after the `sha-<commit>` alias was already
  pushed) can pass again: `inspect-oci` accepted only an index descriptor carrying `platform`, which
  `oras cp --to-oci-layout` never writes. An absent platform is now decided by the digest-verified
  image config (exactly linux/amd64, no `variant`); a declared platform must still be exact. A failed
  inspection now prints its report instead of surfacing as a later Trivy failure (#277).

## [0.10.13] - 2026-11-12

### Fixed

- A backend call that exceeds `GF_BACKEND_TIMEOUT` (or cannot connect) is now a fleet envelope,
  `error_code: upstream_unavailable` with `error_subcode: backend_timeout` / `backend_unreachable`,
  `retryable: true` and a smaller-batch hint, in both the wire result and the audit log; it was a bare
  `ToolError` logged as `unclassified` (#272).
- `POST /register` with an empty or non-JSON body answers 400 `invalid_client_metadata` (RFC 7591
  §3.2.2) instead of an ASGI 500 with a traceback; the mcp<2 handler only caught `ValidationError` (#271).
- A backend that fails its `tools/list` after startup is now recorded as down (`/health`,
  `backend_list_failed` log) and `call_tool` answers `upstream_unavailable` instead of `not_found`;
  previously its tools silently vanished from `search_tools` until restart (#266).

## [0.10.12] - 2026-11-11

### Fixed

- Re-release of 0.10.11: that tag's GitHub release was created by hand before the Container release
  workflow ran, so it was immutable and empty and the workflow failed ("missing attestation"). 0.10.11
  was never published as a container image; 0.10.12 carries the identical dependency and security
  sweep (see 0.10.11 below) and is published by the workflow.

## [0.10.11] - 2026-11-11

### Fixed

- Dependency and security sweep: fastmcp 3.4.11 (GHSA for fastmcp/fastmcp-slim), joserfc 1.8.0,
  filelock 4.2.10, virtualenv 21.15.6, opentelemetry-api 1.45.2, plus a full `uv lock --upgrade`
  (Dependabot alerts 21-52 resolved).
- GitHub Actions pins: setup-python v7.1.0, setup-uv v10.3.3, docker/login-action v4.7.0,
  codeql-action v4.39.1 (the test-guarded `_container-release.yml` / `control-audit.yml` pins are left as reviewed).
- Constrain beartype<0.23 (0.23 ships a `data/` code package the code-only image gate rejects).
- Drop the stale `CIMD refresh failed for ` OAuth log-redaction marker (message removed upstream in fastmcp 3.4.11).
- Dependabot: ignore Python minor/major base-image bumps (fleet stays on 3.14).

## [0.10.10] - 2026-10-28

### Fixed

- Refresh `ci/container-controls.json` from the 2026-10-28 control-audit run: the ledger's oldest
  claim (2026-07-30) passed the 90-day freshness bound, so every pinned fleet container release failed
  "Enforce fleet release controls". The live ledger records the five App-unprovable controls as
  `manual_evidence_required` (partial rows), which the gate accepts as warnings.

## [0.10.9] - 2026-10-28

### Security

- dnspython 2.9.0: fixes CVE-2026-114605 (high).

### Fixed

- Stop advertising unreachable backends: after every catalog refresh the server instructions are rebuilt from reachable backends only, and a call to a tool in a configured but unreachable namespace returns `upstream_unavailable` (`retryable: true`, with the backend name) instead of a `not_found` that points to `search_tools` (#256, #257).

## [0.10.8] - 2026-10-21

### Changed

- Re-pin the drift baseline and release-candidate inventory to `fleet-2026-10-21`: a strict live capture of the 22 immutable, certifi 2026.10.19 / Python 3.14 backend releases (built by `e8931f0`) now deployed in production. Only gtex 3.1.10 changes its release MCP definitions; the genereviews, metadome, panelapp and stringdb baseline entries change only because the v0.10.4+ schema canonicalization now preserves `$defs` (their release `definitions_sha256` is unchanged).
- Regenerated `ci/fleet-application-releases.json`, `ci/release-candidate-fleet.json` and the fleet provenance artifacts from the same verified manifest set.

## [0.10.7] - 2026-10-20

### Fixed

- `_container-release.yml` recovery path: `oras blob fetch` takes one `<name>@<digest>` reference and needs `--output -`, so a rerun after a partial publish failed with `requires exactly 1 argument but got 2`. Now `oras blob fetch --output - "$image@$config_digest"`, guarded by `tests/unit/test_workflow_oras_blob_fetch.py`.

## [0.10.6] - 2026-10-20

### Security

- certifi 2026.10.19: drops emSign root CAs whose intermediate CA keys were compromised (critical).

## [0.10.5] - 2026-10-20

### Changed

- Unify on Python 3.14: the reusable `_container-ci.yml` / `_container-release.yml` workflows (which `uv sync` caller repos with a uv that does not download interpreters), every other workflow, `.python-version`, `requires-python = ">=3.14"`, ruff `py314` and mypy `3.14`. A new guard (`tests/unit/test_workflow_python_version.py`) pins every `setup-python` to `.python-version`. The README-Standard Python badge is now `3.14+`.
- Actions concurrency: every PR/push-triggered workflow already cancels superseded runs; no new groups were needed.

## [0.10.4] - 2026-10-18

### Added

- Provider-compatible OpenAI strict JSON Schema projection (`to_openai_strict_schema`, `detail="strict"` in `search_tools`, and `strip_strict_nulls` in `call_tool`) (#116).
- Support for non-Python (`package.json`) and non-MCP (`mcp_path: null`) services in the reusable container release and CI workflows (#70).
- Integration and conformance suites covering MCP protocol beyond tools (`resources/list`, `prompts/list`, 64-character tool-name ceiling) and Draft 2020-12 `$ref` resolution across all 285 baseline tools (#77).

### Fixed

- Enforce startup invariant `GF_PUBLIC_BASE_URL + GF_MCP_PATH == GF_JWT_AUDIENCE` in OAuth mode and remove the legacy `_install_resource_tolerance()` monkeypatch (#78, #84).
- Flag diagnostics tools advertising `response_mode` in `scripts/check_tool_surface.py` (rule `S5`) and probe `META_TOOLS` advertising `response_mode` with volatile-metadata stripping in `docs/conformance/behaviour.py` (#158).

## [0.10.3] - 2026-10-18

### Changed

- Bump `mcp` from `1.29.0` to `1.30.0` (#241).

## [0.10.2] - 2026-10-18

### Fixed

- Include the 32 pinned `entrypoints` (`always_visible`) in `search_tools` by querying `get_tool_catalog(ctx)` and cache synthetic `search_tools`/`call_tool` `FunctionTool` instances per transform (#221, #232).
- Share a persistent per-event-loop `httpx.AsyncHTTPTransport` connection pool across upstream proxy calls, honor per-backend `cache_ttl` via `CachedProxyProvider`, deduplicate `apply_normalizations()`, and make `RuntimeDriftGuard` a first-class FastMCP `Transform` with zero-RPC quarantine rejection and automatic recovery (#222, #224, #227, #237).
- Offload synchronous `RefreshLedger` SQLite operations from the asyncio event loop via `observe_in_thread()`, recover pending WAL `TRUNCATE` checkpoints in `heartbeat()`, preserve `overlapping_attempt` classification across concurrent refreshes, and propagate `correlation_id` into refresh ledger attempts (#223, #226, #235, #239).
- Include CORS headers on `405 Method Not Allowed` responses to `GET /mcp` (`Accept: text/event-stream`), fail closed in `_verify_cache_admin_auth` when `GF_METRICS_TOKEN` is unset and in `_SlidingWindow.allow()` at `_MAX_TRACKED`, place `NamespaceHintMiddleware` innermost so cached responses retain `_meta.hint`, inspect JSON text blocks in `is_error_response()`, record `is_error` tool outcomes without double-counting `call_tool`, and prevent duplicate OAuth PRM routes (#225, #228, #230, #231, #234, #235).
- Load `.env` in the CLI callback, normalize tool names in `list-tools`, parallelize `doctor` / `fleet-probe` / `snapshot_fleet.py`, pin `workers=1` in `uvicorn.run()`, harden `.github/workflows/fleet-probe.yml` and `.github/workflows/mcp-registry.yml`, and reject malformed JSON-RPC payloads in transport/behaviour conformance probes (#229, #233, #236, #237, #238, #239).

### Changed

- Consolidated Dependabot updates for the `uv` dependency group (#219) and `github_actions` group (#220), and removed unused `gunicorn` and `orjson` runtime dependencies (#237).

## [0.10.1] - 2026-10-04

### Added

- Response caching layer for deterministic slow tools (`vep_recode_variant`, `spliceai`) with TTL, size bounds, LRU eviction, and error response filtering (#213, #215).
- Informative discovery payload on `GET /mcp` instead of HTTP 405 Method Not Allowed (#213).

## [0.10.0] - 2026-10-04

### Added

- Offline BM25 document expansion for `search_tools` (`GF_SEARCH_EXPANSIONS`, default `builtin`): packaged `genefoundry_router/data/tool-expansions.json` with LLM-written requests/keywords per tool, generated by `scripts/gen_tool_expansions.py` (`make expansions`). Held-out hit@5 0.68 → 0.94 with no runtime cost.
- Optional System One reranker stage (`GF_SEARCH_RERANK=systemone`, off by default) for hosted Jev (TypeSafe/OpenRouter, API key) or a self-hosted OneJev: reorders the BM25 shortlist, adds a `relevance` score, can abstain via `GF_SEARCH_RERANK_MIN_SCORE`, and fails open to BM25.
- Held-out discoverability set (`tests/discoverability/heldout_tasks.yaml`, 219 tasks incl. 24 out-of-scope) with CI gates, an expansion-freshness guard, and `make bench-rerank` for validating a rerank endpoint.

## [0.9.4] - 2026-10-04

### Changed

- Refresh the digest-pinned Python 3.14 runtime base and pin the Dockerfile frontend to a verified immutable digest.
- Update paired CodeQL action pins to v4.38.2.
- Refresh all 22 verified backend releases, 285 tool definitions, and database provenance, including the accepted ClinPGx 0.1.3 public data release.
- Increase BM25 weighting of tool names and tags so the refreshed catalog meets the existing discoverability benchmark.
- Run scheduled drift checks through a restricted SSH command on the private fleet network and fail when probes or heartbeat delivery fail.

## [0.9.3] - 2026-10-03

### Security

- Upgrade PyJWT to 2.15.0 and virtualenv to 21.7.13 to resolve the current dependency security advisories.

### Changed

- Consolidate FastAPI 0.142.2, pytest-mock 3.16.0, and Ruff 0.16.9 updates.
- Update immutable setup-uv v10.2.0 and paired CodeQL v4.38.1 action pins and their reviewed contract fixtures.

## [0.9.2] - 2026-09-18

### Changed

- Synchronized fleet application releases and canonical fleet provenance across all 22 federated backends following the fleet-wide Dependabot and security remediation campaign.
- Consolidated runtime and dev dependencies (`gunicorn` 26.2.0, `orjson` 3.12.0, `ruff` 0.16.8, `mypy` 2.3.1).
- Updated GitHub Actions pins across reusable and caller workflows (`astral-sh/setup-uv` v10.1.0, `docker/setup-buildx-action` v4.4.0, `docker/build-push-action` v7.4.0).

## [0.9.1] - 2026-09-18

### Changed

- Consolidated dependency and action updates: bumped `authlib` to 1.8.0, `pydantic` to 2.13.5, `typer` to 0.27.2, `ruff` to 0.16.8, `types-pyyaml` to 6.0.12.20260906.
- Bumped `anchore/sbom-action` to v0.24.2 and `github/codeql-action` to v4.38.0 across reusable container workflows.
- Enabled Dependabot grouping for GitHub Actions and uv dependencies in `.github/dependabot.yml`.

## [0.9.0] - 2026-09-06

### Added

- Added `clinpgx-link` backend (pharmacogenomics: CPIC, PharmGKB, PharmCAT) as the 22nd backend in `servers.yaml`.
- Added canonical fleet provenance endpoints (`GET /provenance` and `GET /api/fleet/provenance`) serving verified database and release provenance for all 22 backends and 285 tools.
- Generated `ci/fleet-provenance.json` and bundled `genefoundry_router/data/fleet-provenance.json`.
- Added generator script `scripts/gen_fleet_provenance.py` and unit tests in `tests/unit/test_provenance.py`.

## [0.8.7] - 2026-09-02

### Changed

- Regenerated the fleet release-evidence set (`ci/fleet-application-releases.json`,
  `ci/release-candidate-inventory.json`, `genefoundry_router/data/fleet-baseline.json`,
  `ci/release-candidate-fleet.json`) at identity `fleet-2026-09-02-contract-remediation`,
  captured against the live fleet (all 21 `-link` backends on their current releases) after
  the controller contract-remediation deploy/activation campaign. Closes #170.

## [0.8.6] - 2026-09-02

### Added

- Release configuration: optional `data.schema_compatibility` in `container-release.json` —
  the data schema versions the application can serve, declared in every `data` mode as fleet
  identifiers (`"4"`, `"5.1"`, `"schema-6"`). The release workflow already projected the field
  into the published manifest, but `ReleaseConfig`'s data models forbid extra keys, so a
  repository that declared it failed `validate-config` with "Extra inputs are not permitted"
  and every data-bearing manifest in the fleet published `"schema_compatibility": []`. The
  fleet controller's data attestation requires the attested schema version to be a member of
  that list and refuses an empty one, so no data release could be activated. Omitting the
  field still publishes `[]`; existing repositories are unaffected. Documented under
  "`data.schema_compatibility`" in `docs/deployment.md`.

### Changed

- `data_requirements.schema_compatibility` in the application release manifest is now
  validated as a bounded list of fleet identifiers rather than free-form strings, so a range
  expression that no consumer evaluates can no longer be published as if it were a version.

## [0.8.5] - 2026-09-02

### Added

- Release: gate the deployed Compose overlay (`docker/docker-compose.npm.yml`) centrally, in
  the shared reusable release workflow, so every fleet repository inherits the same deploy
  contract on its next release. `container_release.py validate-deployed-overlay` renders the
  file set the fleet controller actually deploys and checks numeric `user`, `restart:
  unless-stopped` with no `deploy.restart_policy`, a healthcheck with `start_period`, a
  required `${<PROJECT>_IMAGE:?...}` digest variable, exactly one application image, named
  volumes or tmpfs only, declared container ports with no published host ports, `cap_drop:
  [ALL]`, `read_only`, `no-new-privileges`, and no surviving top-level `x-*` keys. The gate
  runs in `prepare`, before any image is built, and is toggled by the new
  `validate_deployed_overlay` workflow input (default `true`; opting out requires a recorded
  `deployed_overlay_waiver` reason).
- Release configuration: optional `service.deployed_compose_files`,
  `service.deployed_seed_binds` and `service.deployed_sidecars` in `container-release.json`,
  documented under "Fleet Compose Contract" in `docs/deployment.md`.

### Changed

- Split the release model field validators and path patterns into
  `genefoundry_router/release/model_fields.py`, keeping `models.py` inside the 600-line
  module budget. No exported name changed.

## [0.8.4] - 2026-09-02

### Fixed

- Deploy: declare the image's numeric uid:gid in docker/docker-compose.npm.yml so the fleet
  controller can deploy the service; a guard test keeps `user` out of the release Compose
  files.

## [0.8.3] - 2026-08-30

### Security

- Refresh both Python 3.14 container stages to the same reviewed multi-platform index and
  consolidate the current pinned GitHub Actions updates, including paired CodeQL phases.
- Replace the long-lived control-audit credential with a short-lived GitHub App token scoped
  to the exact 22-repository fleet and Metadata/Administration read access. Successful live
  ledgers are strictly validated before retention as workflow artifacts.
- Require GitHub's unattributed-change approval control and an exact zero-approval policy for
  the single-maintainer main-branch ruleset, rejecting missing, relaxed, or mistyped values.
- Fail closed when package linkage, standing-PAT absence, or retention controls cannot be
  observed; anonymous GHCR pullability alone is not accepted as control evidence.

### Changed

- Update FastMCP to 3.4.7, pydantic-settings to 2.15.0, pre-commit to 4.6.2, mypy to
  2.3.1, and Ruff to 0.16.3 in the reviewed dependency graph.
- Require `unless-stopped` for long-lived application/database services while preserving exact
  `restart: "no"` for one-shot init/build roles.

## [0.8.2] - 2026-08-10

### Changed

- Re-pin the drift baseline to `fleet-2026-08-10-dependency-remediation`. All 21
  backends were re-released to land their accumulated Dependabot updates, so every
  image digest and source revision moved and the packaged baseline no longer
  described the running fleet. Regenerated the whole evidence set from the live
  fleet with `make release-candidate` + `make snapshot-baseline`, both of which
  fail closed on any digest mismatch, so this cannot launder a backend that was
  not released.
- Reviewed the tool contract rather than trusting the digests: 272 tools across 21
  namespaces before and after, **none added, none removed, none changed**. These
  were dependency and CI releases; they do not touch the MCP surface, which is why
  no tool was quarantined while the backends rolled out.

## [0.8.1] - 2026-08-10

### Security

- Publish the tested dependency and CI maintenance sweep with refreshed pinned
  CodeQL and Docker login actions and patched runtime dependencies.

### Changed

- Update Uvicorn, Typer, Ruff, and the locked dependency set without changing
  the router API or fleet contract.

## [0.8.0] - 2026-08-07

### Fixed

- Pin the attested production fleet to GTEx Link 3.1.3, PanelApp Link 0.7.0,
  and UniProt Link 5.1.0. PanelApp 0.7.0 moves its `refresh` status from the
  top-level response into `health.refresh`; MCP clients consuming that diagnostic
  shape must read the nested field.
- Canonicalize router-issued OAuth access and refresh token issuers to
  `https://genefoundry.org`. Tokens minted by earlier releases with the historical
  trailing-slash issuer remain valid only through the fixed transition deadline
  `2026-09-06T00:00:00Z`; no arbitrary legacy issuer is accepted. Connectors that
  cached the old authorization-server identifier may reject the new metadata before
  token validation and require disconnect/re-add; an issuer-mismatch during discovery
  is the distinguishing symptom.

### Added

- Add durable, bounded SQLite measurement of OAuth refresh rotation. The router classifies
  successful rotations and a closed set of failure reasons, restores Prometheus counters
  after restart, and records completed startup/clean-shutdown intervals. Production stores
  the ledger at `/data/genefoundry/refresh-observability.sqlite3` on the existing
  `fastmcp_data` volume.
- Add `genefoundry-router refresh-report --json`. The read-only, aggregate-only report uses
  a 7-day/50-attempt gate with a 14-day low-volume fallback. Rotation failure is material
  only when `reuse_after_rotation` plus `upstream_invalid_grant` reaches at least 3 events
  and exceeds 1% of attempts, or at least 2 distinct HMAC-derived clients reauthorize
  within 15 minutes after a failure.
- Export bounded refresh counters as
  `genefoundry_oauth_refresh_attempts_total{client_class}`,
  `genefoundry_oauth_refresh_success_total{client_class}`, and
  `genefoundry_oauth_refresh_failures_total{client_class,reason}`. Existing `/metrics`
  authentication remains unchanged.

### Security

- Keep strict one-time refresh-token rotation unchanged while evidence is collected; the
  observer never replays or accepts a rejected token. Event rows contain only bounded
  classes, a short hash prefix, request ID, timestamp, and HMAC-derived client identity.
  Full token hashes exist only in short-lived mode-0600 tombstones and are never emitted in
  metrics, logs, or reports.
- Domain-separate the client-identity HMAC key from the HS256 OAuth token-signing key,
  while deriving both from the same stable operator-managed key material.

## [0.7.6] - 2026-08-07

### Fixed

- Stop the not-found log-scrub filter blanking framework diagnostics. `NotFoundLogScrubFilter`
  cleared `record.args` for every `fastmcp`/`mcp` record at WARNING or above, and
  `LogRecord.getMessage()` skips `msg % args` when args is falsy — so production printed raw
  printf templates: `Bearer token rejected for client %s: audience mismatch (got %r, expected
  %r)`. The two values that were the entire diagnosis during the 0.7.5 ChatGPT outage were
  invisible, and the same line interpolates correctly under pytest, so it only ever showed up
  in the production topology.
  The scrub is now narrowed by subsystem rather than matching the whole framework logger
  trees. An audit of every `warning`/`error`/`exception` site in the installed `fastmcp` and
  `mcp` found only three that reflect caller input through `%`-args, all request-dispatch
  loggers that remain covered; every other site that echoes caller input builds its message
  with an f-string, where `args` is already empty and clearing it protected nothing. Records
  that are still redacted now interpolate `<redacted>` instead of leaving bare placeholders,
  so they read as a deliberate redaction rather than a broken formatter. The marker branch is
  untouched and still runs for every logger at every level.

### Added

- End-to-end integration coverage for `private_key_jwt` login: CIMD fetch → `/authorize` with
  PKCE S256 → stubbed IdP → `/auth/callback` → `/token` with a genuinely RS256-signed RFC 7523
  assertion → authenticated `initialize` + `tools/list`. The assertion audience is read from
  the running app's authorization-server metadata rather than hardcoded. A Claude-shaped
  control drives the identical harness with `token_endpoint_auth_method: none`, so a broken
  harness turns the control red instead of silently invalidating the result.

## [0.7.5] - 2026-08-07

### Fixed

- Accept `private_key_jwt` client assertions at the token endpoint we actually advertise.
  Every ChatGPT connector login to `https://genefoundry.org/mcp` failed with
  "Beim Einrichten der Verbindung ist etwas schiefgegangen"; Claude was unaffected, which
  made the outage look client-specific rather than like the server-side URL-join bug it is.
  ChatGPT's Client ID Metadata Document declares `token_endpoint_auth_method:
  private_key_jwt`, so it authenticates at `/token` with an RFC 7523 assertion whose `aud`
  is the endpoint published in `/.well-known/oauth-authorization-server` —
  `https://genefoundry.org/token`. FastMCP derives the audience it checks against as
  `f"{self.base_url}/token"`, and `base_url` is a pydantic `AnyHttpUrl`, which normalises a
  bare origin to a **trailing slash**. At the root origin — which this deployment requires,
  because `/authorize`, `/token` and `/register` live at root — that yields
  `https://genefoundry.org//token`. Every correctly signed assertion was rejected as an
  audience mismatch → `Invalid JWT assertion` → HTTP 401 `invalid_client`.
  Clients declaring `token_endpoint_auth_method: none` (Claude) never walk the assertion
  path and kept working throughout, masking the failure. Deployments whose `base_url`
  carries a path are unaffected — the trailing slash only appears for a bare origin.
  The defect is unchanged in the newest FastMCP (3.4.6 is byte-identical to 3.4.5 at the
  offending line), so a dependency bump does not help; the shim stays until upstream joins
  the URL properly. It sits beside `_install_resource_tolerance()` and follows the same
  idempotent-monkeypatch pattern. The URL normaliser parses rather than regex-squeezing, so
  credentials, ports, IPv6 hosts, query strings and fragments are returned byte-for-byte.

### Security

- `cryptography` 49.0.0 → 50.0.0 (CVE-2026-69247, HIGH). This was the sole *fixable*
  HIGH/CRITICAL finding and it failed the `fixable-high-critical-v1` container policy, so
  no image could be published until it was bumped. The remaining HIGH/CRITICAL findings in
  the scan are unfixable Debian base-image CVEs with no upstream fix available and do not
  trip the policy.

## [0.7.4] - 2026-07-30

### Fixed

- Unblock container releases fleet-wide. `ci/container-controls.json` predated the `role`
  field added on 2026-07-18, so `require_compliant_controls` failed closed and no release
  could be published — the last published release was v0.6.14 (2026-07-16). The ledger is
  regenerated from live probes (22/22 rows verified) and the `Protect trusted-builder main`
  ruleset now exists.
- Accept a `required_approving_review_count` of **0 or 1** on the trusted-builder main
  ruleset instead of exactly 1. Requiring 1 encoded an assumption the fleet does not meet:
  GitHub forbids self-approval, so a 1-approval rule with no bypass actor makes `main`
  permanently unmergeable on a single-maintainer repository — including the commit that
  seals the regenerated ledger. That is why the ruleset was never created. Everything the
  control exists for is unchanged and still enforced: active, scoped to `main` alone, zero
  bypass actors, deletions blocked, force-pushes blocked, all changes via pull request. A
  count above 1 is still rejected.
- Treat `automatic_copilot_code_review_enabled` and `dismissal_restriction` as
  optional-but-neutral in the ruleset probe. GitHub omits them from the API response unless
  they are set, and demanding them as mandatory keys made the probe reject a correctly
  configured ruleset. Unknown keys are still rejected.

### Changed

- `test_checked_in_ledger_covers_every_repository_and_is_release_ready` now validates the
  committed ledger **exactly as committed**. It previously injected `role` into every row
  and a synthetic `main_branch_ruleset` into the router's row before validating — asserting
  the ledger was release-ready after making it release-ready. That is why `make ci-local`
  stayed green for ten days against a ledger the release gate rejected. Proven by reverting
  the ledger and watching the guard fail.

- Re-pin the packaged drift baseline and the release-candidate inventory to
  `fleet-2026-07-30`, the fleet released once the gate above was unblocked. All 21 backends
  moved (`autopvs1` 4.1.1→4.1.3, `clingen` 4.0.1→4.0.3, `clinvar` 0.5.0→0.5.3,
  `gencc` 0.8.0→0.8.2, `genereviews` 5.1.0→5.1.1, `gnomad` 9.0.0→9.0.2,
  `gtex` 3.1.0→3.1.2, `hgnc` 2.1.0→2.1.2, `hpo` 0.4.0→0.4.2, `litvar` 6.0.0→6.0.2,
  `mavedb` 0.5.0→0.5.2, `metadome` 0.3.1→0.3.3, `mgi` 0.6.0→0.6.2, `mondo` 0.4.0→0.4.2,
  `orphanet` 0.4.0→0.4.2, `panelapp` 0.6.0→0.6.2, `pubtator` 7.1.4→7.1.6,
  `spliceai` 4.0.0→4.0.2, `stringdb` 4.1.0→4.1.2, `uniprot` 5.0.0→5.0.2,
  `vep` 1.1.0→1.1.2), so every definition digest changed.

  The tool **contract** barely moved, which is the point of reading the diff rather than
  trusting the digest: 272 tools across 21 namespaces before and after, none added and none
  removed. Exactly four definitions changed beyond an embedded version string —
  `gencc.find_curations` gained `Natera` as a submitter enum value, and
  `genereviews.search_passages`, `gnomad.compute_gene_carrier_frequency` and
  `gnomad.get_gene_variants` had their descriptions shortened by the tool-surface-budget
  work, with parameters, enums and semantics unchanged.

## [0.7.3] - 2026-07-30

### Fixed

- Re-pin `github/codeql-action` so Dependabot can actually track it. The pin
  `ed410739…` is not a commit — it is the *annotated tag object* the moving `v4` tag
  pointed at when the pin was taken. GitHub Actions resolves it, so CI stayed green, but
  Dependabot cannot resolve a tag object to a release and therefore never proposed an
  update. The pin had silently frozen at **v4.35.3** while upstream reached v4.37.4.
  Now pinned to the commit `f205ea1c…` with an exact `# v4.37.4` comment, which both
  unfreezes it and restores Dependabot tracking.

## [0.7.2] - 2026-07-30

### Fixed

- Correct five more SHA-pin version comments that named a tag their pin is not. Every one
  understated the pinned release, some by three majors:
  `actions/upload-artifact@043fb46d…` said `v4.6.2` but is **v7.0.1**;
  `actions/download-artifact@3e5f45b2…` said `v4.3.0` but is **v8.0.1**;
  `docker/build-push-action@53b7df96…` said `v7.0.0` but is **v7.3.0**;
  `docker/login-action@af1e73f9…` said `v4.0.0` but is **v4.4.0**;
  `docker/setup-buildx-action@bb05f3f5…` said `v4.0.0` but is **v4.2.0**.
  The pins themselves were already the intended releases — only the comments lied, so this
  is a documentation correction with no change to what CI executes.

### Added

- `make lint-action-versions` (`scripts/check_action_pin_versions.py`) resolves every
  SHA pin against the upstream tag and fails when an exact-version comment names a tag its
  SHA is not. The offline guard added in 0.7.1 proves pin comments are *self-consistent*;
  it cannot catch one that is uniformly wrong, which is exactly how these five survived.
  Wired into CI (needs a token) and skips cleanly on an offline checkout, so it can never
  turn a local build red or become a reason to weaken the offline guard. Bare-major
  comments (`# v4`) are reported as advisory, not failed — those tags legitimately move.

## [0.7.1] - 2026-07-30

### Fixed

- Correct the `actions/setup-python` SHA-pin version comments in the two fleet-wide
  reusable workflows (`_container-ci.yml`, `_container-release.yml`). The pinned SHA
  `5fda3b95…` is tag `v7.0.0`, but five uses documented it as `v6.0.0` while the same SHA
  was correctly labelled `v7.0.0` elsewhere. Every `-link` backend consumes these two
  workflows, so the misleading comment was the fleet's audit surface.

### Added

- Guard the readable half of every SHA pin: `tests/unit/test_action_pin_comments.py`
  fails when one SHA is documented as two different versions, and when an external
  action is unpinned or carries no version comment. `make lint-actions` runs
  `actionlint`, which checks workflow syntax and never reads the pin comment, so this
  class of drift was previously invisible to CI.

### Changed

- Refresh locked dependencies within their declared ranges: `fastmcp` 3.4.4 → 3.4.5,
  `mcp` 1.28.1 → 1.29.0, `fastapi` 0.140.0 → 0.141.1, `uvicorn` 0.51.0 → 0.52.0,
  `websockets` 16.0 → 17.0, plus development tooling.

## [0.7.0] - 2026-07-19

### Added

- Establish trusted-builder governance for reusable container releases, with
  immutable workflow provenance, protected-branch control audits, and sealed
  release manifests.
- Publish the canonical Contract Truth v1 and Runtime Data Identity v1
  conformance helpers, backed by router dogfood tests and rollout gates.

### Changed

- Require release evidence to bind observed runtime data identity while
  preserving explicit compatibility with historical release records.
- Harden GitHub ruleset verification against permissive, unknown, and malformed
  branch-control policy representations.

## [0.6.15] - 2026-07-18

### Fixed

- Re-pin the digest-locked Python runtime base image so the released container
  includes Debian's `liblzma5 5.8.1-1+deb13u1` fix for CVE-2026-34743 (Trivy
  alert #5), without suppressing the vulnerability scanner.

### Changed

- Consolidate the reviewed Dependabot updates for FastAPI, Typer, Ruff, Mypy,
  and the pinned CI, attestation, and scanner actions.

## [0.6.14] - 2026-07-16

### Changed

- Re-pin the reviewed runtime baseline, release-candidate inventory, and
  discoverability catalog to the fully deployed 21-backend fleet. The inventory
  carries the exact immutable application-release provenance for each backend,
  including ClinGen v4.0.1 and MetaDome v0.3.1.
- Make fleet-level README and citation checks reliable from isolated Git
  worktrees by resolving the repository/fleet location from Git's main
  checkout.

## [0.6.13] - 2026-07-16

### Changed

- Give OAuthProxy-issued MCP reference tokens a configurable, bounded 12-hour
  lifetime by default. The router continues to validate and refresh the
  short-lived upstream Keycloak token, improving hosted-connector continuity
  without extending the upstream bearer-token lifetime.

## [0.6.12] - 2026-07-16

### Documentation

- Publish the reviewed cross-fleet issue-remediation design and implementation
  plans, explicitly distinguishing completed remediations from proposed
  follow-up controls.

## [0.6.11] - 2026-07-16

### Changed

- Re-pin the reviewed, signed LitVar-Link v6.0.0 release and its live six-tool
  definition surface. This restores router-to-runtime attestation after the
  backend's runtime-hardening release.

## [0.6.10] - 2026-07-16

### Changed

- Re-pin the reviewed, signed fleet inventory and packaged drift baseline to AutoPVS1
  v4.1.1, StringDB v4.1.0, and PubTator-Link v7.1.4 after live definition
  verification. This restores end-to-end deployment attestation for those releases.

## [0.6.9] - 2026-07-15

### Added

- Add MCP Behaviour Standard v1, Tool-Surface Budget Standard v1, Tool-Schema
  Documentation Standard v1, and the canonical behaviour conformance gate used by
  the GeneFoundry backend fleet.

### Fixed

- Treat `not_found` from a tool's own example-acceptance probe as inconclusive
  while still failing malformed examples.
- Keep auxiliary empty objects from hiding collection rows in grouped payload
  detection.

## [0.6.8] - 2026-07-15

### Fixed

- **Restore OAuth login. Reverts the auth part of #71, which broke every Claude/ChatGPT
  login to `genefoundry.org/mcp` (incident 2026-07-15).** #71 set the OAuthProxy's
  `resource_base_url` to the root origin, on the theory that FastMCP forms
  `_resource_url = resource_base_url + set_mcp_path("/mcp")`. That is true only when you call
  `set_mcp_path("/mcp")` by hand. Under the real mount — `server.http_app(path="/mcp")` inside
  FastAPI — the OAuthProxy's `set_mcp_path` receives the sub-app's own root, `""`, so
  `_resource_url == resource_base_url` verbatim. With the origin, the live `_resource_url` was
  the bare origin: the OAuthProxy's RFC 8707 resource check rejected every client sending
  `…/mcp` (the endpoint the RFC 9728 metadata itself advertises) with `server_error`, and
  minted tokens carried `audience == origin`, which the router's own `JWTVerifier`
  (`GF_JWT_AUDIENCE` = `…/mcp`) rejects.

  `resource_base_url` is now `GF_JWT_AUDIENCE` again, so the live `_resource_url` is the
  endpoint (correct resource check + correct minted audience). `_install_resource_tolerance()`
  is restored: because FastMCP's PRM derivation *does* append the path, it can advertise
  `…/mcp/mcp`, and the tolerance collapses that doubled segment so clients echoing it back
  still validate. `tests/unit/test_auth_resource_url.py` now models the LIVE mount
  (`set_mcp_path("")`), not the hand-call, so origin-base fails it — #71 cannot silently
  return. The proper long-term fix is upstream in FastMCP: the PRM advertisement and the
  OAuthProxy resource check must derive the resource URI the same way (see the tracking issue).

**Operator note:** minted-token audience changes back to `…/mcp`, so live OAuth sessions
re-authenticate after the redeploy (clients re-run dynamic client registration).

## [0.6.7] - 2026-07-14

### Fixed

- Stop advertising a doubled protected-resource URI (`https://host/mcp/mcp`) in OAuth
  mode. FastMCP appends the MCP mount path itself — `set_mcp_path("/mcp")` sets
  `_resource_url = resource_base_url + "/mcp"` — so `resource_base_url` must be the ROOT
  origin. The router passed `GF_JWT_AUDIENCE`, which already ends in `/mcp`, baking the
  segment in twice. Consequences, all now gone: the RFC 9728 metadata advertised a
  resource that is not the endpoint; `OAuthProxy` minted tokens with an audience
  (`…/mcp/mcp`) that the router's own `JWTVerifier` — configured with `GF_JWT_AUDIENCE`
  (`…/mcp`) — would reject; and spec-compliant clients, reading that metadata, echoed the
  doubled URI back in their RFC 8707 `resource` parameter.

  `jwt` mode was always correct (it passed `GF_PUBLIC_BASE_URL`); only the deployed
  `oauth` mode regressed. The two modes now agree, and
  `tests/unit/test_auth_resource_url.py` pins the resource URI in both — nothing in the
  suite covered it, which is how this shipped.

- Remove the `_install_resource_tolerance()` monkeypatch. It rewrote FastMCP's resource
  normalizer to collapse `/mcp/mcp` → `/mcp`, attributing the doubled URI to "some MCP
  clients (e.g. ChatGPT)". The clients were behaving correctly: they read the router's own
  metadata and echoed it back. With the root cause fixed, the compensation is unnecessary,
  and the router no longer patches a dependency's internals at import time.

**Operator note:** this changes the minted token audience, so live OAuth sessions
re-authenticate after the redeploy (clients re-run dynamic client registration).

## [0.6.6] - 2026-07-13

### Fixed

- Authenticate to GHCR in the finalize job. It held `packages: write` and pushed the
  version alias with `oras cp`, but never logged in, so the final step of the final job
  failed with `denied` after the image, the release, and every attestation had already
  published. `publish-attest` was the only job that authenticated. The login is placed
  immediately before the push, after the sealed evidence is verified, preserving the
  "verify before credentials" ordering. A guard test now fails any job that pushes to
  GHCR without authenticating.

## [0.6.5] - 2026-07-13

### Fixed

- Complete the container release pipeline end to end. The first real release exercised
  jobs that had never run and exposed six defects, each fixed with a regression test:
  the gate ran the image with no environment so the router's secure-by-default guards
  refused to boot it; publication addressed the OCI layout by a ref name that a fresh
  buildx export never writes; `--signer-repo` and `--signer-workflow` were passed
  together though `gh` rejects them as mutually exclusive (also in the deploy-time
  verifier); scanner evidence was read from fields the scan report does not carry,
  sealing the string `"null"` as a timestamp; the privileged finalize job had no git
  context for `gh`; and release verification raced GitHub's asynchronous
  immutable-release attestation.

### Added

- Validate auxiliary sidecar roles and implement the smoke profiles. The compose policy
  previously permitted exactly one service, which blocked every data-bearing backend
  that needs an init or database sidecar. Sidecars are now authorized by role, never by
  name, with the container-hardening invariants enforced on the sidecar too.

## [0.6.4] - 2026-07-12

### Security

- Add the reviewed fleet release-candidate baseline gate, proxy-aware production
  bind controls, and the versioned HTTP Policy v1 adoption/conformance ledger.
  The router now rejects candidate baseline drift, requires fleet adoption
  evidence, and documents the canonical outbound HTTP safeguards used by the
  affected backends.
- Attest the fleet GitHub and GHCR release controls. `scripts/audit_container_controls.py`
  probes every expected repository's tag ruleset, protected release environment,
  immutable releases, and public anonymously-pullable GHCR package against the live
  API and emits `ci/container-controls.json`. A control that cannot be proven emits an
  `unavailable` row naming the exact repository and control, so the release gate stays
  closed; absence of evidence is never a pass.

### Fixed

- Apply a repository's declared `smoke_environment` to the release gate containers. The
  gate ran the built image with `docker run` and no environment, so the router's
  secure-by-default guards refused to start it (`GF_AUTH_MODE=none` on a non-loopback
  bind, then an empty `GF_ALLOWED_HOSTS`) and the health/MCP gate could never pass.
  Backends declare no smoke environment and are unaffected. Assignments are schema-bound
  to `KEY=VALUE` over a charset that excludes whitespace, quotes, and shell
  metacharacters, so an entry cannot split the `docker run` argument or reach a shell;
  the field is public checked-in configuration and must never carry a secret.

## [0.6.3] - 2026-07-12

### Build

- Consolidate Dependabot updates: `uvicorn[standard]` floor `>=0.50.0` → `>=0.51.0` (#46),
  `ruff` `0.15.20` → `0.15.21` (#45), `mypy` `2.1.0` → `2.2.0` (#44). Dev/build tooling + ASGI
  server bumps only — no runtime behavior or API change; `make ci-local` green under the new
  toolchain.

## [0.6.2] - 2026-07-11

### Security

- Close the FastMCP-core not-found reflection residual on the router itself (the tracked
  fast-follow from 0.6.1). The router's own FastMCP core reflected the caller's requested
  tool name / resource URI / prompt name — with any control / zero-width / bidi / NUL code
  points it carried — back to the caller frame and to logs, for a name/URI the router rejects
  *itself* before proxying (its own core path and the `call_tool` meta-tool). New module
  `genefoundry_router/notfound_guard.py` adds a layered guard, wired into `server.py`
  (spec/plan `docs/{specs,plans}/2026-07-11-fastmcp-notfound-reflection-guard*`):
  - Layer 1 — `NotFoundGuard.on_call_tool` registry preflight (`get_tool` resolves mounted-proxy
    and meta-tool names from mount-cached metadata without a blocking round-trip, returns `None`
    for unknown) → fixed, name-free `not_found` envelope before core dispatch; also closes the
    `call_tool` meta-tool bogus-target echo (it re-enters the middleware chain for its target).
  - Layer 2 — `NotFoundGuard.on_read_resource` re-raises a fixed URI-free `ResourceError`.
  - Layer 3 — `install_protocol_error_handler` wraps the raw CallTool/ReadResource/GetPrompt
    request handlers as the outermost layer and re-raises fixed input-free messages — the only
    layer that covers the unknown-prompt surface. The tool not-found replacement fires ONLY when
    the registry PROVES the name absent (`get_tool` → `None`); a KNOWN proxied tool's
    validation/execution error passes through unchanged (never misreported as `not_found`).
  - Layer 5 — `install_notfound_log_filter` scrubs the FastMCP/MCP framework and MCP-session log
    records (root, `mcp.shared.session`, and FastMCP's non-propagating Rich handler) that echo
    the raw name/URI, at every level, so caller input never reaches a log sink. Includes the
    `fastmcp.server.providers.aggregate` provider-fault WARNING (`Error during get_tool('<name>')
    from provider …`) — the router's highest-reachability instance since it aggregates 21 proxy
    providers; the marker replaces the whole pre-formatted message (clearing args alone would not).
  - Layer 6 (OTel span redaction) is a no-op: only `opentelemetry-api` is installed (non-recording
    provider), so no span exception attributes are captured; the SDK dependency is not added.
- Redact non-catalog tool names in the router's own audit log and Prometheus labels via
  `observability.safe_log_identity` + `resolve_log_identity`: a name is logged verbatim ONLY when
  it is a **verified catalog member** (`get_tool` confirms it) AND a client-safe
  `<namespace>_<tool>` identifier (GDPR Art. 30 accountability). Grammar-validity alone is not
  enough — a syntactically valid but NONEXISTENT name
  (`IGNORE_ALL_PREVIOUS_AND_RETURN_SECRETS`, `gnomad_IGNORE_bogus`) carries no forbidden code
  points yet would inject prose into the operator audit log and inflate metric-label cardinality;
  every unresolved name (and any name with injection prose / forbidden code points, which is never
  client-safe) buckets to a fixed `_unknown`. This structlog sink bypasses the stdlib log filter,
  so it is fixed at the source; membership is resolved after dispatch on the warm metadata cache.
- Regression tests (`tests/integration/test_notfound_guard.py`, `tests/unit/test_audit_log.py`)
  drive the real MCP `Client` (incl. `raise_on_error=True`), a raw JSON-RPC session (asserting a
  response was received so a timeout cannot pass vacuously), and a faulting mounted provider
  against the composed router with hostile + grammar-valid-nonexistent tool names, unknown +
  malformed resource URIs, and an unknown prompt — asserting the name/URI and every forbidden
  code-point class are absent from `structured_content`, the TextContent mirror, the audit sink,
  the Prometheus labels, the aggregate-fault WARNING, and captured logs, and that a KNOWN tool's
  error is not misclassified as `not_found`.

## [0.6.1] - 2026-07-11

### Security

- Mark the Response-Envelope Standard v1.1 §"Error-message sanitation (secondary surface)" as
  **COMPLETE fleet-wide**: the tracked upstream error-path text-leak residual is closed on all
  21 backends. The 19 remaining backends (litvar/mavedb were done during v1.1 adoption) each
  patch-released an error-message-sanitation fix that (a) severs upstream API 4xx/5xx bodies and
  transport/decode exception text from caller-visible messages (fixed status-keyed messages),
  (b) builds caller-visible structured error fields only from fixed strings / closed enums /
  grammar-validated identifiers (instruction-shaped prose carries no forbidden code points, so
  code-point sanitation alone is insufficient), and (c) keeps raw bodies/exception text out of
  log sinks. Every merge was gated by an adversarial Codex (gpt-5.6-sol xhigh) review driving the
  real MCP tools with hostile upstream bodies and inputs. Spec + plan added under
  `docs/specs/2026-07-11-error-message-sanitation-fleet-sweep-design.md` and
  `docs/plans/2026-07-11-error-message-sanitation-fleet-sweep.md`.
- Fast-follow (tracked, out of scope here): FastMCP-core not-found surfaces (unknown-tool-name /
  unknown-resource-URI reflection) echo the caller's own requested name — a uniform middleware
  preflight/redaction sweep is tracked separately; several backends already carry the fix.

## [0.6.0] - 2026-07-11

### Security

- Complete fleet-wide Response-Envelope Standard v1.1 (untrusted-content fencing) adoption:
  the 16 free-text `-link` backends (autopvs1, clingen, clinvar, gencc, genereviews, gnomad,
  gtex, hpo, litvar, mavedb, mgi, mondo, orphanet, panelapp, stringdb, uniprot) each shipped a
  breaking release that fences every upstream free-text surface as the typed `untrusted_text`
  object and are flipped to `compatibility: breaking-v1.1` in
  `docs/conformance/untrusted-text-inventory.yml`, with the full set of tool+pointer surfaces
  and measured per-tool object-count ceilings recorded per row. The 4 no-text backends (hgnc,
  vep, metadome, spliceai) are confirmed `n/a-no-untrusted-text` with a regression-guard test
  citation. Corrected the `gencc` row (only `get_gene_disease_assertion` exposes a notes
  surface; `get_gene_curations`/`get_disease_curations` do not) and the `clinvar` row
  (`top_traits[].trait`, not `.name`).
- Add `tests/unit/test_untrusted_content_fleet_conformance.py`, a fleet completeness gate
  asserting every `untrusted-text` inventory row is `breaking-v1.1` with a named test vector,
  and every `no-untrusted-text` row is `n/a-no-untrusted-text` with evidence.
- Ratify `docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md`: a limit breach MUST raise a backend-specific
  typed execution error (not one fleet-uniform code); document the error-message sanitation
  requirement for upstream error-body text as a secondary untrusted-content surface.

## [0.5.0] - 2026-07-11

### Security

- Add a patient-data / on-prem deployment profile (`docker/.env.patient-data.example`) that
  disables AutoPVS1 by omitting its backend URL, preventing third-country transfer of possibly
  Art. 9 variant data to `autopvs1.bgi.com` (router #32 / autopvs1-link #41). The profile
  mandates edge auth and drift enforcement and documents the required network-level egress deny.
- Gate the untrusted-text conformance inventory against the `servers.yaml` registry so a newly
  federated backend cannot ship a free-text tool without an explicit untrusted-content
  classification (Response-Envelope Standard v1.1 §9.2).

### Documentation

- Complete the untrusted-text source audit: every backend inventory row now names its exact
  free-text tool(s) + JSON pointer(s) with model evidence, or is classified `no-untrusted-text`.
- Add the fleet-modernization reconciliation ledger reconciling merged state against verified
  git reality with the remaining-work matrix.

## [0.4.0] - 2026-07-10

### Security

- Enforce exact Host and Origin allowlists at the outer HTTP boundary, including
  health, metrics, OAuth metadata, and MCP routes.
- Package the reviewed normalized fleet baseline and compare complete tool
  definitions at startup and on polling refreshes. Enforce mode fails startup
  on changed definitions and quarantines added or changed tools during polling.
- Publish bounded drift state through health and aggregate metrics, and require
  production Host and healthcheck configuration in the supplied Compose stack.

## [0.3.0] - 2026-07-10

### Security

- Add router-owned backend service credentials without forwarding caller Authorization headers.
- Require the `pubtator:write` caller scope for the canonical eight state-changing PubTator
  tools, with fail-closed no-auth behavior and PII-safe denial logging.
- Ignore missing or blank backend credentials instead of emitting an empty Bearer header, and
  document router-first credential staging for outage-free backend enforcement.

### Documentation

- Add the fleet modernization execution ledger with immutable Wave 0 merge, release, and
  validation evidence and explicit pending states for later security waves.
