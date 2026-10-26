"""OCR adapter (tesseract CLI) and label-keyed row pairing.

Engine (decided after the spike, docs/slate-ocr-spike.md): tesseract 5.5.2 via subprocess,
`tesseract IMAGE stdout --psm 3 -l eng tsv`, env OMP_THREAD_LIMIT=1, no preprocessing.
Options verified on the production machine by the spike (`tesseract --help`, `--help-psm`,
`--help-extra`: `stdout` output base, `--psm`, `-l`; `tsv` config gives the 12 TSV columns,
level 5 = word); OMP_THREAD_LIMIT verified by effect only (spike "Throughput").
Binary: profile param `tesseract`, else env CMR_TESSERACT, else `tesseract` on PATH.

pair(): port of the spike's slate_ocr.py pair(): words -> lines (vertical centre) ->
segments (split at gaps > 1.5 x line height) -> label segments (template aliases) that share
the right-aligned label column -> value = the segment on the label's line that starts on
the value column (median left edge). Countdown, logo or anything else is ignored because it
does not start on the value column (and words centred in the template's countdown box are
dropped first). Not handled (spike): wrapped values, centred values.
pair_colon() (template `layout: label_colon`): `Label: value` slates with left-aligned labels
ending in a colon, value after the colon on the same segment (`Ad-ID: X`) or in the next
segment of the same line (tab-stop column). See pair_colon().
"""
from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from cmr import log
from .template import Template, norm_label

LOG = log.get("slate")

ENV_BIN = "CMR_TESSERACT"
JUNK_CONF = 50          # spike: drop words without a letter/digit below this confidence


class OCRUnavailable(RuntimeError):
    pass


@dataclass
class Word:
    text: str
    conf: float
    x: int
    y: int
    w: int
    h: int

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class FieldRead:
    text: str                                       # "" = label found, value empty
    words: list[tuple[str, float]] = field(default_factory=list)
    line: int | None = None                         # label line number, top to bottom

    @property
    def min_conf(self) -> float | None:
        return min((c for _, c in self.words), default=None)

    def uncertain(self, threshold: float) -> bool:
        return any(c < threshold for _, c in self.words)


def tesseract_bin(params: dict) -> str:
    return params.get("tesseract") or os.environ.get(ENV_BIN) or "tesseract"


def resolve_bin(binary: str) -> str:
    """Absolute path of the tesseract binary, or OCRUnavailable with a clear message."""
    p = Path(binary)
    if p.is_absolute() or os.sep in binary:
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    elif shutil.which(binary):
        return shutil.which(binary)
    raise OCRUnavailable(f"tesseract not found ({binary!r}); install tesseract 5 and put it on "
                         f"PATH, or set {ENV_BIN} or the profile param `tesseract`")


def parse_tsv(text: str) -> list[Word]:
    rows = text.splitlines()
    if not rows:
        return []
    hdr = rows[0].split("\t")
    out = []
    for line in rows[1:]:
        f = dict(zip(hdr, line.split("\t")))
        if f.get("level") != "5" or not (f.get("text") or "").strip():
            continue
        conf = float(f.get("conf", -1))
        if not re.search(r"[A-Za-z0-9]", f["text"]) and conf < JUNK_CONF:
            continue
        out.append(Word(f["text"].strip(), conf, int(f["left"]), int(f["top"]),
                        int(f["width"]), int(f["height"])))
    return out


def run(image: str, binary: str, psm: int = 3, lang: str = "eng", timeout: int = 60) -> list[Word]:
    env = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    try:
        p = log.run(LOG, [binary, str(image), "stdout", "--psm", str(psm), "-l", lang, "tsv"],
                    "tesseract", warn_stderr=False, capture_output=True, text=True, env=env,
                    timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"tesseract timed out after {timeout}s") from None
    except OSError as e:
        raise OCRUnavailable(f"cannot run tesseract {binary!r}: {e}") from e
    if p.returncode != 0:
        raise RuntimeError(f"tesseract failed (exit {p.returncode}): {p.stderr.strip()[-300:]}")
    return parse_tsv(p.stdout)


