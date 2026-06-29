# Changelog

## 0.1.0 — 2026-06-29

- Initial standalone release.
- `spec-audit` was extracted from a private internal codebase where it was
  originally developed and dogfooded against a real multi-file research
  codebase before being published as an independent, general-purpose tool.
- No code, data, or business logic from that private system is included
  here. The demo under `demo/strategy_repo/` is entirely synthetic and was
  written specifically for this public release.
- Detection rules included: `IDENTICAL_SYMBOL_DUPLICATION`, `INLINE_OVERRIDE`.
