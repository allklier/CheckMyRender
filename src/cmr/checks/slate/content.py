"""slate.content: each slate field vs its expectation (job constants, the deliverables row
selected from the ACTUAL filename, filename parts), plus the DATE policy.

Authority (D27, user decision): the slate TITLE is compared with the actual filename first;
if they differ -> FAIL "wrong slate or renamed file", and the other fields are still compared
against the row selected from the actual filename. AD-ID in the row, the filename and on the
slate must agree. A field is checked only when its expectation resolves to a value; "<empty>"
= must be empty. Low-confidence reads that decide a mismatch are UNCERTAIN (profile
`uncertain_severity`), matches only under confusable classes are INFO (`confusable_severity`).
"""
from __future__ import annotations

import datetime
import os

from cmr import deliverables as dl
from cmr.engine import register
from cmr.model import Severity
from .base import SlateCheck, schema, _SEV
from .compare import DATE_FORMATS, compare, parse_date

DATE_DEFAULTS = {"mode": "window", "format": "YYYYMMDD", "window_days": 30, "severity": "warn"}


def file_date(media) -> tuple[datetime.date, str]:
    """The file's own date: container creation_time tag, else the file modification time."""
    ct = ((media.raw.get("format") or {}).get("tags") or {}).get("creation_time")
    if ct:
        try:
            return datetime.datetime.fromisoformat(ct.replace("Z", "+00:00")).date(), "creation_time"
        except ValueError:
            pass
    return datetime.datetime.fromtimestamp(os.stat(media.path).st_mtime,
                                           datetime.timezone.utc).date(), "file mtime"


