# Residual Fleet Remediation — Completion Ledger

**Status:** locally verified implementation candidates; pending push and external CI verification

**Scope:** R-01 through R-08 from the supplied issue brief.  This ledger is deliberately an
evidence record, not a statement that unpushed branches have been released or that GitHub has
accepted them.

## Evidence rules

- **Local gates passed:** each remediation candidate passed its established focused test command
  and `make ci-local` before handoff.  This is local, branch-local evidence only.  It does not
  establish a GitHub Actions result, nor does it prove the branch is the current remote revision.
- **Directly re-run for this ledger:** in this router worktree, `git diff --check HEAD` exited 0
  and `uv run pytest tests/unit/test_http_policy_adoption.py -q` exited 0 (**19 passed in
  0.02s**) on 2026-07-12.
- **Actionlint:** the final reviewed GeneReviews, Orphanet, and MGI tips were checked locally
  with actionlint.  Their exact results are recorded in the R-06/R-07 rows below.  These local
  checks do not establish a remote workflow result.
- **External verification still required:** push every listed branch, open/review the associated
  changes, and require the relevant current-revision GitHub Actions checks.  In particular, the
  R-06 workflow changes require actionlint/YAML validation in remote CI, and R-07 explicitly
  requires current-revision CI to be green.

## Candidate revisions and local commands

| Track | Candidate implementation commit(s) | Established local verification (exit 0) |
| --- | --- | --- |
| Router R-02/R-08 | `7ff76dd65742ce4a0501f5179051cacbca90f4a4` | `make ci-local` |
| Router R-05 recipe/gate/attestations | `ee1b8c1de9ec92679b3e2342d3e209c2aaf0b132`, `aa4ca1da95b7074079a8b7440f0466335a1624a2` | `make http-policy-adoption`; `make ci-local` |
| GeneReviews R-01 | `36d9f73` + final reviewed tip `d68ef3a666411cc80f0ffa16fc9876399244b4ab` | `uv run pytest tests/unit/test_corpus_ingest_ceilings.py -q`; `make ci-local` (**932 passed, 1 skipped**) |
| GeneReviews R-06 | `347a562` + final reviewed tip `d68ef3a666411cc80f0ffa16fc9876399244b4ab` | `uv run pytest tests/test_github_actions_pinning.py -q`; Docker workflow actionlint clean; `make ci-local` (**932 passed, 1 skipped**) |
| UniProt R-03/R-05/R-07 | `7a1314c`, `281505c`, `4e61658f1b76512d9925d601b2943032d4f00670` | focused query/client/log-filter/conformance tests; `PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run pytest -n auto tests/unit/mcp/test_log_filters.py -q`; `make ci-local` |
| MaveDB R-04 | `77dfeae` + `196584ab42e41b33af805175531730b4cfd19bf3` | `uv run pytest tests/unit/test_hgvs_resolution_privacy.py -q`; `make ci-local` |
| Orphanet R-06 | `f7626b1` + final reviewed tip `97528a6c79907cbf98d1fa4b4a403d427f00f35d` | `uv run pytest tests/unit/test_docs_and_ci_contracts.py -q`; actionlint 1.7.12 clean; `make ci-local` (**549 passed, 1 skipped**) |
| MGI R-07 | `99a00144214d3b6d1f90809032b7d6ddef88bcfb` + final reviewed tip `37d34869f73e7713d88f10e6f8de0f2a3cc651c5` | `uv run pytest tests/unit/test_cli.py tests/integration/test_live.py -q`; actionlint 1.7.12 clean; `make ci-local` (**280 passed, 1 skipped**) |

Short commit IDs in the table are resolved by their named candidate branch; the full branch-tip
SHA is included where that is the reviewed revision.  No candidate has been represented as
merged, pushed, or remotely green here.

## Acceptance matrix

### R-01 — GeneReviews #93: whole-operation ingest ceilings

Implementation: `36d9f73` (deadline and archive accounting), with artifact-specific caller
deadlines in `a9c8f947cb421ee248a1fc45dcadb6eef23a5d8f`, carried by final reviewed tip
`d68ef3a666411cc80f0ffa16fc9876399244b4ab`.

