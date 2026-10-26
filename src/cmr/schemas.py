"""JSON Schemas (draft 2020-12) for spec sheets, profiles and the JSON report.
Versioned: spec_version / profile_version / report schema_version."""

SEVERITY_NAMES = ["PASS", "SKIPPED", "INFO", "WARN", "FAIL", "ERROR"]
_sev_in = {"type": "string", "pattern": "^(?i:pass|skipped|info|warn|fail|error)$"}

QUALITIES = ["fast", "high"]   # per-check `quality`; default high

SEGMENT_TYPES = ["slate", "black", "bars_tone", "two_pop", "program"]

_opt_int = {"type": ["integer", "null"], "minimum": 1}   # minimum applies to integers only

SPEC_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["spec_version"],
    "properties": {
        "spec_version": {"const": 1},
        "job": {"type": "object", "properties": {
            "filename": {"type": "string"},
            "naming_pattern": {"type": "string"}}},
        "video": {"type": "object", "properties": {
            "width": _opt_int,          # null = not specified, not checked (also below)
            "height": _opt_int,
            "fps": {"type": ["string", "integer"]},   # rational string, never a float
            "scan": {"enum": ["progressive", "interlaced"]},
            "color": {"type": "object"}}},
        "timing": {"type": "object", "properties": {
            "timecode_mode": {"enum": ["non-drop", "drop"]},
            "file_start_timecode": {"type": ["string", "null"]},
            "program_start_timecode": {"type": ["string", "null"]},
            "program_duration_frames": _opt_int,
            "head_layout": {"type": "array", "items": {"$ref": "#/$defs/segment"}},
            "tail_layout": {"type": "array", "items": {"$ref": "#/$defs/segment"}}}},
        "audio": {"type": "object", "properties": {
            "sample_rate": _opt_int,
            "bit_depth": _opt_int,
            "tracks": {"type": "array", "items": {
                "type": "object", "required": ["index", "layout"],
                "properties": {"index": {"type": "integer", "minimum": 0},
                               "layout": {"type": "string"}}}}}},
        "content": {"type": "object"},
    },
    "$defs": {"segment": {
        "type": "object", "required": ["type"],
        "properties": {"type": {"enum": SEGMENT_TYPES},
                       "frames": {"type": "integer", "minimum": 1}},
        "additionalProperties": False}},
}

# Job file (D27): the job-level part of the spec, merged into the one spec by `cmr check --job`.
# Blank values mean "not specified" (not checked); "<empty>" means "must be empty".
JOB_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["job_version"],
    "additionalProperties": False,
    "properties": {
        "job_version": {"const": 1},
        # D31: the job file as the entry point. job.id = short job id (waivers, reports); the
        # file names its spec sheet, profile (stock name or path), waiver file and report
        # folder; relative paths resolve from the job file's folder; CLI options override.
        "job": {"type": "object", "properties": {"id": {"$ref": "#/$defs/job_id"}}},
        "spec": {"type": "string", "minLength": 1},
        "profile": {"type": "string", "minLength": 1},
        "waivers": {"type": "string", "minLength": 1},
        "output": {"type": "string", "minLength": 1},
        "deliverables": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "csv": {"type": "string"},
                "columns": {"type": "object", "additionalProperties": {"type": "string"}},
                # one column, or a composite key (list of columns = filename groups); D27
                "key": {"oneOf": [{"type": "string"},
                                  {"type": "array", "minItems": 1, "items": {"type": "string"}}]},
                "key_type": {"oneOf": [{"enum": ["ad_id", "text", "duration"]},
                                       {"type": "object", "additionalProperties":
                                        {"enum": ["ad_id", "text", "duration"]}}]},
                "match": {"enum": ["key", "generated_name", "both", "fields"]},
                "filename_regex": {"type": "string"},
                "match_fields": {"type": "array", "items": {"type": "string"}},
                "row": {"type": "string"}}},
        "slate": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "template": {"type": "string"},
                "expect": {"type": "object"}}},
    },
    # letters, digits, `_`, `-`, `.`; "*" is reserved for cross-job waivers
    "$defs": {"job_id": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"}},
}

PROFILE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["profile_version", "name", "checks"],
    "properties": {
        "profile_version": {"const": 1},
        "name": {"type": "string"},
        "extends": {"type": "string"},
        "description": {"type": "string"},
        "checks": {"type": "object", "additionalProperties": {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}, "severity": _sev_in,
                           "quality": {"enum": QUALITIES},
                           # findings listed per check before the rest is summarised; 0 = all
                           "max_findings": {"type": "integer", "minimum": 0}}}},
        "waivers": {"type": "object", "additionalProperties": False,   # matching tolerances (D17)
                    "properties": {"video_max_bits": {"type": "integer", "minimum": 0, "maximum": 64},
                                   "audio_max_db": {"type": "number", "minimum": 0},
                                   "margin_frames": {"type": "integer", "minimum": 0}}},
    },
    "additionalProperties": False,
}

