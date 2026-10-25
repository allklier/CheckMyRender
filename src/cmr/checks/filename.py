"""Filename convention. An explicit spec job.filename wins over job.naming_pattern.

Pattern placeholders {name} are filled from spec job fields (verbatim), then from
profile `tags`: each tag is a literal string or {from: <value>, map: {value: tag}}
(e.g. fps_tag: {from: "${spec.video.fps}", map: {"24000/1001": "2398"}}).
"""
from __future__ import annotations

import re

from ..engine import Check, Skip, register
from ..model import Severity

_PH = re.compile(r"\{([A-Za-z0-9_]+)\}")


def _tag(name, spec_tag):
    if isinstance(spec_tag, dict):
        frm, mapping = spec_tag.get("from"), spec_tag.get("map") or {}
        for k, v in mapping.items():
            if k == frm or str(k) == str(frm):
                return str(v)
        raise KeyError(f"tag {name}: no mapping for {frm!r}")
    return str(spec_tag)


def expected_filename(job: dict, tags: dict) -> str:
    if job.get("filename"):
        return job["filename"]
    pattern = job.get("naming_pattern")
    if not pattern:
        raise Skip("spec has neither job.filename nor job.naming_pattern")

    def repl(m):
        name = m.group(1)
        if name in job and not isinstance(job[name], (dict, list)):
            return str(job[name])
        if name in tags:
            return _tag(name, tags[name])
        raise KeyError(f"placeholder {{{name}}} not in spec job fields or profile tags")
    return _PH.sub(repl, pattern)


@register
class Filename(Check):
    id = "filename"

    def evaluate(self, ctx, candidates):
        try:
            exp = expected_filename(ctx.params.get("job") or {}, ctx.params.get("tags") or {})
        except KeyError as e:
            return [ctx.finding(f"cannot build expected filename: {e.args[0]}", Severity.ERROR)]
        act = ctx.media.path.name
        if act != exp:
            return [ctx.finding(f"filename: expected {exp!r}, got {act!r}", expected=exp, actual=act)]
        return []