# --- pairing ----------------------------------------------------------------------------
def segments(words: list[Word]) -> list[dict]:
    words = sorted(words, key=lambda w: w.cy)
    lines: list[dict] = []
    for w in words:
        if lines and abs(w.cy - lines[-1]["cy"]) < 0.5 * min(w.h, lines[-1]["h"]):
            ln = lines[-1]
            ln["words"].append(w)
            ln["cy"] = statistics.mean(x.cy for x in ln["words"])
            ln["h"] = statistics.median(x.h for x in ln["words"])
        else:
            lines.append({"cy": w.cy, "h": w.h, "words": [w]})
    segs = []
    for li, ln in enumerate(lines):
        ws = sorted(ln["words"], key=lambda w: w.x)
        cur = [ws[0]]
        for w in ws[1:]:
            if w.x - (cur[-1].x + cur[-1].w) > 1.5 * ln["h"]:
                segs.append((li, cur))
                cur = [w]
            else:
                cur.append(w)
        segs.append((li, cur))
    return [{"text": " ".join(w.text for w in s), "x0": min(w.x for w in s),
             "x1": max(w.x + w.w for w in s), "cy": statistics.mean(w.cy for w in s),
             "h": statistics.median(w.h for w in s), "words": s, "line": li} for li, s in segs]


def drop_region(words: list[Word], box: dict | None, width: int, height: int) -> list[Word]:
    if not box or not width or not height:
        return words
    x0, x1 = box.get("x0", 0) * width, box.get("x1", 1) * width
    y0, y1 = box.get("y0", 0) * height, box.get("y1", 1) * height
    return [w for w in words if not (x0 <= w.x + w.w / 2 <= x1 and y0 <= w.cy <= y1)]


def pair(words: list[Word], tpl: Template) -> dict[str, FieldRead]:
    """Field name -> FieldRead for every label found (absent key = label not found). The
    template's `layout` selects the pairing: `aligned` (default) or `label_colon`."""
    if tpl.layout == "label_colon":
        return pair_colon(words, tpl)
    return pair_aligned(words, tpl)


def _label_start(ws: list[Word], tpl: Template):
    """A segment that starts with a label: -> (field, value [(text, conf)]) else None.
    The label is the text up to the FIRST colon (up to 4 words, e.g. `Air Date:`); text after
    the colon in the same word (`Ad-ID:X` read as one word) and the following words of the
    segment are the value, colons and slashes included (`Title: A: B` -> `A: B`). A segment
    without a colon whose whole text is an alias is a label whose colon was not read (the
    value is then the next segment)."""
    for k in range(1, min(4, len(ws)) + 1):
        head = " ".join(w.text for w in ws[:k])
        if ":" in head:
            lab, _, rest = head.partition(":")
            name = tpl.alias.get(norm_label(lab))
            if name is None:
                return None
            rest = rest.strip()
            return name, ([(rest, ws[k - 1].conf)] if rest else []) + [(w.text, w.conf) for w in ws[k:]]
    name = tpl.alias.get(norm_label(" ".join(w.text for w in ws)))
    return (name, []) if name else None


