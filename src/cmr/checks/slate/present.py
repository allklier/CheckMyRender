"""slate.present and slate.consistency."""
from __future__ import annotations

from cmr.engine import Skip, register
from .base import SlateCheck, schema
from .compare import compare


@register
class SlatePresent(SlateCheck):
    """head_layout has a slate segment (else SKIPPED: unslated variant) and the template's
    required labels are found on every frame read."""
    id = "slate.present"
    params_schema = schema()

    def evaluate(self, ctx, candidates):
        tpl = self.template(ctx)
        sr, err = self.read(ctx, tpl)
        if err:
            return err
        out, missing = [], {}
        for f, r, words in zip(sr.frames, sr.reads, sr.words):
            if not r:
                text = " ".join(w.text for w in words)
                out.append(self.finding(
                    ctx, sr, f"no slate found on frame {f}: none of the template {tpl.name!r} labels "
                    f"was read" + (f" (text read: {text[:80]!r})" if text else " (no text)"),
                    "slate", text, frame_start=f, frame_end=f))
                continue
            for name in tpl.required():
                if name not in r:
                    missing.setdefault(name, []).append(f)
        for name, frames in missing.items():
            where = "frame " + " and ".join(map(str, frames))
            out.append(self.finding(ctx, sr, f"slate label {name} not found on {where} "
                                    f"(template {tpl.name!r})", name, "",
                                    frame_start=frames[0], frame_end=frames[-1]))
        return out


@register
class SlateConsistency(SlateCheck):
    """The frames read agree on every field (countdown excluded). A disagreement where one
    side has a low-confidence word is an UNCERTAIN read, not a slate change. A field found on
    one frame only is slate.present's finding (not repeated here)."""
    id = "slate.consistency"
    params_schema = schema()

    def evaluate(self, ctx, candidates):
        tpl = self.template(ctx)
        sr, err = self.read(ctx, tpl)
        if err:
            return err
        if len(sr.frames) < 2:
            raise Skip("slate segment has one frame: nothing to compare")
        (fa, fb), (ra, rb) = (sr.frames[0], sr.frames[-1]), (sr.reads[0], sr.reads[-1])
        thr, out = self.p(ctx, "min_confidence"), []
        for name in tpl.fields:
            if name not in ra or name not in rb:
                continue
            a, b = ra[name], rb[name]
            res = compare(a.text, b.text, tpl.type(name))
            text = f"{a.text} | {b.text}"
            ev = {"evidence": {"texts": [a.text, b.text], "confidence": [a.min_conf, b.min_conf]}}
            if res.status == "confusable":
                out.append(self.finding(
                    ctx, sr, f"slate {name}: frames {fa} and {fb} read {a.text!r} / {b.text!r}, "
                    f"equal only under confusable characters (positions {res.positions})",
                    name, text, self.sev(ctx, "confusable_severity"), **ev))
            elif res.status == "mismatch":
                if a.uncertain(thr) or b.uncertain(thr):
                    out.append(self.finding(
                        ctx, sr, f"slate {name}: uncertain read - frame {fa} {a.text!r} (conf "
                        f"{a.min_conf}) vs frame {fb} {b.text!r} (conf {b.min_conf}); "
                        f"low-confidence word below {thr}, not judged as a slate change",
                        name, text, self.sev(ctx, "uncertain_severity"), **ev))
                else:
                    out.append(self.finding(
                        ctx, sr, f"slate {name} changes during the slate: frame {fa} {a.text!r}, "
                        f"frame {fb} {b.text!r}", name, text, expected=a.text, actual=b.text, **ev))
        return out
