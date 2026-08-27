# ADR 0002 — Use a runtime-neutral rule source

Date: 2026-08-27
Status: accepted

## Context

The original contract stored the project index in `CLAUDE.md` and made `AGENTS.md`
point to it. The content was shared, but the source name privileged one runtime and
made other agents surface that runtime's file as their project rule source.

Copying the index into multiple loader-specific files would remove the name leak but
reintroduce content drift. A neutral source needs thin runtime entry files while the
existing on-demand `docs/*.md` routing remains unchanged.

## Decision

1. `AI_RULES.md` is the only project rule index and keeps the existing 60-line target.
2. On Linux/WSL/POSIX repositories that preserve Git symlinks, `CLAUDE.md` and
   `AGENTS.md` are relative symlinks to `AI_RULES.md`.
3. Repositories that cannot preserve symlinks use two regular pointer files of at
   most 15 lines. Both point directly to `AI_RULES.md`; mixed modes fail verification.
4. The manifest records a real file's content hash, but a symlink's type and target.
5. The quiz receives `AI_RULES.md` once and never duplicates loader entry content.
6. The scanner reports broken rule-entry symlinks separately so update mode stops
   before treating a damaged rule set as a new project.

## Consequences

- Generated rule content is runtime-neutral while each loader keeps its expected
  filename.
- Windows-compatible repositories retain a portable mode without duplicating rules.
- Existing generated layouts are not silently migrated during an ordinary update;
  replacing their entry files requires explicit migration approval.
- Editors that replace symlinks with regular files cause a manifest conflict instead
  of silently changing the entry mode.
