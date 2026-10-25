"""Report assembly (versioned, schema-validated JSON) and console output."""
from __future__ import annotations

import json
import platform
from collections import Counter

import jsonschema

from .. import __version__
from ..model import SCHEMA_VERSION, Severity, exit_code
from ..schemas import REPORT_SCHEMA


def build_report(files: list[dict], profile_name: str, profile_shas: list[str], spec_path: str,
                 spec_sha: str, ffmpeg_version: str, fail_on: str) -> dict:
    worst = max((Severity[f["worst"]] for f in files), default=Severity.PASS)
    counts = Counter(fd.severity.name for f in files for fd in f["findings"])
    report = {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": "cmr", "version": __version__, "ffmpeg": ffmpeg_version,
                 "python": platform.python_version()},
        "profile": {"name": profile_name, "sha256": profile_shas},
        "spec": {"path": spec_path, "sha256": spec_sha},
        "files": [{**f, "findings": [fd.to_dict() for fd in f["findings"]]} for f in files],
        "summary": {"worst": worst.name, "fail_on": fail_on, "exit_code": exit_code(worst, fail_on),
                    "counts": {s.name: counts.get(s.name, 0) for s in Severity}},
    }
    # round-trip through JSON so Fractions etc. become strings, then validate
    report = json.loads(json.dumps(report, default=str))
    jsonschema.validate(report, REPORT_SCHEMA)
    return report


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=False) + "\n"
