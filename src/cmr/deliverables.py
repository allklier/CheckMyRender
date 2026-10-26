"""Deliverables (D27): CSV rows, filename parts, run-time tokens and row matching.

One CSV row per SPOT (never a filename column); variants (slated/unslated, HD/SD) are told
apart by a filename part. The row is selected from the ACTUAL filename:
  key             (A) a named group of `filename_regex` (= the `key` column name) selects
                      the row; Ad-ID keys are compared without a trailing H/D (key_type ad_id).
                      Composite key: `key: [ad_base, length]` = every listed column equals the
                      same-named filename group (e.g. `AB002` + `15` + `H` -> ad_base AB002,
                      length 15); types per part (key_parts()).
  generated_name  (B) the row whose `job.naming_pattern` (with {row.*}) renders to the
                      actual filename.
  both                key selects, the `filename` check verifies the generated name.
  fields          (C) fallback without a key: every `match_fields` group (default: the
                      regex groups that are also columns) equals the row value, loosely
                      compared; must select exactly ONE row, else ConfigError (use --row).
  --row VALUE     (D) override: `COLUMN=VALUE`, a key value, or `#N` (1-based data row).
Every row value comes from that ONE row.

Tokens `{ns.key}` are resolved per file at run time (D20 style; `${spec.*}` is load time,
D4): ns = row (selected row, canonical + normalised header names), file (named groups,
`stem`, `name`, `ext`), job (spec job section). Blank value = not specified; the literal
EMPTY ("<empty>") = must be empty.
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import log
from .config import ConfigError
from .textnorm import nfc

LOG = log.get("deliverables")

EMPTY = "<empty>"            # job/CSV marker: the slate field must be empty
TOKEN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+)*)\}")
_LOOSE = re.compile(r"[\s_\-:.]+")


def norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(h).strip().lower()).strip("_")


def blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


KEY_TYPES = ("ad_id", "text", "duration")


def norm_key(v: str, key_type: str = "text") -> str:
    """Key comparison: upper case, no spaces. ad_id: a trailing H/D (HD/SD suffix) is dropped
    from a 12-char Ad-ID (11 + H/D) and from a short house id (< 11 chars, e.g. `AB00215H`)
    when a digit precedes it; an 11-char Ad-ID is kept whole (its last character is part of
    the id). duration: seconds (`:15`, `15`, `15s` -> `15`), else as text."""
    s = re.sub(r"\s+", "", str(v)).upper()
    if key_type == "ad_id" and s[-1:] in ("H", "D") and (
            len(s) == 12 or (len(s) < 11 and s[-2:-1].isdigit())):
        s = s[:-1]
    elif key_type == "duration":
        from .checks.slate.compare import seconds
        sec = seconds(s)
        s = s if sec is None else str(sec)
    return s


def key_parts(cfg: dict) -> list[tuple[str, str]]:
    """[(column, key_type)] of `deliverables.key` (a column name or a list of them).
    key_type: `key_type` (a type for a single key; a mapping column -> type for any key),
    else by name: ad_id when the normalised name is ad_id/adid or starts with `ad_` (ad_base),
    duration for length/duration, else text."""
    key = (cfg or {}).get("key")
    if not key:
        return []
    cols = [key] if isinstance(key, str) else list(key)
    kt = (cfg or {}).get("key_type")

    def default(c):
        n = norm_header(c)
        if n in ("ad_id", "adid") or n.startswith("ad_"):
            return "ad_id"
        return "duration" if n in ("length", "duration") else "text"
    if isinstance(kt, str) and len(cols) > 1:
        raise ConfigError(f"deliverables.key_type {kt!r}: the key {cols} is composite; give a "
                          f"mapping column -> type")
    out = []
    for c in cols:
        t = kt.get(c) if isinstance(kt, dict) else kt
        if t is not None and t not in KEY_TYPES:
            raise ConfigError(f"deliverables.key_type {t!r} for {c!r}: one of {', '.join(KEY_TYPES)}")
        out.append((c, t or default(c)))
    return out


def loose(v) -> str:
    """Option C comparison: case-folded, separators (space _ - : .) removed."""
    return _LOOSE.sub("", str(v).casefold())


# --- tokens ---------------------------------------------------------------------------
class Unresolved(KeyError):
    """A token whose value is missing or blank (= not specified)."""


def lookup(ns: dict, ref: str):
    cur = ns
    for part in ref.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise Unresolved(ref)
    return cur


def render(expr, ns: dict):
    """Resolve `{ns.key}` tokens in a string. Returns None when the expression is blank or
    any token is missing/blank (not specified = not checked); returns EMPTY when the whole
    expression is EMPTY or a token resolves to EMPTY."""
    if expr is None:
        return None
    if not isinstance(expr, str):
        return str(expr)
    if blank(expr):
        return None
    if expr.strip() == EMPTY:
        return EMPTY

    def repl(m):
        v = lookup(ns, m.group(1))
        if blank(v) or isinstance(v, (dict, list)):
            raise Unresolved(m.group(1))
        if str(v).strip() == EMPTY:
            raise _Empty()
        return str(v)
    try:
        return TOKEN.sub(repl, expr)
    except Unresolved:
        return None
    except _Empty:
        return EMPTY


class _Empty(Exception):
    pass


# --- CSV ------------------------------------------------------------------------------
def load_rows(path, columns: dict | None = None) -> list[dict]:
    """Rows as {name: value}: every header under its normalised name, plus canonical names
    from `columns` {canonical: "CSV Header"}. Values are stripped strings."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            headers = nfc(list(reader.fieldnames or []))     # D32: cells compared in NFC
            raw = [{nfc(k): nfc(v) for k, v in r.items()} for r in reader]
    except OSError as e:
        raise ConfigError(f"cannot read deliverables CSV {path}: {e}") from e
    missing = [h for h in (columns or {}).values() if h not in headers]
    if missing:
        raise ConfigError(f"deliverables CSV {path}: column(s) {missing} not in header {headers}")
    rows = []
    for r in raw:
        row = {norm_header(k): (v or "").strip() for k, v in r.items() if k is not None}
        for canon, header in (columns or {}).items():
            row[canon] = (r.get(header) or "").strip()
        if any(v for v in row.values()):
            rows.append(row)
    LOG.debug("CSV %s: %d row(s), columns %s", log.redact("path", path), len(rows), headers)
    return rows


