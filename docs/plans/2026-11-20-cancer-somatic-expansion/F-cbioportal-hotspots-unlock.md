# F — cBioPortal API use + Cancer Hotspots data license (unlock check)

Date: 2026-11-20. Method: curl (UA `genefoundry-research-client/0.1`), `gh api`/`gh search`, WebFetch, Europe PMC REST. No emails sent.

---

## Q1. Does cBioPortal permit programmatic use of its public API by third-party tools? — **GO-with-conditions**

### Decisive evidence: the official `llms.txt` (an explicit policy for scripts and agents)

Live at `https://www.cbioportal.org/llms.txt` (HTTP 200, `content-type: text/markdown`, fetched 2026-11-20). Source:
`cBioPortal/cbioportal-go:ops/cbioportal.org/llms.txt`, added in commit `708f329f`, 2026-10-08, "cbioportal.org: serve www's llms.txt". Verbatim:

> "Please don't render it page by page or loop over the web API to collect data in bulk: every dataset shown on the site can be downloaded directly, and that is much faster for you and much cheaper for us."

> "## Programmatic access (small, targeted queries)
> - cBioPortal MCP server for AI assistants: `https://mcp.cbioportal.org/db/mcp/` (OAuth sign-in). It answers questions straight from the cBioPortal database and is the best option for agents.
> - REST API docs: use `https://api.cbioportal.org` for scripted access. Prefer filtered requests (specific genes and samples) over whole-study pulls, run them sequentially, and cache results locally. Clients that send many concurrent bulk requests are rate-limited (HTTP 429)."

> "## Bulk data (preferred for large or repeated access)
> - cBioPortal public database on Hugging Face (huggingface.co/datasets/cBioPortal/publicDatabase): weekly Parquet export of the whole public portal database ...
> - cBioPortal Datahub on GitHub: the source study files ... for every public study.
> - Per-study downloads: `https://datahub.assets.cbioportal.org/<studyId>.tar.gz`"

So scripted and agent use is sanctioned, with stated conditions: use **`api.cbioportal.org`**, make small filtered requests, run them **sequentially** (no concurrency), **cache locally**, and use bulk exports for anything large or repeated. The same file also says cBioPortal runs its **own official MCP server** (OAuth). That overlaps with `cbioportal-link`, so our backend should add something that server doesn't (a federated fleet, no OAuth needed), not just copy it.

### Host split: api.cbioportal.org is the dedicated host for scripts and agents

`cBioPortal/cbioportal-go#18` ("Roll out go to www.cbioportal.org", inodb, 2026-11-06):
> "go has served api.cbioportal.org, and www.cbioportal.org's API traffic from scripts and agents, since 2026-11-06 (#15, release v0.1.1)."
> Table: "api.cbioportal.org (done) | Traffic: scripts and agents, read only"
> Gate: "www's routes keep their rate limits, IP blocks and crawler rules when they point at go."

`cbioportal-go#17` (2026-11-06) puts non-browser API traffic at "~800k–1M" requests a day, which includes "scripts and AI agents". Heavy programmatic use is normal and budgeted for.

### The 2026-11-14 "bot detection" change only affects HTML pages

- Commit `cBioPortal/cbioportal-go@bb36f33` (Ino de Bruijn, 2026-11-14T16:39Z). This is the commit the live `/api/info` reports as `gitCommitId`. Message:
  > "parity: compare HTML pages without the bot-detection script Cloudflare adds — Cloudflare added its challenge-platform script to go.cbioportal.org's Swagger UI page on 2026-11-14; it differs per request, so the pages never matched."
  It changes only parity-diff tooling (`docs/rewrite/parity.md`, `rewrite/java-v7-to-go/paritydiff/*`). It is not a cBioPortal policy change. Cloudflare's passive JS-detection script (`/cdn-cgi/challenge-platform/scripts/jsd/main.js`) is injected into HTML responses.
- `cbioportal-go#18`, comment by inodb on 2026-11-16: "go.cbioportal.org now has Cloudflare's bot check, as www does."
- My live test confirms this. `www.cbioportal.org/api/swagger-ui/index.html` and `go.cbioportal.org/api/swagger-ui/index.html` both contain the `challenge-platform/scripts/jsd/main.js?ray=…` snippet. The JSON endpoints are unaffected: no challenge, plain 200 JSON.

### Rate limiting: proxy level only, no published numbers