@register
class SlateContent(SlateCheck):
    id = "slate.content"
    params_schema = schema(
        job={"type": ["object", "null"]},              # "${spec.job}"
        deliverables={"type": ["object", "null"]},     # "${spec.deliverables}"
        tags={"type": "object"},                       # filename tags (match generated_name)
        date={"type": "object", "additionalProperties": False, "properties": {
            "mode": {"enum": ["ignore", "format", "window", "equals_row"]},
            "format": {"enum": list(DATE_FORMATS)},
            "window_days": {"type": "integer", "minimum": 0},
            "severity": _SEV}})

    def evaluate(self, ctx, candidates):
        tpl = self.template(ctx)
        sr, err = self.read(ctx, tpl)
        if err:
            return err
        job = ctx.params.get("job") or {}
        cfg = ctx.params.get("deliverables") or {}
        name = ctx.media.name
        out = []
        if cfg.get("csv") or cfg.get("row"):
            sel = dl.select(cfg, job, name, ctx.params.get("tags"))   # ConfigError -> ERROR
        else:
            sel = dl.Selection(None, None, "none", dl.file_tokens(name, cfg.get("filename_regex"))[0])
        for p in sel.problems:
            out.append(self.finding(ctx, sr, f"{p}; row fields not checked", "row", name))
        ns = sel.ns(job)
        out += self._row_vs_filename(ctx, sr, sel, cfg)
        expect = tpl.expectations((ctx.params.get("slate") or {}).get("expect"))
        for field, expr in expect.items():
            exp = dl.render(expr, ns)
            read = sr.best(field)
            if exp is None or read is None:     # not specified / label not read (slate.present)
                continue
            out += self._compare(ctx, sr, tpl, field, exp, read)
        date_field = "DATE" if "DATE" in tpl.fields else tpl.field_of_type("date")
        if date_field:
            out += self._date(ctx, sr, sel, tpl, date_field)
        return out

    def _row_vs_filename(self, ctx, sr, sel, cfg):
        """Every key part (one column, or each part of a composite key) agrees between the
        selected row and the filename (e.g. --row picked another spot)."""
        out = []
        for key, kt in dl.key_parts(cfg) or [("ad_id", "ad_id")]:
            a, b = (sel.row or {}).get(key), sel.file.get(key)
            if a and b and dl.norm_key(a, kt) != dl.norm_key(b, kt):
                out.append(self.finding(ctx, sr, f"{key} differs: deliverables {sel.describe()} has "
                                        f"{a!r}, the filename has {b!r}", "row", b, expected=a, actual=b))
        return out

    def _compare(self, ctx, sr, tpl, field, exp, read):
        text, ftype, thr = read.text, tpl.type(field), self.p(ctx, "min_confidence")
        ev = {"evidence": {"confidence": read.min_conf, "expected": exp}}
        if exp == dl.EMPTY:
            if text:
                return [self.finding(ctx, sr, f"slate {field} must be empty, reads {text!r}", field,
                                     text, expected="", actual=text, **ev)]
            return []
        if not text:
            return [self.finding(ctx, sr, f"slate {field} is empty, expected {exp!r}", field, text,
                                 expected=exp, actual="", **ev)]
        res = compare(exp, text, ftype)
        if res.status == "match":
            return []
        if res.status == "confusable":
            return [self.finding(ctx, sr, f"slate {field} {text!r} matches {exp!r} only under "
                                 f"confusable characters (positions {res.positions}; 0/O, 1/I/l, "
                                 f"5/S, 2/Z, 8/B look alike)", field, text,
                                 self.sev(ctx, "confusable_severity"), expected=exp, actual=text, **ev)]
        if read.uncertain(thr):
            return [self.finding(ctx, sr, f"slate {field}: uncertain read {text!r} (confidence "
                                 f"{read.min_conf} < {thr}) does not match {exp!r}; check by eye",
                                 field, text, self.sev(ctx, "uncertain_severity"),
                                 expected=exp, actual=text, **ev)]
        if field == "TITLE" and exp == ctx.media.stem:
            msg = (f"wrong slate or renamed file: slate TITLE {text!r} does not match the filename "
                   f"{ctx.media.name!r}")
        elif ftype == "id":
            msg = f"slate does not match the file: slate {field} {text!r}, expected {exp!r}"
        else:
            msg = f"slate {field} {text!r}, expected {exp!r}"
        return [self.finding(ctx, sr, msg, field, text, expected=exp, actual=text, **ev)]

    def _date(self, ctx, sr, sel, tpl, name="DATE"):
        """Formats: the template's `date_formats` (the slate style) when set, else the profile
        `date.format`. A date that parses in any of them is accepted."""
        pol = {**DATE_DEFAULTS, **(ctx.params.get("date") or {})}
        read = sr.best(name)
        if pol["mode"] == "ignore" or read is None:
            return []
        sev = Severity.parse(pol["severity"])
        text = read.text
        if read.uncertain(self.p(ctx, "min_confidence")):
            sev = min(sev, self.sev(ctx, "uncertain_severity"))
        if not text:
            return [self.finding(ctx, sr, f"slate {name} is empty", name, text, sev)]
        fmts = tpl.date_formats or [pol["format"]]
        d = next((x for x in (parse_date(text, f) for f in fmts) if x), None)
        if d is None:
            return [self.finding(ctx, sr, f"slate {name} {text!r} is not a {' or '.join(fmts)} date",
                                 name, text, sev, expected=" or ".join(fmts), actual=text)]
        if pol["mode"] == "window":
            fd, src = file_date(ctx.media)
            days = (d - fd).days
            if abs(days) > pol["window_days"]:
                return [self.finding(
                    ctx, sr, f"slate {name} {text} is {abs(days)} days {'after' if days > 0 else 'before'} "
                    f"the file date {fd:%Y%m%d} ({src}); allowed +-{pol['window_days']} days",
                    name, text, sev, expected=f"{fd:%Y%m%d} +-{pol['window_days']} d", actual=text)]
        elif pol["mode"] == "equals_row":
            want = dl.render("{row.date}", sel.ns({}))
            if want and want != dl.EMPTY:
                wd = next((x for x in (parse_date(want, f) for f in fmts) if x), None)
                same = (wd == d) if wd else compare(want, text, "date").status != "mismatch"
                if not same:
                    return [self.finding(ctx, sr, f"slate {name} {text!r}, deliverables row date {want!r}",
                                         name, text, sev, expected=want, actual=text)]
        return []
