"""Comparison rules for OCR'd slate values (spike: docs/slate-ocr-spike.md).

- Every type: case-folded, runs of whitespace collapsed, ends stripped.
- title: separators `_`, space and `-` are one class (tesseract turns `_` into a space on
  compressed frames), then the confusable classes.
- id: spaces removed, then the confusable classes.
- duration: leading `:` and spaces dropped (`:60` is often read `60`), then normalised to
  seconds when it parses (`:15`, `15`, `15s`, `15 sec`, `1:00` -> `60`); `760` for `:60`
  comes with a low confidence and is handled as an uncertain read by the caller.
- date: digit groups, one-digit groups zero-padded (`5/7/2020` = `05/07/2020`), then digits only.
- text / audio: case and spacing only (no confusable classes).
Confusable classes (same glyph in slate fonts, no OCR can tell them apart): {0,O}, {1,I,l,|},
{5,S}, {2,Z}, {8,B}. A value equal only under the classes is `confusable` (reported INFO with
the positions), never a mismatch.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

TYPES = ("text", "title", "id", "duration", "date", "audio")
_CLASS = {"0": "0", "O": "0", "o": "0", "1": "1", "I": "1", "l": "1", "|": "1", "i": "1",
          "5": "5", "S": "5", "s": "5", "2": "2", "Z": "2", "z": "2", "8": "8", "B": "8"}
_CONFUSABLE_TYPES = ("title", "id")


def canon(s: str, ftype: str = "text") -> str:
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if ftype == "title":
        s = re.sub(r"[_\s-]+", " ", s).strip(" ")
    elif ftype == "id":
        s = re.sub(r"\s+", "", s)
    elif ftype == "duration":
        s = re.sub(r"\s+", "", s).lstrip(":")
        sec = seconds(s)
        s = s if sec is None else str(sec)
    elif ftype == "date":
        s = "".join(g.zfill(2) for g in re.findall(r"\d+", s))
    return s.casefold()


def seconds(text: str) -> int | None:
    """Duration text in seconds: `:60`, `60`, `60s`, `60 sec`, `1:00`; None if not a duration."""
    s = re.sub(r"\s+", "", str(text or "")).lstrip(":").casefold()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,3})(?:s|sec|secs)?\.?", s)
    if not m:
        return None
    return int(m.group(1) or 0) * 60 + int(m.group(2))


def _classes(s: str) -> str:
    return "".join(_CLASS.get(c, c) for c in s)


# Slate date formats: name -> pattern. YYYY/YY/MM/DD = exactly that many digits, M/D = 1 or 2
# digits (M/D/YYYY: `5/7/2020` and `05/07/2020`); everything else is literal.
DATE_FORMATS = ("YYYYMMDD", "YYYY-MM-DD", "MM/DD/YYYY", "M/D/YYYY", "DD.MM.YYYY", "MM/DD/YY",
                "M/D/YY")
_DATE_TOKENS = {"YYYY": (r"(?P<y>\d{4})", 4), "YY": (r"(?P<y>\d{2})", 2), "MM": (r"(?P<m>\d{2})", 0),
                "DD": (r"(?P<d>\d{2})", 0), "M": (r"(?P<m>\d{1,2})", 0), "D": (r"(?P<d>\d{1,2})", 0)}


def parse_date(text: str, fmt: str) -> datetime.date | None:
    """Date in one of DATE_FORMATS (whitespace ignored), None if it does not parse."""
    rx, ylen = "", 4
    for tok in re.findall(r"YYYY|YY|MM|DD|M|D|.", fmt):
        if tok in _DATE_TOKENS:
            rx += _DATE_TOKENS[tok][0]
            ylen = _DATE_TOKENS[tok][1] or ylen
        else:
            rx += re.escape(tok)
    m = re.fullmatch(rx, re.sub(r"\s+", "", str(text or "")))
    if not m:
        return None
    y = int(m["y"]) + (2000 if ylen == 2 else 0)
    try:
        return datetime.date(y, int(m["m"]), int(m["d"]))
    except ValueError:
        return None


@dataclass
class Result:
    status: str                      # match | confusable | mismatch
    positions: list[int] = field(default_factory=list)   # 1-based, in the read value

    @property
    def ok(self) -> bool:
        return self.status != "mismatch"


def compare(expected: str, read: str, ftype: str = "text") -> Result:
    a, b = canon(expected, ftype), canon(read, ftype)
    if a == b:
        return Result("match")
    if ftype in _CONFUSABLE_TYPES:
        # classes are applied before case folding, so `l` (=1) and `L` stay different
        ca = canon(_classes(re.sub(r"\s+", " ", str(expected or ""))), ftype)
        cb = canon(_classes(re.sub(r"\s+", " ", str(read or ""))), ftype)
        if ca == cb and len(a) == len(b):
            return Result("confusable", [i + 1 for i, (x, y) in enumerate(zip(a, b)) if x != y])
    return Result("mismatch")