# --- filename -------------------------------------------------------------------------
def file_tokens(name: str, regex: str | None) -> tuple[dict, bool]:
    """{stem, name, ext} + named groups of `regex` (fullmatch on the file name).
    Returns (tokens, matched); matched is True when no regex is configured."""
    p = Path(nfc(name))
    tok = {"name": p.name, "stem": p.stem, "ext": p.suffix.lstrip(".")}
    if not regex:
        return tok, True
    try:
        m = re.fullmatch(regex, p.name)
    except re.error as e:
        raise ConfigError(f"deliverables.filename_regex: {e}") from e
    if not m:
        return tok, False
    tok.update({k: v for k, v in m.groupdict().items() if v is not None})
    return tok, True


def render_name(pattern: str, ns: dict, tags: dict | None = None) -> str:
    """D20 naming_pattern with namespaced tokens. Bare `{name}` = job field, then profile tag
    (as before); `{row.x}`, `{file.x}`, `{job.x}` from ns. Missing -> KeyError."""
    from .checks.filename import _tag   # tag mapping lives with the filename check

    def repl(m):
        ref = m.group(1)
        if "." in ref:
            try:
                v = lookup(ns, ref)
            except Unresolved:
                raise KeyError(f"placeholder {{{ref}}} has no value") from None
            if blank(v) or isinstance(v, (dict, list)):
                raise KeyError(f"placeholder {{{ref}}} is blank")
            return str(v)
        job = ns.get("job") or {}
        if ref in job and not isinstance(job[ref], (dict, list)):
            return str(job[ref])
        if tags and ref in tags:
            return _tag(ref, tags[ref])
        raise KeyError(f"placeholder {{{ref}}} not in spec job fields or profile tags")
    return TOKEN.sub(repl, pattern)


# --- row selection --------------------------------------------------------------------
@dataclass
class Selection:
    row: dict | None                  # selected row (None = no row)
    number: int | None                # 1-based data row number
    how: str                          # key | generated_name | both | fields | override | none
    file: dict                        # file tokens
    problems: list[str] = field(default_factory=list)   # file-level problems (FAIL)

    def ns(self, job: dict) -> dict:
        return {"row": self.row or {}, "file": self.file, "job": job or {}}

    def describe(self) -> str:
        return f"row {self.number} (by {self.how})" if self.row else "no row"


def _one(rows: list[tuple[int, dict]], what: str) -> tuple[int, dict]:
    if len(rows) > 1:
        nums = ", ".join(str(n) for n, _ in rows)
        raise ConfigError(f"{what} matches {len(rows)} deliverables rows ({nums}); "
                          f"select one with --row")
    return rows[0]


def _override(rows, value: str, parts: list[tuple[str, str]]):
    if value.startswith("#") and value[1:].isdigit():
        n = int(value[1:])
        if not 1 <= n <= len(rows):
            raise ConfigError(f"--row {value}: the CSV has {len(rows)} data rows")
        return n, rows[n - 1]
    if "=" in value:
        col, _, want = value.partition("=")
        col = norm_header(col)
        if rows and col not in rows[0]:
            raise ConfigError(f"--row {value}: no column {col!r} (have {sorted(rows[0])})")
        hits = [(i, r) for i, r in enumerate(rows, 1) if loose(r.get(col, "")) == loose(want)]
    else:
        if not parts:
            raise ConfigError(f"--row {value}: no deliverables.key configured; use COLUMN=VALUE or #N")
        if len(parts) > 1:
            raise ConfigError(f"--row {value}: the key is composite ({', '.join(c for c, _ in parts)}); "
                              f"use COLUMN=VALUE or #N")
        key, key_type = parts[0]
        hits = [(i, r) for i, r in enumerate(rows, 1)
                if norm_key(r.get(key, ""), key_type) == norm_key(value, key_type)]
    if not hits:
        raise ConfigError(f"--row {value}: no deliverables row matches")
    return _one(hits, f"--row {value}")