- `cBioPortal/cbioportal#11938` (2026-02-05): users got nginx `429 Too Many Requests` on `/api/*` for several days. inodb: "We were getting overloaded by requests, still working to fix it". alisman (2026-02-10): one client was "making mutation requests with an erroneous gene param ... returning mutations for ALL genes"; they added validation (PR #11946 made `entrezGeneId` required).
- `cBioPortal/cbioportal#11980` (feature request for app-level rate limiting) was closed. alisman, 2026-09-21: "We've decided not impose rate limiting at the app level".
- So limits live at the edge (Cloudflare/Traefik/nginx: "rate limits, IP blocks and crawler rules", #18), and llms.txt says concurrent bulk clients get 429. No numbers are published, and responses carry no `RateLimit-*`/`Retry-After` headers.

### Official docs and clients (programmatic use is sanctioned)

- `https://docs.cbioportal.org/web-api-and-clients/` (source `cBioPortal/documentation:src/web-API-and-Clients.md`): "cBioPortal provides a REST API for programmatic access to the data." It lists cBioPortalData (Bioconductor), cbioportalR, and a Python bravado client, and says "Do reach out if you'd like us to add a language." The page has no rate-limit, fair-use or terms text, and it still points to `www.cbioportal.org/api`, not `api.cbioportal.org`.
- FAQ (`src/user-guide/faq.md`): "Does the cBioPortal provide a Web Service API? ... Yes".
- Org repos: `cBioPortal/codebook` (API notebooks), `cBioPortal/2020-cbioportal-r-workshop` ("Webinar 5: API & R Client"), `cBioPortal/cbioportal-mcp` (Apache-2.0, ClickHouse-backed MCP, "under construction"), `cbioportal-navigator`, `cbioportal-mcp-qa`.

### robots.txt

`www.` and `api.cbioportal.org/robots.txt` both return `User-agent: * / Disallow: /`. robots.txt is aimed at crawlers. The go server code links robots.txt to llms.txt (`internal/api/portal/status.go`: robots "pointing to /llms.txt when llms_txt.location is set"), and llms.txt is the explicit policy for API clients. A targeted query proxy doesn't crawl, but it must stay out of the HTML pages.

### Live test (2026-11-20, from a German IP, Cloudflare TXL)

| Request | Status | Headers of note |
|---|---|---|
| `GET www.cbioportal.org/api/info` | 200 JSON | `server: cloudflare`, `cf-cache-status: DYNAMIC`, `cf-ray`; no rate-limit headers |
| `GET www.cbioportal.org/api/studies?pageSize=1` | 200 JSON | same |
| 10× `/api/studies?pageSize=1`, 3 s apart | 10/10 × 200, 0.16–0.20 s | `cf-cache-status: DYNAMIC` ×10; no `RateLimit`/`Retry-After`/`cf-mitigated` |
| `GET api.cbioportal.org/api/info`, `/api/studies?pageSize=1` | 200 JSON | same |
| default curl UA and `python-requests` UA | 200 | no UA blocking |

`/api/info` reports `portalVersion v7.2.3`, `goRewriteVersion v0.1.3`, `dbVersion 3.1.0`. API traffic is now served by the Go rewrite (`cBioPortal/cbioportal-go`, Apache-2.0, created 2026-10-02, "Experimental ... Not an official cBioPortal release", but it serves production API traffic).

### Bulk alternatives

- `cBioPortal/datahub` holds study files tracked with **git-LFS**. `.gitattributes` routes `data*.txt`, `*.seg`, `*.tar.gz` to LFS, and `.lfsconfig` excludes LFS by default; the backend is `knowledgesystems/git-lfs-s3`. The README has no GitHub license file but says: "The data are available under the ODC Open Database License (ODbL) ... attribute any public use ... keep the resulting data-sets open ... under the same ODbL license." It also adds a TCGA caveat ("Broad Institute GDAC TCGA Analysis Pipeline License ... preliminary data").
- Hugging Face `cBioPortal/publicDatabase`: weekly Parquet export of the full DB, last modified 2026-11-18. The card is tagged `license: agpl-3.0`, which conflicts with ODbL. That tag is probably copied from the software license; flag it, don't rely on it.
- `datahub.assets.cbioportal.org/<studyId>.tar.gz` gives per-study tarballs.

### Conditions for `cbioportal-link`

1. Use `https://api.cbioportal.org` as the base URL.
2. One request at a time per backend (a global semaphore of 1). Cache responses, since data changes weekly at most and `/api/info` `dbVersion` can serve as the cache key. Back off on 429.
3. Small, filtered tools only: require a gene/sample/study filter, with no whole-study or all-genes pulls (the #11938 incident).
4. Send a UA that identifies the service.
5. Never fetch HTML pages.
6. Pass through per-study ODbL attribution and citations in the envelope.
7. Optional: tell the cBioPortal team (`info@cbioportal.org`, or the Google group) about the shared gateway once it's live. Their policy doesn't require it.

---

## Q2. What license governs Cancer Hotspots DATA? — **GO-with-conditions (ODbL 1.0)**

### Decisive evidence: cancerhotspots.org states the data license explicitly

`cBioPortal/cancerhotspots:webapp/src/main/resources/static/index.html`, added in commit **`b257eeaf`** (2016-07-21, "added data license (ODbL) information"). It is still on master and live on `https://www.cancerhotspots.org/` (fetched 2026-11-20). About page, verbatim:

> "The data are available under the ODC Open Database License (ODbL) (summary available here): you are free to share and modify the data so long as you attribute any public use of the database, or works produced from the database; keep the resulting data-sets open; and offer your shared or adapted version of the data-set under the same ODbL license."

The site footer also links "ODC Open Database License (ODbL)" → `http://opendatacommons.org/licenses/odbl/1.0/`.

The repo's AGPL-3.0 `LICENSE` covers the code. The data files are in the same repo (`webapp/src/main/resources/data/v2_multi_type_residue.txt`, `v3_multi_type_residue.txt`, `3d_hotspots.txt`, `static/files/hotspots_v2.xls`, `hotspots_v3.xlsx`), and the site's own statement sets ODbL for the data. That matches cBioPortal's datahub license.

### Republished downstream under the same ODbL

- **Genome Nexus** (`genome-nexus/genome-nexus`, MIT code): `model/.../HotspotAnnotation.java` hard-codes `this.license = "https://opendatacommons.org/licenses/odbl/1.0/";`, so every hotspot annotation in the API response carries the ODbL license. The live endpoint `https://www.genomenexus.org/cancer_hotspots/hgvs/7:g.140453136A%3ET` returns BRAF V600 (v2 + 3d), HTTP 200.
- **genome-nexus-importer** (MIT code) ships a combined, HGNC-normalised, GRCh38-lifted `hotspots_v2_v3_and_3d.txt` (`data/common_input/hotspots/README.md`, pinned upstream commits). This is a ready-made **build-agnostic GRCh37/GRCh38 table** we could ingest, still under ODbL.
- **OpenCRAVAT** `cancer_hotspots` module v1.2.1 (store manifest): `commercial_warning: null`, cites Chang et al. Cancer Discovery 2017, hg38, "datasource: December 2017". No separate license field.
- I found no permissive (CC0/CC-BY) re-licensing anywhere. ODbL is the most permissive license it carries, and it applies everywhere.

### Papers (these licenses cover the articles, not the database)

| Source | PMID | OA / license (Europe PMC) |
|---|---|---|
| Chang et al. 2016, Nat Biotechnol, v1 hotspots | 26619011 | OA (PMC4744099, author manuscript), no CC license |
| Chang et al. 2018, Cancer Discov, v2 hotspots (1,165) | 29247016 | not OA (PMC5809279 author manuscript), **not CC-BY** |
| Gao et al. 2017, Genome Med, 3D hotspots | 28115009 | CC BY |
| Bandlamudi et al. 2026, Cancer Cell, v3 (+164 hotspots) | 41895280 | CC BY-NC-ND |

The supplementary tables in Cancer Discovery are not CC-BY, and Bandlamudi 2026 is CC BY-NC-ND. So **take the data from cancerhotspots.org or Genome Nexus (ODbL), not from paper supplements**.

3dhotspots.org returns 200 but the static HTML has no license text. Treat it as the same MSK project and use the 3D set as shipped in the cancerhotspots repo under ODbL.

### Conclusion: can we redistribute the hotspot table in a public research-use service?

**Yes, under ODbL 1.0, with these conditions:**
1. **Attribution** in every tool response or envelope and in the README: "Cancer Hotspots (cancerhotspots.org), Memorial Sloan Kettering Cancer Center; data © MSKCC, licensed under ODbL 1.0", plus a link to the license. Cite Chang et al. 2016 (Nat Biotechnol, PMID 26619011), Chang et al. 2018 (Cancer Discov, PMID 29247016), Gao et al. 2017 (Genome Med, PMID 28115009) for 3D, and Bandlamudi et al. 2026 (Cancer Cell, PMID 41895280) for v3.
2. **Share-alike.** Any derived database (our normalised or lifted table) must be offered under ODbL. Publish it or make it downloadable with its build script, for example by committing the derived TSV to the repo's `data/` with an ODbL notice. Query results are a "Produced Work" and only need the attribution notice.
3. **Keep it open.** No technical lock-down of the derived database. ODbL §4.7 allows a parallel unrestricted copy if we ever put it behind access controls.
4. The software is a separate matter: don't vendor AGPL code from `cBioPortal/cancerhotspots`. Re-implementing the lookup ourselves is clean.
5. Source the data from cancerhotspots.org or genome-nexus-importer, not from paper supplements (Cancer Discov is not CC-BY; Bandlamudi is NC-ND).