_loc = {"type": ["integer", "null"]}
_tc = {"type": ["string", "null"]}
CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["check_id", "stream", "frame_start", "frame_end", "score", "evidence"],
    "properties": {"check_id": {"type": "string"}, "stream": _loc,
                   "frame_start": {"type": "integer"}, "frame_end": {"type": "integer"},
                   "score": {"type": "number"}, "evidence": {"type": "array"}},
}
FINDING_SCHEMA = {
    "type": "object",
    "required": ["check_id", "severity", "raw_severity", "message", "frame_start", "frame_end",
                 "timecode_start", "timecode_end", "verifier", "waiver_ref"],
    "properties": {
        "check_id": {"type": "string"},
        "severity": {"enum": SEVERITY_NAMES}, "raw_severity": {"enum": SEVERITY_NAMES},
        "message": {"type": "string"}, "stream": _loc,
        "frame_start": _loc, "frame_end": _loc, "timecode_start": _tc, "timecode_end": _tc,
        "candidate": {"oneOf": [{"type": "null"}, CANDIDATE_SCHEMA]},
        "evidence": {"type": "array"},
        "verifier": {"type": ["object", "null"]}, "waiver_ref": {"type": ["string", "null"]},
        "quality": {"enum": QUALITIES + [None]},
        "waiver": {"oneOf": [{"type": "null"}, {
            "type": "object", "required": ["id", "status", "reason"],
            "properties": {"id": {"type": "string"},
                           "status": {"enum": ["waived", "content_changed"]},
                           "type": {"enum": ["category"]}}}]},
    },
}
_span = {"frame_start": _loc, "frame_end": _loc, "timecode_start": _tc, "timecode_end": _tc}
_counts = {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}}
SUPPRESSED_SCHEMA = {   # 1.2: findings over the per-check cap, one entry per check
    "type": "object",
    "required": ["check_id", "count", "limit", "worst_severity", "by_severity", *_span],
    "additionalProperties": False,
    "properties": {"check_id": {"type": "string"}, "count": {"type": "integer", "minimum": 1},
                   "limit": {"type": "integer", "minimum": 1},
                   "worst_severity": {"enum": SEVERITY_NAMES}, "by_severity": _counts, **_span},
}
WAIVED_BY_CATEGORY_SCHEMA = {   # 1.2: findings waived by one category waiver, per check
    "type": "object",
    "required": ["check_id", "waiver_id", "count", "worst_raw_severity", *_span],
    "additionalProperties": False,
    "properties": {"check_id": {"type": "string"}, "waiver_id": {"type": "string"},
                   "count": {"type": "integer", "minimum": 1},
                   "worst_raw_severity": {"enum": SEVERITY_NAMES}, **_span},
}
REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "tool", "profile", "spec", "files", "summary"],
    "properties": {
        # 1.1 (additive): finding.waiver, counts.WAIVED, waivers
        # 1.2 (additive): files[].suppressed, files[].waived_by_category, summary.max_findings /
        #     check_limits / suppressed / waived_by_category (counts stay complete)
        # 1.3 (additive): summary.mode (run mode, D26) and summary.quality (`cmr check --quality`
        #     fast|high, or "profile" when not given; per-finding `quality` is the mode used)
        # 1.4 (additive): summary.job_id (job.yaml `job.id`, D31, null without one) and
        #     waivers.foreign (waivers of another job, skipped)
        "schema_version": {"const": "1.4"},
        "tool": {"type": "object", "required": ["name", "version", "ffmpeg"]},
        "profile": {"type": "object", "required": ["name", "sha256"]},
        "spec": {"type": "object", "required": ["path", "sha256"]},
        "files": {"type": "array", "items": {
            "type": "object", "required": ["path", "identity", "timeline", "worst", "findings",
                                           "suppressed", "waived_by_category"],
            "properties": {"worst": {"enum": SEVERITY_NAMES},
                           "findings": {"type": "array", "items": FINDING_SCHEMA},
                           "suppressed": {"type": "array", "items": SUPPRESSED_SCHEMA},
                           "waived_by_category": {"type": "array",
                                                  "items": WAIVED_BY_CATEGORY_SCHEMA}}}},
        "summary": {"type": "object",
                    "required": ["worst", "exit_code", "counts", "max_findings", "check_limits",
                                 "suppressed", "waived_by_category", "mode", "quality",
                                 "job_id"],
                    "properties": {
                        "job_id": {"type": ["string", "null"]},
                        "mode": {"enum": ["foreground", "background", None]},
                        "quality": {"enum": QUALITIES + ["profile"]},
                        "max_findings": {"type": "integer", "minimum": 0},
                        "check_limits": _counts,
                        "suppressed": {"type": "integer", "minimum": 0},
                        "waived_by_category": {"type": "integer", "minimum": 0}}},
        "waivers": {"type": "object",   # present only with `cmr check --waivers FILE`
                    "required": ["file", "applied", "content_changed", "stale", "expired",
                                 "foreign"]},
    },
}

# --- check params (profile check entries minus the reserved keys) ---------------------------
# Built with `check_params`: additionalProperties false, so a misspelt param is a config error
# naming the key (engine.validate_profile_params, `cmr check` / `cmr profiles show`, exit 3).
# Values from `${spec.*}` refs may be null (= not specified, not checked).
SEV_PARAM = _sev_in
OPT_STR = {"type": ["string", "null"]}
OPT_INT = {"type": ["integer", "null"]}
OPT_OBJ = {"type": ["object", "null"]}


def check_params(required=(), **props) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": props}
