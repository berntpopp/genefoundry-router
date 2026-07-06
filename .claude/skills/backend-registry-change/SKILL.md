---
name: backend-registry-change
description: Use when adding, removing, enabling/disabling, or reconfiguring a backend in the router's servers.yaml registry (URLs, namespaces, entrypoints, server_name/transform overrides).
---

# Backend Registry Change

Follow `AGENTS.md` first. `servers.yaml` is the source of truth for the federated fleet; URLs/secrets live in `.env`.

## Workflow

1. Edit the backend's entry in `servers.yaml`. Keys per server: `name`, `repo`, `url_env` (the `.env` var holding the URL), `namespace`, `tags`, `entrypoints`; `enabled` defaults true via the `defaults` block. Add the URL var to `.env` / `.env.example`.
2. `entrypoints` seed the discovery catalog / golden tasks — set the common first-call tools for a new backend.
3. **Prefer namespacing over transforms.** The current fleet has **no** `transform` blocks (all backends adopted Tool-Naming v1). Use `server_name` only to reconcile a backend whose ratified `serverInfo.name` differs from its registry alias (e.g. `spliceai` → `spliceailookup-link`). If you must add a `transform`, delete it once the backend fixes naming upstream.
4. `make validate` (config schema) → `make list-tools` (namespaced, collision-free surface) → `make fleet-probe` (live reachability + non-zero tool harvest; fail loud on 0-tool backends).
5. `make snapshot-catalog` to regenerate the discovery catalog; keep the guard test green.
6. Never expose a backend directly — it stays behind the router / proxy. Run `make ci-local`.

## Common mistakes

- Using `url` instead of `url_env`, or forgetting to add the var to `.env`.
- Adding a `transform` to patch a naming issue the backend should fix upstream.
- Enabling a backend without `fleet-probe` — a 307 redirect or 0-tool backend passes CI but is dead in prod.
