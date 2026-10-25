"""JSON Schemas (draft 2020-12) for spec sheets, profiles and the JSON report.
Versioned: spec_version / profile_version / report schema_version."""

SEVERITY_NAMES = ["PASS", "SKIPPED", "INFO", "WARN", "FAIL", "ERROR"]
_sev_in = {"type": "string", "pattern": "^(?i:pass|skipped|info|warn|fail|error)$"}

QUALITIES = ["fast", "high"]   # per-check `quality`; default high

SEGMENT_TYPES = ["slate", "black", "bars_tone", "two_pop", "program"]

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
            "width": {"type": "integer", "minimum": 1},
            "height": {"type": "integer", "minimum": 1},
            "fps": {"type": ["string", "integer"]},   # rational string, never a float
            "scan": {"enum": ["progressive", "interlaced"]},
            "color": {"type": "object"}}},
        "timing": {"type": "object", "properties": {
            "timecode_mode": {"enum": ["non-drop", "drop"]},
            "file_start_timecode": {"type": "string"},
            "program_start_timecode": {"type": "string"},
            "program_duration_frames": {"type": "integer", "minimum": 1},
            "head_layout": {"type": "array", "items": {"$ref": "#/$defs/segment"}},
            "tail_layout": {"type": "array", "items": {"$ref": "#/$defs/segment"}}}},
        "audio": {"type": "object", "properties": {
            "sample_rate": {"type": "integer"},
            "bit_depth": {"type": "integer"},
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
                           "quality": {"enum": QUALITIES}}}},
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
    },
}
REPORT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["schema_version", "tool", "profile", "spec", "files", "summary"],
    "properties": {
        "schema_version": {"const": "1.0"},
        "tool": {"type": "object", "required": ["name", "version", "ffmpeg"]},
        "profile": {"type": "object", "required": ["name", "sha256"]},
        "spec": {"type": "object", "required": ["path", "sha256"]},
        "files": {"type": "array", "items": {
            "type": "object", "required": ["path", "identity", "timeline", "worst", "findings"],
            "properties": {"worst": {"enum": SEVERITY_NAMES},
                           "findings": {"type": "array", "items": FINDING_SCHEMA}}}},
        "summary": {"type": "object", "required": ["worst", "exit_code", "counts"]},
    },
}
