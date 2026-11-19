# GeneFoundry Deploy Contract Standard v1

> Canonical reference for the GeneFoundry `-link` MCP fleet and the `genefoundry-router`
> aggregator. Adopted 2026-11-19. It fixes the shape of the one Compose file the fleet
> controller deploys, so a thin deploy tool can run any repository without per-repo
> knowledge. Hardening inside that file stays in
> [`CONTAINER-HARDENING-STANDARD-v1.md`](CONTAINER-HARDENING-STANDARD-v1.md); the existing
> deployed-overlay rules are in [`deployment.md`](deployment.md#fleet-compose-contract).

## Why

The 2026-11-19 thin-deploy run broke on five things, none of them a code bug:

- Several repositories had no top-level `name:`, so Compose named every project `docker`
  and the stacks collided.
- About a dozen single-service internal networks each took a Docker default address pool,
  until the host reported `could not find an available, non-overlapping IPv4 address pool`.
- Eight repositories deployed a stack layered from two or three files, so the deploy tool
  had to know each repository's file order.
- Image and data pins used per-repository variable names (three image naming schemes, five
  data schemes), so the deploy tool needed a lookup table per repository.
- mondo's `v0.3.x` tags were moved after release, so a tag no longer named one commit.

## The rules

| # | Rule | Why (the 2026-11-19 incident) |
|---|---|---|
| C1 | Exactly **one** deployed Compose file, listed explicitly as the single entry of `container-release.json` `service.deployed_compose_files`. No `!reset` / `!override` in it. | 3-file stacks: every deploy tool that forgets one file deploys a different stack. |
| C2 | That file has a literal top-level `name: <repository>`. | Project `docker` collision between repositories. |
| C3 | The application image is `image: ${GF_IMAGE:-${<OLD>_IMAGE:?…}}`; the old per-repository name stays a fallback for one release, then the form becomes `${GF_IMAGE:?…}`. | Per-repository pin names. |
| C4 | Data services read only `GF_DATA_RELEASE_TAG` and `GF_DATA_DIGEST` (the manifest's `data_requirements`); everything else is defaulted in Compose or derived in the init service. | Per-repository pin names (4–6 required data variables in some repos). |
| C5 | Every top-level volume has an explicit `name:`. A data volume is `name: ${GF_DATA_VOLUME:-<logical>-${GF_DATA_RELEASE_TAG}}`. A volume that already lives in production under a project-prefixed name is pinned to that **exact** name (table below), so no data moves. | Project collision and renaming: an unnamed volume follows the project name and comes up empty. |
| C6 | No project network unless at least two services share it. A single service joins only the external proxy network. A shared network declares `ipam.config[].subnet: ${GF_INTERNAL_SUBNET:-10.251.<n>.0/28}` from the registry below. External networks are unaffected. | Address-pool exhaustion. |
| C7 | A data service's `/health` reports `release_identity.data_identity.{expected,actual}` (extra keys allowed). | The deploy tool cannot confirm the data it activated otherwise. |
| C8 | Published `vX.Y.Z` tags are immutable: a repository ruleset blocks tag update and deletion. | Moved mondo tags. |

## Enforcement

`container_release.py validate-deployed-overlay` (shared release workflow, `prepare` job)
gates C1, C2, C3, C5 and C6 as the rules `single-deployed-file`, `deployed-file-set`,
`project-name`, `application-image`, `volume-name` and `internal-network`. A repository
gets them when it bumps its shared-workflow revision, so the rules never break a
repository before its migration PR.

**Not checked yet:** C4 (data pins) and C7 (`/health` data identity) arrive with the
data-repository wave; C8 is a repository setting, not a file check. C5 checks only that a
name is explicit, not that it equals the production name: compare against the table.

## Subnet registry (C6)

`10.251.<n>.0/28` per repository, `n` assigned alphabetically from 1 over the `-link`
repositories that ran two or more services on a project network on 2026-11-19 (a data-init
plus its server, or a database sidecar). `10.251.10.0/24` belongs to gene-curator and is
skipped; `10.250.0.0/16` is reserved for non-fleet stacks. Override per host with
`GF_INTERNAL_SUBNET`. A repository whose migration drops its shared network (an init that
only needs egress can join the proxy network instead) leaves its number unused; a new
repository takes the next free number. Numbers are never reassigned.

| n | Repository | Subnet |
|---|---|---|
| 1 | clinvar-link | `10.251.1.0/28` |
| 2 | gencc-link | `10.251.2.0/28` |
| 3 | genereviews-link | `10.251.3.0/28` |
| 4 | hgnc-link | `10.251.4.0/28` |
| 5 | hpo-link | `10.251.5.0/28` |
| 6 | mavedb-link | `10.251.6.0/28` |
| 7 | mgi-link | `10.251.7.0/28` |
| 8 | mitomap-link | `10.251.8.0/28` |
| 9 | mondo-link | `10.251.9.0/28` |
| — | *(gene-curator, `10.251.10.0/24`)* | — |
| 11 | pubtator-link | `10.251.11.0/28` |
| 12 | ucsc-link | `10.251.12.0/28` |

A `/28` holds 11 containers; ask for a wider block here before going past that.

## Production volume names (C5)

Names in use on the fleet host on 2026-11-19. Pin each one verbatim with `name:`; the
data-release ones become the `GF_DATA_VOLUME` default's current value.

| Repository | Volume name(s) to pin |
|---|---|
| genefoundry-router | `genefoundry-router_fastmcp_data` |
| clingen-link | `clingen_reference-data-clingen-9a97518362a6acda` |
| clinpgx-link | `clinpgx-data-core-d91c0c4082f14706-20261119` |
| clinvar-link | `clinvar-data-bundle-2026-11-09-r2` |
| gencc-link | `gencc-data-data-gencc-d6a5392d2700f14d` |
| genereviews-link | `genereview_pg_data-…`, `genereview_pg_run-…`, `genereview_model_data-corpus-data-2026-11-12-r1-app-v532` |
| gtex-link | `gtex-link-npm_gtex_link_scratch` |
| hgnc-link | `hgnc-data-data-hgnc-d1c34e1547c96761` |
| hpo-link | `hpo-reference-db-v2026-09-01-r3` |
| mavedb-link | `mavedb-data-data-2026-06-24-s4-r3-app-v0512` |
| metadome-link | `metadome-link-npm_metadome-data` |
| mgi-link | `mgi-data-data-mgi-5f11fe44b4ba9e20` |
| mitomap-link | `mitomap-reference-data-mitomap-a6646827e6157779` |
| mondo-link | `mondo-data-data-mondo-1ed8ac77ae72f843` |
| orphanet-link | `orphanet-data-r20260623T075350Z-r3-app-v062` |
| pubtator-link | `pubtator-link_pubtator_postgres_data`, `pubtator-link_pubtator_postgres_run` |
| ucsc-link | `ucsc-blat-hg38-kent-v479-r2` |

(The genereviews Postgres names end `-corpus-data-2026-11-12-r1-app-v532`.) Repositories
not listed mount no named volume in production today.

## Definition of Done (per repository)

1. `container-release.json` `service.deployed_compose_files` is one file; that file
   renders alone and passes `validate-deployed-overlay`.
2. Image, volume and network shapes match C3, C5, C6; volume names match the table.
3. Data repositories: C4 and C7 (tracked in the data wave).
4. The tag ruleset (C8) is on.
5. The old image/data variable names are removed one release after adoption.

Reference implementation: the router's `docker/docker-compose.npm.yml`.