| Acceptance criterion | Evidence |
| --- | --- |
| Slow drip under the read timeout fails at the total deadline | `tests/unit/test_corpus_ingest_ceilings.py::test_stream_timeout_is_per_read_not_total`; monotonic operation deadline in `download_guard.py`; focused command and local gate above passed. |
| Oversized non-NXML is rejected before/during consumption | `test_iter_tarball_rejects_oversized_member` and `test_iter_tarball_accounts_for_ignored_regular_members`; every regular member is counted before suffix filtering. |
| Ignored and NXML members share one expanded-byte budget | `test_iter_tarball_accounts_for_ignored_regular_members`; `parallel.py` applies cumulative accounting before NXML selection. |
| Hostile archive cases cover declared mismatch, ignored members, and compressible data | `test_iter_tarball_rejects_regular_member_actual_size_mismatch`, `test_iter_tarball_accounts_for_ignored_regular_members`, and `test_iter_tarball_rejects_highly_compressible_decompression_bomb`. |
| Valid corpus ingestion remains green | Established GeneReviews `make ci-local` pass (**932 passed, 1 skipped**); remote CI remains pending. |

### R-02 — Router #48: reviewed release baseline

Implementation: `7ff76dd65742ce4a0501f5179051cacbca90f4a4`.

| Acceptance criterion | Evidence |
| --- | --- |
| LitVar annotations and output schemas are explicit in the packaged baseline | Offline gate `tests/integration/test_release_candidate_baseline.py::test_release_candidate_baseline_has_corrected_tool_metadata`; release inventory and full definitions are checked in. |
| VEP read-only annotations are complete | Same offline test asserts `readOnlyHint is True` for every VEP tool. |
| MetaDome request tool annotation is non-read-only, non-destructive, idempotent | Same offline test asserts all four relevant annotation values. |
| Enforce-mode startup accepts the pinned release definitions | `test_packaged_baseline_matches_reviewed_release_candidate_full_definitions` requires exact backend definitions plus candidate inventory/revisions to match the packaged baseline; established router `make ci-local` includes integration tests.  This is offline contract evidence, not a live deployment probe. |
| CI fails for unreviewed backend contract drift | Candidate inventory SHA/revision and baseline equality are source-controlled integration assertions in the same test; router CI runs `make ci-local`.  Remote CI is pending. |

### R-03 — UniProt #17: bounded SPARQL work

Implementation: `7a1314c` (carried by reviewed branch tip
`4e61658f1b76512d9925d601b2943032d4f00670`).

| Acceptance criterion | Evidence |
| --- | --- |
| Graph forms cannot initiate unbounded default work | `tests/unit/test_queries.py::TestQueryClassifier::test_classify_rejects_graph_returning_forms`; validation rejects `CONSTRUCT`/`DESCRIBE`. |
| Federated `SERVICE` is rejected by default | `test_classify_rejects_real_service_at_any_group_depth`, plus decoy tests for comments, literals, IRIs, and prefix tokens. |
| Deadline covers the whole request, including first byte | `tests/unit/test_client.py::test_execution_deadline_covers_retry_backoff` and `::test_execution_deadline_covers_time_to_first_byte`; `asyncio.timeout` encloses execution/retry. |
| Obfuscation/nesting/decoys and first-byte timeout are covered | The three classifier tests above plus `test_execution_deadline_covers_time_to_first_byte`. |
| Bounded SELECT behavior remains green | Existing query tests and established UniProt `make ci-local` pass. |

### R-04 — MaveDB #20: HGVS-private resolution failures

Implementation: `77dfeae` (additional live-path regression in
`196584ab42e41b33af805175531730b4cfd19bf3`).

| Acceptance criterion | Evidence |
| --- | --- |
| Not-found, ambiguous, and resolution-failure responses contain no supplied HGVS | `tests/unit/test_hgvs_resolution_privacy.py::test_hgvs_resolution_failures_do_not_reflect_input`; fixed error texts in resolvers and variant lookup. |
| Normal/error logs contain no supplied HGVS | The same test captures logs and asserts the distinctive input is absent. |
| Hostile valid-looking HGVS is absent from envelopes, exceptions, and logs | `test_hgvs_resolution_failures_do_not_reflect_input` and `test_live_hgvs_probe_miss_does_not_reflect_input`. |
| Error code and retryability stay machine-actionable | Privacy tests retain typed resolver errors/envelope assertions; established focused test and `make ci-local` passed. |

### R-05 — eight backends: HTTP-policy v1 conformance

