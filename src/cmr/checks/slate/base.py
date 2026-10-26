"""Shared plumbing of the slate checks: params schema, template + OCR read, findings."""
from __future__ import annotations

from cmr.engine import Check
from cmr.model import Severity
from . import ocr, reader, template

_SEV = {"enum": ["info", "warn", "fail", "INFO", "WARN", "FAIL"]}
OCR_PROPS = {
    "slate": {"type": "object"},                 # "${spec.slate}": template path + expect
    "tesseract": {"type": "string"},             # binary; else env CMR_TESSERACT, else PATH
    "psm": {"type": "integer", "minimum": 0, "maximum": 13},
    "lang": {"type": "string"},
    "ocr_timeout": {"type": "integer", "minimum": 1},
    # slate/black split may be up to N frames earlier than the spec says (reader.slate_frames)
    "split_tolerance_frames": {"type": "integer", "minimum": 0},
    "min_confidence": {"type": "number", "minimum": 0, "maximum": 100},
    "uncertain_severity": _SEV,                  # low-confidence read that decides a problem
    "confusable_severity": _SEV,                 # equal only under confusable classes
    # problem severity per field (evidence kind), e.g. {AD-ID: fail, CLIENT: warn}; replaces
    # the check severity for that field (not the uncertain/confusable severities)
    "field_severity": {"type": "object", "additionalProperties": _SEV},
}
DEFAULTS = {"min_confidence": 60, "uncertain_severity": "warn", "confusable_severity": "info"}


def schema(**extra) -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["slate"],
            "properties": {**OCR_PROPS, **extra}}


class SlateCheck(Check):
    """Base: no taps (everything runs after the main pass, by seeking)."""
    qualities = ("fast", "high")      # D14: same behaviour in both modes (2 frames only)

    def p(self, ctx, key):
        return ctx.params.get(key, DEFAULTS.get(key))

    def template(self, ctx) -> template.Template:
        return template.load((ctx.params.get("slate") or {}).get("template"), ctx.spec)

    def read(self, ctx, tpl):
        """-> SlateRead, or an ERROR finding list when OCR cannot run (never a crash)."""
        try:
            return reader.read(ctx, tpl, ctx.params), None
        except ocr.OCRUnavailable as e:
            return None, [ctx.finding(f"slate not read: {e}", Severity.ERROR)]
        except (RuntimeError, OSError) as e:
            return None, [ctx.finding(f"slate not read: {e}", Severity.ERROR)]

    def finding(self, ctx, sr, msg, kind, text, severity=None, **kw):
        """Finding on the slate frames read; evidence kind = field name, text = exact OCR text
        (the waiver fingerprint, D17 slate_text)."""
        ev = {"kind": kind, "text": text, "frames": list(sr.frames)}
        ev.update(kw.pop("evidence", {}))
        fs = ctx.params.get("field_severity") or {}
        if severity is None and kind in fs:
            severity = Severity.parse(fs[kind])
        kw.setdefault("frame_start", sr.frames[0])
        kw.setdefault("frame_end", sr.frames[-1])
        return ctx.finding(msg, severity=severity, stream=ctx.media.video[0]["index"],
                           evidence=[ev], **kw)

    def sev(self, ctx, key) -> Severity:
        return Severity.parse(self.p(ctx, key))
