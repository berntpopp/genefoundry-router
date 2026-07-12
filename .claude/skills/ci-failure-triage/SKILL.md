---
name: ci-failure-triage
description: Use when `make ci-local` fails locally or a GitHub Actions run reports a CI failure (format, lint, line-budget, typecheck, or test errors).
---

# CI Failure Triage

Follow `AGENTS.md` first.

## Workflow

1. Run `make ci-local` and read the log to see which sub-target failed. It typically chains `format-check lint-ci lint-loc typecheck test-fast test-integration`, but repos vary (e.g. `clinvar-link` has no `lint-loc`) — trust the actual recipe/CI log, not this list.
2. **Format** (`format-check`) — run `make format`, re-check.
3. **Lint** (`lint-ci`) — run `make lint-fix`; fix remaining findings by hand rather than blanket-ignoring.
4. **Line budget** (`lint-loc`, the per-file line budget in `scripts/check_file_size.py`, where present) — split the growing module along its seams; edit `.loc-allowlist` only with a tracked decomposition reason.
5. **Typecheck** (`typecheck`) — run `make typecheck`; fix the type, don't add broad `ignore_errors` / `# type: ignore`.
6. **Tests** (`test-fast` / `test-integration`) — fix code or test per the assertion; parser/data changes need fixture-backed coverage under `tests/`.
7. Re-run `make ci-local` until green before handoff.

## Common mistakes

- Silencing lint/typecheck instead of fixing the cause.
- Bumping `.loc-allowlist` to dodge a needed split.
- Committing with a red `ci-local`.
