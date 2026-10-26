"""Filename convention. An explicit spec job.filename wins over job.naming_pattern.

Pattern placeholders {name} are filled from spec job fields (verbatim), then from
profile `tags`: each tag is a literal string or {from: <value>, map: {value: tag}}
(e.g. fps_tag: {from: "${spec.video.fps}", map: {"24000/1001": "2398"}}).
D27 extension: namespaced placeholders `{job.x}`, `{row.x}` (the deliverables row selected
for this file, see cmr/deliverables.py) and `{file.x}` (named groups of
deliverables.filename_regex). A pattern with {row.*}/{file.*} needs the profile param
`deliverables: "${spec.deliverables}"`; with match generated_name/both this check is the
one that verifies the whole generated name (the slate checks do not repeat it).
"""
from __future__ import annotations

import re

from ..engine import Check, Skip, register
from ..model import Severity
from ..schemas import OPT_OBJ, check_params

_PH = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _tag(name, spec_tag):
    if isinstance(spec_tag, dict):
        frm, mapping = spec_tag.get("from"), spec_tag.get("map") or {}
        for k, v in mapping.items():
            if k == frm or str(k) == str(frm):
                return str(v)
        raise KeyError(f"tag {name}: no mapping for {frm!r}")
    return str(spec_tag)


def expected_filename(job: dict, tags: dict, ns: dict | None = None) -> str:
    from ..deliverables import render_name
    if job.get("filename"):
        return job["filename"]
    pattern = job.get("naming_pattern")
    if not pattern:
        raise Skip("spec has neither job.filename nor job.naming_pattern")
    return render_name(pattern, {"job": job, **(ns or {})}, tags)


@register
class Filename(Check):
    id = "filename"
    params_schema = check_params(job=OPT_OBJ, tags={"type": "object"}, deliverables=OPT_OBJ)

    def evaluate(self, ctx, candidates):
        from .. import deliverables
        job, tags = ctx.params.get("job") or {}, ctx.params.get("tags") or {}
        act = ctx.media.name
        ns, out = None, []
        pattern = job.get("naming_pattern") or ""
        if not job.get("filename") and re.search(r"\{(row|file)\.", pattern):
            cfg = ctx.params.get("deliverables")
            if not cfg:
                return [ctx.finding("naming_pattern uses {row.*}/{file.*} but the profile passes "
                                    "no `deliverables` param", Severity.ERROR)]
            sel = deliverables.select(cfg, job, act, tags)   # ConfigError -> ERROR
            out = [ctx.finding(p) for p in sel.problems]
            if sel.row is None and "{row." in pattern:
                return out or [ctx.finding("no deliverables row selected for this file")]
            ns = sel.ns(job)
        try:
            exp = expected_filename(job, tags, ns)
        except KeyError as e:
            return out + [ctx.finding(f"cannot build expected filename: {e.args[0]}", Severity.ERROR)]
        if act != exp:
            out.append(ctx.finding(f"filename: expected {exp!r}, got {act!r}", expected=exp, actual=act))
        return out