def select(cfg: dict, job: dict, filename: str, tags: dict | None = None) -> Selection:
    """Select the deliverables row for `filename` (see module doc). ConfigError for an
    ambiguous match, a C fallback that does not select exactly one row, or bad config."""
    try:
        sel = _select(cfg, job, filename, tags)
    except ConfigError as e:
        LOG.warning("row selection for %s failed: %s", log.redact("path", filename),
                    log.redact_quoted(str(e)))
        raise
    LOG.info("row selection for %s: %s; filename groups %s; %d problem(s)",
             log.redact("path", filename), sel.describe(), sorted(sel.file), len(sel.problems))
    if LOG.isEnabledFor(logging.DEBUG):
        LOG.debug("selected row values: %s", {k: log.redact("row", v) for k, v in (sel.row or {}).items()})
        for p in sel.problems:
            LOG.debug("row selection problem: %s", log.redact_quoted(p))
    return sel


def _select(cfg: dict, job: dict, filename: str, tags: dict | None = None) -> Selection:
    cfg = cfg or {}
    if not cfg.get("csv"):
        raise ConfigError("deliverables.csv is not set (job file or --deliverables)")
    rows = load_rows(cfg["csv"], cfg.get("columns"))
    parts = key_parts(cfg)
    tok, matched = file_tokens(filename, cfg.get("filename_regex"))
    mode = cfg.get("match") or ("key" if parts else "fields")
    if cfg.get("row"):
        n, r = _override(rows, str(cfg["row"]), parts)
        return Selection(r, n, "override", tok)
    sel = Selection(None, None, mode, tok)
    if not matched:
        sel.problems.append(f"filename {filename!r} does not match deliverables.filename_regex "
                            f"{cfg['filename_regex']!r}")
    if mode in ("key", "both"):
        if not parts:
            raise ConfigError(f"deliverables.match {mode!r} needs deliverables.key")
        if not matched:
            return sel
        groups = set(re.compile(cfg["filename_regex"]).groupindex) if cfg.get("filename_regex") else set()
        missing = [c for c, _ in parts if c not in groups]
        if missing:
            raise ConfigError(f"deliverables.filename_regex has no named group(s) {missing} "
                              f"(the key column(s))")
        # an optional group that did not match (AB003H has no length part) is not compared;
        # the remaining parts must then select one row
        used = [(c, t) for c, t in parts if c in tok]
        if not used:
            sel.problems.append(f"the filename has none of the key parts {[c for c, _ in parts]}")
            return sel
        hits = [(i, r) for i, r in enumerate(rows, 1)
                if all(norm_key(r.get(c, ""), t) == norm_key(tok[c], t) for c, t in used)]
        what = " + ".join(f"{c} {tok[c]!r}" for c, _ in used)
        if not hits:
            sel.problems.append(f"no deliverables row with {what} (from the filename)")
            return sel
        sel.number, sel.row = _one(hits, what)
        return sel
    if mode == "generated_name":
        pattern = (job or {}).get("naming_pattern")
        if not pattern:
            raise ConfigError("deliverables.match generated_name needs job.naming_pattern")
        hits = []
        for i, r in enumerate(rows, 1):
            try:
                if render_name(pattern, {"row": r, "file": tok, "job": job or {}}, tags) == filename:
                    hits.append((i, r))
            except KeyError:
                continue   # this row lacks a value the pattern needs
        if not hits:
            sel.problems.append(f"no deliverables row generates the filename {filename!r} "
                                f"(job.naming_pattern {pattern!r})")
            return sel
        sel.number, sel.row = _one(hits, f"filename {filename!r}")
        return sel
    # fields (C)
    fields = cfg.get("match_fields") or [k for k in tok if k not in ("name", "stem", "ext")
                                         and rows and k in rows[0]]
    if not matched or not fields:
        raise ConfigError(f"cannot select a deliverables row for {filename!r}: no key and no "
                          f"filename fields to match (filename_regex groups); use --row")
    miss = [f for f in fields if f not in tok]
    if miss:
        raise ConfigError(f"deliverables.match_fields {miss} are not filename_regex groups")
    hits = [(i, r) for i, r in enumerate(rows, 1)
            if all(loose(r.get(f, "")) == loose(tok[f]) for f in fields)]
    if len(hits) != 1:
        raise ConfigError(f"filename fields {', '.join(f'{f}={tok[f]!r}' for f in fields)} match "
                          f"{len(hits)} deliverables rows (need exactly one); select one with --row")
    sel.number, sel.row = hits[0]
    return sel
