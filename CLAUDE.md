# CLAUDE.md

@AGENTS.md

Claude Code entrypoint only:

- Use `AGENTS.md` for shared repository instructions.
- Keep Claude-specific additions here short and tool-specific.
- Prefer `make ci-local` before final handoff (runs `lint-loc`, the 600-LOC budget).
- FastMCP 3.x symbols are post-training-cutoff and fast-moving — verify imports
  against the installed package before relying on them (see the import smoke in
  `docs/plans/2026-06-13-genefoundry-router-implementation.md`, Task 2).
- When a backend's source repo adopts Tool-Naming Standard v1, delete its
  `transform` block from `servers.yaml` rather than adding router-side workarounds.
