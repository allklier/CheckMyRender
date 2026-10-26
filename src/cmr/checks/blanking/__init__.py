"""video.blanking: black/non-picture rows or columns at the frame edges.

Plugs into the core single pass (src/cmr/CLAUDE.md v1.0). Logic and
tuning knobs live in detector.py; see src/cmr/checks/blanking/CLAUDE.md for measurements.
"""
from cmr.engine import Check, ConfigError, Tap, register

from . import detector

_NUM = {"type": "number", "minimum": 0}
EXCLUDE_SEGMENTS = ("slate", "bars_tone", "two_pop")   # user decision (gap fix)
_SEV = {"type": "string", "enum": ["pass", "info", "warn", "fail"]}


@register
class Blanking(Check):
    id = detector.CHECK_ID
    needs = frozenset({"probe"})
    qualities = ("fast", "high")      # fast: every fast_frame_step-th frame only
    requires_filters = ("split", "crop", "format", "shuffleplanes", "lut2", "signalstats",
                        "metadata")
    params_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "coverage": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
            "coverage_tolerance": {"type": "number", "minimum": 0, "maximum": 1},
            "black_luma": _NUM, "matte_luma": _NUM, "min_step": _NUM, "min_contrast": _NUM,
            "dark_frame_luma": _NUM,
            "max_depth": {"type": "integer", "minimum": 1},
            "min_run_frames": {"type": "integer", "minimum": 1},
            "persistent_fraction": {"type": "number", "minimum": 0, "maximum": 1},
            "fast_frame_step": {"type": "integer", "minimum": 1},
            "merge_gap_frames": {"type": "integer", "minimum": 0},
            "dark_edge_mode": {"enum": list(detector.DARK_EDGE_MODES)},
            # head/tail layout segments (D7) not judged; default slate, bars_tone, two_pop:
            # a vignetted slate reads as dark_edge, bars/2-pop cards have black edge areas
            "exclude_segments": {"type": "array", "items": {"type": "string"}},
            "class_severity": {
                "type": "object", "additionalProperties": False,
                "properties": {k: _SEV for k in detector.DEFAULTS["class_severity"]},
            },
        },
    }

    @staticmethod
    def _step(ctx):
        return (detector.params_with_defaults(ctx.params)["fast_frame_step"]
                if ctx.quality == "fast" else 1)

    def taps(self, ctx):
        v = ctx.media.video[0]["index"]
        return [Tap(name, v, chain, detector.tap_keys(name))
                for name, chain in detector.chains(ctx.params, self._step(ctx)).items()]

    def detect(self, ctx, records):
        skip = [s for s in ctx.timeline.segments
                if s["type"] in ctx.params.get("exclude_segments", EXCLUDE_SEGMENTS)]
        if skip:
            records = {k: [r for r in v if not any(s["start"] <= r.frame <= s["end"] for s in skip)]
                       for k, v in records.items()}
        return detector.detect(detector.stats_from_records(records), ctx.params,
                               stream=ctx.media.video[0]["index"], frame_step=self._step(ctx))

    def evaluate(self, ctx, candidates):
        try:
            return detector.evaluate(candidates, ctx.params, cap=ctx.severity,
                                     check_id=ctx.check_id)
        except ValueError as e:   # bad dark_edge_mode
            raise ConfigError(str(e)) from e