def pair_colon(words: list[Word], tpl: Template) -> dict[str, FieldRead]:
    """`LABEL: value` slates (template `layout: label_colon`): one text line per field, the
    label first on its segment and ending in a colon; the value follows the colon on the same
    segment (`Ad-ID: X`) or is the next segment on the same line (labels and values in two
    tab-stop columns, gap > 1.5 x line height). No alignment rule, so labels may be left- or
    right-aligned and two label columns side by side also pair. A label followed by nothing
    (or directly by the next label) has an empty value. The first (top-most) occurrence of a
    field wins. `line: N` = N-th line whose first segment starts on the left edge of the label
    column (median label x0 +- 1 label height); its value = text after the first colon, else
    the next segment."""
    segs = segments(words) if words else []
    by_line: dict[int, list[dict]] = {}
    for sg in segs:
        by_line.setdefault(sg["line"], []).append(sg)
    out: dict[str, FieldRead] = {}
    starts = []                                   # every label found (field names)
    for li in sorted(by_line, key=lambda i: by_line[i][0]["cy"]):
        row = sorted(by_line[li], key=lambda sg: sg["x0"])
        i = 0
        while i < len(row):
            got = _label_start(row[i]["words"], tpl)
            if got is None:
                # an unknown `Label:` with nothing after its colon owns the next segment (its
                # value), so a value like `Date: x` there is never taken for a label
                head = " ".join(w.text for w in row[i]["words"][:4])
                i += 2 if head.endswith(":") and head.count(":") == 1 else 1
                continue
            name, value = got
            nxt = row[i + 1] if i + 1 < len(row) else None
            if not value and nxt is not None and _label_start(nxt["words"], tpl) is None:
                value = [(w.text, w.conf) for w in nxt["words"]]
                i += 1                            # the next segment is this label's value
            starts.append(name)
            if name not in out:
                out[name] = FieldRead(" ".join(t for t, _ in value), value)
            i += 1
    if not starts:
        return {}
    firsts = sorted((min(by_line[li], key=lambda sg: sg["x0"]) for li in by_line), key=lambda sg: sg["cy"])
    labels = [sg for sg in firsts if _label_start(sg["words"], tpl)] or \
        [sg for sg in segs if _label_start(sg["words"], tpl)]
    x0 = statistics.median(sg["x0"] for sg in labels)
    hmed = statistics.median(sg["h"] for sg in labels)
    column = [sg for sg in firsts if abs(sg["x0"] - x0) < 1.0 * hmed]
    for sg in column:
        got = _label_start(sg["words"], tpl)
        if got and got[0] in out and out[got[0]].line is None:
            out[got[0]].line = column.index(sg) + 1
    for name, f in tpl.fields.items():           # alternative key: N-th line of the column
        if f.get("line") and name not in out and f["line"] <= len(column):
            sg = column[f["line"] - 1]
            ws = sg["words"]
            after = " ".join(w.text for w in ws).partition(":")[2].strip()
            value = [(after, min(w.conf for w in ws))] if after else []
            if not value:                         # no colon, or nothing after it: next segment
                row = sorted(by_line[sg["line"]], key=lambda s: s["x0"])
                j = row.index(sg)
                value = [(w.text, w.conf) for w in row[j + 1]["words"]] if j + 1 < len(row) else []
            out[name] = FieldRead(" ".join(t for t, _ in value), value, line=f["line"])
    return out


def pair_aligned(words: list[Word], tpl: Template) -> dict[str, FieldRead]:
    """Right-aligned label column, left-aligned value column (the spike layout)."""
    segs = segments(words) if words else []
    labels = [s for s in segs if norm_label(s["text"]) in tpl.alias]
    if not labels:
        return {}
    right = statistics.median(s["x1"] for s in labels)
    hmed = statistics.median(s["h"] for s in labels)
    labels = [s for s in labels if abs(s["x1"] - right) < 1.0 * hmed]   # right-aligned column
    # label-column lines (for `line: N`): any segment ending on the column
    column = sorted((s for s in segs if abs(s["x1"] - right) < 1.0 * hmed), key=lambda s: s["cy"])
    cands = []
    for lab in column:
        same = [s for s in segs if s is not lab and abs(s["cy"] - lab["cy"]) < 0.6 * hmed
                and s["x0"] > lab["x1"]]
        if same:
            cands.append(min(same, key=lambda s: s["x0"]))
    vcol = statistics.median(s["x0"] for s in cands) if cands else None

    def value(lab):
        if vcol is None:
            return None
        same = [s for s in segs if s is not lab and abs(s["cy"] - lab["cy"]) < 0.6 * hmed
                and abs(s["x0"] - vcol) < 1.5 * hmed]
        return same[0] if same else None

    out: dict[str, FieldRead] = {}
    for lab in labels:
        name = tpl.alias[norm_label(lab["text"])]
        if name in out:
            continue
        v = value(lab)
        out[name] = FieldRead(v["text"] if v else "",
                              [(w.text, w.conf) for w in v["words"]] if v else [],
                              line=column.index(lab) + 1)
    for name, f in tpl.fields.items():   # alternative key: N-th line of the label column
        if f.get("line") and name not in out and f["line"] <= len(column):
            lab = column[f["line"] - 1]
            v = value(lab)
            out[name] = FieldRead(v["text"] if v else "",
                                  [(w.text, w.conf) for w in v["words"]] if v else [],
                                  line=f["line"])
    return out
