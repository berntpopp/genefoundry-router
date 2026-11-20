# Shared rubric — cancer/somatic resource assessment for GeneFoundry `-link` fleet

Context: GeneFoundry = a thin FastMCP router (/home/bernt-popp/development/genefoundry-router) federating
26 read-only `-link` MCP backends (siblings at /home/bernt-popp/development/<name>-link). Fleet has ZERO
cancer/somatic coverage today. Existing join keys: HGNC symbol/ID (hgnc), HGVS + VEP consequence/MANE (vep),
gnomAD variant id chr-pos-ref-alt (gnomad), ClinVar VCV/RCV (clinvar), MONDO disease IDs (mondo), UniProt.
Newest template repos to clone: ucsc-link, pgscatalog-link (live-API, no snapshot). Snapshot-pattern repos: clingen-link, hpo-link, orphanet-link.

Hard fleet constraints (from docs/*-STANDARD-v1.md in the router repo):
- Backends are UNAUTHENTICATED behind the router; the router is publicly listed in the MCP Registry and
  serves anyone with router auth. => a backend may only hold a *service* credential, never per-user tokens.
  Any upstream license that forbids redistribution / third-party access / commercial use / "derived databases"
  is a blocker or forces a self-host-only/disabled-by-default deployment. Research-use-only fleet.
- Tool-Naming v1.1: unprefixed `verb_noun` tool names, ≤50 chars, canonical verbs (get/search/resolve/list...).
- Tool-Surface Budget: ≤1,200 tokens per tool def, ≤10,000 tokens per server (so ~6-12 tools).
- Response-Envelope v1.1: honest total/truncated, untrusted-text fencing, errors name the parameter, isError.
- Outbound HTTP policy: allowlisted upstream hosts, ≤5 redirects, decoded-byte caps.
- Container hardening, digest-pinned images, data snapshots must be immutable/versioned (data_version stamp).
- Fleet param canon: `response_mode` minimal|compact|standard|full (default compact).

For EACH resource assigned to you, produce (be concrete, verify live, cite sources):
1. What it is: content scope, size, curation model, update cadence, current version/release.
2. Access: API type (REST/GraphQL/bulk/none), base URL, auth needed?, rate limits. ACTUALLY PROBE it with
   `curl -s -m 20` (a real query for BRAF V600E and/or TP53/KRAS G12D) and report the HTTP status + a trimmed
   response shape. Note quirks (unknown-ID behaviour, pagination, max page size).
3. License / Terms of Use: exact license (CC0, CC BY, CC BY-NC, custom), quote the decisive clause on
   redistribution / API re-exposure / commercial use / registration. Verdict on compatibility with a public,
   research-use, unauthenticated-backend-behind-router deployment.
4. Fleet fit: live-proxy vs snapshot pattern; proposed 5-9 tools with v1.1 names + one-line purpose each;
   entry-point resolver; join keys to existing fleet backends; overlap with other candidates.
5. Scientific value: what agent questions this uniquely answers (e.g. "is BRAF V600E actionable in melanoma,
   what level of evidence"). Use the GeneFoundry pubtator MCP tool (load via ToolSearch
   "select:mcp__claude_ai_GeneFoundry__pubtator_search_literature,mcp__claude_ai_GeneFoundry__search_tools,mcp__claude_ai_GeneFoundry__call_tool")
   to find the resource's canonical citation + latest database-issue paper (PMID, year). Web search too.
6. Verdict: BUILD new `<name>-link` / EXTEND existing link / ROUTER-SKILL only / PARK (license/access) / SKIP,
   with priority P0-P3, effort S/M/L, key risks.

Browser: prefer curl/WebFetch/WebSearch. Use claude-in-chrome ONLY if a site blocks automated fetch
(Cloudflare/JS); if so, open your OWN new tab, never reuse tabs, close it when done, no dialogs.
Do NOT modify any repo. Write your full report to the file path given in your task (markdown), then
return a compact summary (≤40 lines): per resource the verdict line + top 3 facts + license verdict.