Canonical implementation: router `ee1b8c1de9ec92679b3e2342d3e209c2aaf0b132` and
`aa4ca1da95b7074079a8b7440f0466335a1624a2`; normative recipe:
`docs/HTTP-POLICY-STANDARD-v1.md`; contract hash:
`c2aad4ecb0ec88839fe2caa1e059ddef491a11abd52a38bb8bb4f3b1bd56a2ee`.

Every R-05 issue has the same acceptance criteria.  The checked-in conformance fixture covers
HTTPS-only, syntactic userinfo (including empty `:@`), normalized exact origin and `:443`, every
redirect hop, a limit of five redirects, decoded streamed byte caps, and fixed host-free,
non-retryable failures.  The backend-specific production adapter tests preserve documented
POST/pagination/upstream behavior.  The local commands were the backend conformance test plus
`make ci-local`; all passed as established above.

| Repository / issue | Exact reviewed implementation candidate | Reviewer-attested source-only evidence |
| --- | --- | --- |
| GTEx #63 | `2e73a07a5aeb353fa649167db36fc16736ecbd02` | `ci/http-policy-v1-evidence/gtex-link/{attestation.json,test_http_policy_v1.py}` |
| LitVar #50 | `c53f829b744941d2caf1c6f4932eff5524b7bc54` | `ci/http-policy-v1-evidence/litvar-link/{attestation.json,test_http_policy_v1.py}` |
| MetaDome #10 | `019e430b79c90423af61c3d5ad623bb2eb35ce6b` | `ci/http-policy-v1-evidence/metadome-link/{attestation.json,test_http_policy_v1.py}` |
| PanelApp #14 | `dab7952cbb593c2dd86223b537d6c62d9cb3beb0` | `ci/http-policy-v1-evidence/panelapp-link/{attestation.json,test_http_policy_v1.py}` |
| SpliceAI #16 | `21a8110461c0cb83e252da622cc868058a9c0241` | `ci/http-policy-v1-evidence/spliceailookup-link/{attestation.json,test_http_policy_v1.py}` |
| StringDB #23 | `60bf70b104bdf473abbf8634c8885716e8b3b0b5` | `ci/http-policy-v1-evidence/stringdb-link/{attestation.json,test_http_policy_v1.py}` |
| UniProt #18 | `4e61658f1b76512d9925d601b2943032d4f00670` | `ci/http-policy-v1-evidence/uniprot-link/{attestation.json,test_http_policy_v1.py}` |
| VEP #15 | `3b06cb2e25e3c51ee0df6a33ac8ea6b3cfb95eea` | `ci/http-policy-v1-evidence/vep-link/{attestation.json,test_http_policy_v1.py}` |

The router test `tests/unit/test_http_policy_adoption.py` requires precisely those eight rows,
their reviewed 40-hex candidate revisions, identical conformance-file hashes, and a non-empty
reviewer/date attestation.  It was re-run for this ledger: **19 passed**.  This is intentionally
only **reviewer-attested source evidence**: it verifies checked-in copies and claims, not a
backend checkout, a pushed revision, a test execution in another repository, external services,
or GitHub Actions.  Each row still requires external review and push before it can count as
current-revision evidence.

### R-06 — immutable third-party GitHub Actions

| Issue / acceptance criterion | Implementation and local evidence |
| --- | --- |
| GeneReviews #94: all non-local actions are full SHA pins; nested/reusable workflows are scanned | `347a562` (carried by final reviewed tip `d68ef3a666411cc80f0ffa16fc9876399244b4ab`); `tests/test_github_actions_pinning.py::test_external_github_actions_are_sha_pinned_with_version_comments` recursively scans `.github`; focused test and `make ci-local` (**932 passed, 1 skipped**) passed. |
| GeneReviews #94: Dependabot/equivalent can propose reviewed SHA updates | Existing dependency-update configuration is retained; source test proves readable version comments accompany pins. |
| GeneReviews #94: workflow YAML/actionlint and jobs pass | YAML source is covered by the local test/gate; the Docker workflow actionlint check was clean at `d68ef3a666411cc80f0ffa16fc9876399244b4ab`. Remote jobs remain pending because this commit is unpushed. |
| Orphanet #14: no mutable/short action refs, including composite action | `f7626b1` (carried by final reviewed tip `97528a6c79907cbf98d1fa4b4a403d427f00f35d`); `tests/unit/test_docs_and_ci_contracts.py::test_github_action_pin_check_recurses_and_rejects_version_tags` and follow-up `test_github_action_pin_check_rejects_mutable_docker_actions`; focused test and `make ci-local` (**549 passed, 1 skipped**) passed. |
| Orphanet #14: release/data-build workflows retain a reviewable update path | `f7626b1` adds `.github/dependabot.yml` with `github-actions`; local source checks passed. |
| Orphanet #14: workflow YAML/actionlint and jobs pass | actionlint 1.7.12 was clean at `97528a6c79907cbf98d1fa4b4a403d427f00f35d`. Remote jobs remain pending because this commit is unpushed. |

