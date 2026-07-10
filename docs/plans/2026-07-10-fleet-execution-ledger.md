# Fleet Modernization Execution Ledger

Completed evidence is immutable. Pending cells are updated only after the named gate has produced
verifiable evidence; runtime run identifiers refer to the post-release validation workflow.

| Repo | Issue/PR | Branch | Behavior tests | ci-local | Claude review | PR/checks | Merge SHA | Version/release | Runtime evidence | State |
|---|---|---|---|---|---|---|---|---|---|---|
| genefoundry-router | plan PR #37 | docs/fleet-security-modernization-2026-07-10 | n/a (docs) | passed | adversarial findings resolved | #37 checks passed | 9afea88418e5f271dd169288f146f121a87720ef | n/a | n/a | complete |
| gencc-link | base PR #22 | fix/dependabot-base-format | n/a (format-only) | 434 passed; 1 live probe skipped | task review approved | #22 checks passed | c8606050560bdeb7b07d533637b1ae600990575e | v0.5.4 / #23 | run 29110009167 passed | complete |
| gencc-link | #20 | dependabot/github_actions/astral-sh/setup-uv-8.3.2 | remote blob/YAML/SHA checks passed | passed | anomalous findings disproved; clear | #20 checks passed | 0ff5b2687f078b496f4c496991b41c632ca57960 | v0.5.4 / #23 | run 29110009167 passed | complete |
| gencc-link | #21 | dependabot/uv/uv-42d6deb6a8 | FastMCP envelope regression fixed; non-swallow tests | 436 passed; 1 live probe skipped | clear; medium test gap resolved | #21 checks passed | 50af7ee83318e4606a4592a58b28ad1ea25aa16b | v0.5.4 / #23 | run 29110009167 passed | complete |
| gencc-link | release PR #23 | chore/release-0.5.4 | version/dependency metadata spot-check | 436 passed; 1 live probe skipped | clear | #23 checks passed | 954166fe75f9cf096557cbe1b332c78ecb5c014b | v0.5.4 | run 29110009167 passed | complete |
| gnomad-link | base PR #31 | fix/dependabot-base-format | n/a (format-only) | 615 unit + 15 eval passed; 1 deselected | task review approved | #31 checks passed | 99d7ad74fd7a49a55ba5de0100d1730b305e01d0 | v6.0.4 / #33 | run 29110056288 passed | complete |
| gnomad-link | #29 | dependabot/uv/uv-6350993ea2 | superseded by #32 | failed on stale base | n/a (superseded) | #29 closed; superseded by #32 | n/a | n/a | n/a | closed (superseded) |
| gnomad-link | replacement PR #32 | dependabot/uv/uv-fff772d727 | 14 FastMCP regressions fixed; wrapper/non-swallow tests; docs build | 618 unit + 15 eval passed; 1 deselected | clear; all medium findings resolved | #32 checks passed | eedc9a751fdf5f564e1bea5b57ee3e30c53ff6c0 | v6.0.4 / #33 | run 29110056288 passed | complete |
| gnomad-link | #30 | dependabot/github_actions/astral-sh/setup-uv-8.3.1 | remote blob/full-SHA checks passed | 615 unit + 15 eval passed; 1 deselected | clear | #30 checks passed | f037e8c922df7b79667360c0646509c79284b80e | v6.0.4 / #33 | run 29110056288 passed | complete |
| gnomad-link | release PR #33 | chore/release-6.0.4 | version/dependency metadata and docs build | 618 unit + 15 eval passed; 1 deselected | clear | #33 checks passed | 8f38d15cfc19df57a5d12f3dd18bd53591e67785 | v6.0.4 | run 29110056288 passed | complete |
| autopvs1-link | #41 / router #32 | fix/security-egress-production | pending | pending | pending | pending | pending | pending | pending | open |
| pubtator-link | #85 | fix/write-boundary | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | router #33 | feat/pubtator-write-boundary | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #31 | feat/untrusted-content-contract | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #36 | fix/transport-runtime-drift | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #35 | chore/phase5-tracker | pending | pending | pending | pending | pending | pending | pending | open |
| genefoundry-router | #3 | chore/close-stale-discovery | n/a | n/a | reviewed | pending | pending | n/a | reproduced | open |
| genereviews-link | #27 | feat/corpus-release-automation | pending | pending | pending | pending | pending | pending | pending | open |
| genereviews-link | #40 | feat/revision-variant-context | pending | pending | pending | pending | pending | pending | pending | open |
| genereviews-link | #49 | spike/hybrid-annotation | pending | pending | pending | pending | pending | pending | report | open |
| clinvar-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| gencc-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| hpo-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| hgnc-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| mgi-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| mondo-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| orphanet-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| mavedb-link | router #35 / Phase 5 | fix/ingest-artifact-hardening-2026-07-10 | pending | pending | pending | pending | pending | pending | pending | open |
| gnomad-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| gtex-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| hgnc-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| mgi-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| uniprot-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| clingen-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| gencc-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| litvar-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| stringdb-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| autopvs1-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| spliceailookup-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| genereviews-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| pubtator-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| clinvar-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| vep-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| panelapp-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| mondo-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| mavedb-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| hpo-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| metadome-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
| orphanet-link | FastMCP guard | fix/fastmcp-344-strict-host-origin | pending | pending | pending | pending | pending | pending | pending | open |
