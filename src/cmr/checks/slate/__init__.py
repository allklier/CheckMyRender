"""Slate checks (D27): slate.present, slate.consistency, slate.content, slate.claims,
slate.bars. Contract, config keys and comparison rules: src/cmr/checks/slate/CLAUDE.md.
Everything runs after the main pass on two decoded slate frames (no taps)."""
from . import present, content, claims, bars  # noqa: F401  (register the checks)