### R-07 — hermetic, parallel-safe tests

| Issue / acceptance criterion | Implementation and local evidence |
| --- | --- |
| MGI #16: refresh unit passes with network disabled | `99a00144214d3b6d1f90809032b7d6ddef88bcfb` (carried by final reviewed tip `37d34869f73e7713d88f10e6f8de0f2a3cc651c5`); `tests/unit/test_cli.py::test_cli_refresh_builds` patches at the runtime lookup site under the unit network-deny fixture. |
| MGI #16: accidental unit-network access fails clearly | Same implementation adds the autouse unit fixture in `tests/conftest.py`; the failure message is `network access is disabled in unit tests`. |
| MGI #16: live coverage is opt-in and bounded | `tests/integration/test_live.py::test_live_markers_download_has_an_explicit_timeout`; focused test and `make ci-local` (**280 passed, 1 skipped**) passed. |
| MGI #16: workflow YAML/actionlint passes locally | actionlint 1.7.12 was clean at `37d34869f73e7713d88f10e6f8de0f2a3cc651c5`. |
| MGI #16: current-revision CI green | **Pending external verification** after push; no GitHub Actions result is claimed. |
| UniProt #19: repeated two-worker run passes | `7a1314c`/`4e61658f1b76512d9925d601b2943032d4f00670`; established `PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run pytest -n auto tests/unit/mcp/test_log_filters.py -q` and `make ci-local` passed. |
| UniProt #19: logger filters/handlers are restored exactly | `tests/unit/mcp/test_log_filters.py::test_logging_fixture_restores_every_captured_global_state_after_mutation`. |
| UniProt #19: redaction is never weakened | `test_installed_filter_sanitizes_records_end_to_end` preserves fixed-message sanitizer behavior. |
| UniProt #19: current-revision CI green | **Pending external verification** after push; no GitHub Actions result is claimed. |

### R-08 — Router #49: proxy-aware production controls

Implementation: `7ff76dd65742ce4a0501f5179051cacbca90f4a4`.

| Acceptance criterion | Evidence |
| --- | --- |
| Production/proxied loopback fails without rate limit and metrics token | `tests/unit/test_secure_bind.py::test_production_requires_controls_on_loopback`; `GF_DEPLOYMENT_MODE=production` is explicit in production Compose. |
| Development bypass is explicit and warns | `test_development_only_override_warns_when_controls_are_omitted`; production cannot use the override (`test_insecure_bind_override_cannot_downgrade_production_controls`). |
| Direct public, loopback-development, and proxied-loopback cases are covered | `test_refuse_public_auth_bind_without_rate_limit`, `test_loopback_bind_is_secure_without_auth`, `test_production_requires_controls_on_loopback`, and deployment-profile tests. |
| Reachable production metrics are authenticated | `test_refuse_public_metrics_without_token` plus production-loopback control test. |
| Reachable production general rate limit is positive | `test_production_requires_controls_on_loopback` and `test_refuse_public_auth_bind_without_rate_limit`; established router `make ci-local` passed. |

## Required external close-out

1. Push each named candidate branch and ensure its reviewed SHA is the branch/PR tip; update the
   R-05 ledger only if the reviewed revision changes.
2. Require current-revision GitHub Actions for router, all eight R-05 backends, GeneReviews,
   MaveDB, Orphanet, MGI, and UniProt.  Record URLs/run IDs and conclusions in a follow-up ledger
   update rather than retroactively treating this local evidence as remote evidence.
3. Before deployment, perform the operator-owned production rollout/re-pin checks.  This work
   does not deploy services, change secrets, or assert live upstream behavior.
