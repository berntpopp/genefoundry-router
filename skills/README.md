# Research workflow skills

This folder holds **research workflow skills** in the [agentskills.io](https://agentskills.io)
format (`<name>/SKILL.md`, `name` + `description` frontmatter). The router will serve them to MCP
clients as `skill://<name>/SKILL.md` resources (MCP Skills, SEP-2640) via FastMCP's
`SkillsDirectoryProvider`. First skill: `somatic-variant-interpretation/` (cancer/somatic workflow).

These are **product content** for agents using the GeneFoundry fleet (e.g. how to chain tools for
a variant review). Developer instructions for working on this repo live in `.claude/skills/`
(mirrored at `.agents/skills`), not here.

Research use only. Not clinical decision support; skills must mirror the backends' disclaimers.
