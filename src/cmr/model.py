"""Versioned data model: Severity, Candidate (stage-1 detector output), Finding (verdict).

Frame numbers are 0-based file frame indices; frame ranges are inclusive
(frame_start..frame_end). Timecodes are filled in by the engine from the Timeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any

SCHEMA_VERSION = "1.4"   # Candidate / Finding / report schema version (1.1: waivers; 1.2: findings cap, category waivers; 1.3: summary mode/quality; 1.4: summary.job_id, waivers.foreign; additive)


class Severity(IntEnum):
    PASS = 0
    SKIPPED = 1
    INFO = 2
    WARN = 3
    FAIL = 4
    ERROR = 5   # tool failure, distinct from FAIL

    @classmethod
    def parse(cls, v: "str | Severity") -> "Severity":
        if isinstance(v, Severity):
            return v
        try:
            return cls[str(v).upper()]
        except KeyError:
            raise ValueError(f"unknown severity {v!r}") from None


# Exit codes: 0 pass, 1 warn, 2 fail, 3 tool error.
EXIT_PASS, EXIT_WARN, EXIT_FAIL, EXIT_ERROR = 0, 1, 2, 3


def exit_code(worst: Severity, fail_on: str = "warn") -> int:
    """fail_on='warn': WARN -> 1. fail_on='fail': WARN -> 0. ERROR always -> 3."""
    if worst == Severity.ERROR:
        return EXIT_ERROR
    if worst == Severity.FAIL:
        return EXIT_FAIL
    if worst == Severity.WARN and fail_on == "warn":
        return EXIT_WARN
    return EXIT_PASS


def file_verdict(severities) -> Severity:
    """Worst severity of a file's results, except that SKIPPED alone is not a verdict: a file
    whose only non-PASS results are SKIPPED is PASS (user decision; SKIPPED stays in the
    counts and the listing). The exit code is the same either way (SKIPPED exits 0)."""
    worst = max(severities, default=Severity.PASS)
    return Severity.PASS if worst == Severity.SKIPPED else worst


@dataclass
class Candidate:
    """Stage-1 detector output. Loose / high recall; a verifier may later dismiss it."""
    check_id: str
    stream: int | None          # ffprobe stream index, None = file-level
    frame_start: int
    frame_end: int              # inclusive
    score: float                # detector-specific strength, higher = more likely a defect
    evidence: list[dict] = field(default_factory=list)   # free-form, JSON-serialisable

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    check_id: str
    severity: Severity                      # final severity (after verifier / waiver)
    message: str
    raw_severity: Severity | None = None    # severity from detector + profile (max)
    stream: int | None = None
    frame_start: int | None = None
    frame_end: int | None = None            # inclusive
    timecode_start: str | None = None       # filled by engine
    timecode_end: str | None = None
    expected: Any = None
    actual: Any = None
    candidate: Candidate | None = None
    evidence: list[dict] = field(default_factory=list)
    verifier: dict | None = None            # {decision, p, model_hash}; None = no verifier ran
    waiver_ref: str | None = None           # id of the waiver that WAIVED it (D17), else None
    quality: str | None = None              # 'fast' | 'high' mode that produced it (engine fills)
    waiver: dict | None = None              # {id, status waived|content_changed, reason, distance, tolerance[, note]}

    def __post_init__(self):
        self.severity = Severity.parse(self.severity)
        if self.raw_severity is None:
            self.raw_severity = self.severity
        self.raw_severity = Severity.parse(self.raw_severity)

    def lower_to(self, sev: "Severity | str") -> None:
        """Verifier/waiver hook: may only lower the severity, never raise it."""
        sev = Severity.parse(sev)
        if sev < self.severity:
            self.severity = sev

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.name
        d["raw_severity"] = self.raw_severity.name
        return d
