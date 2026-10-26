"""Slate template (YAML): which labels a slate has, how to read and compare them, where the
bars are. Selected by `slate.template` in the job file (a path; relative to the job file).

    template_version: 1
    name: xr_standard
    layout: aligned                 # aligned (default) | label_colon (see ocr.pair)
    date_formats: [MM/DD/YYYY, M/D/YYYY]    # optional; slate DATE formats (compare.DATE_FORMATS)
    fields:                         # canonical field name -> rules
      AD-ID: {aliases: [AD-ID, ADID, AD ID], type: id, expect: "{row.ad_id}"}
      MISC:  {optional: true}       # may be missing from the slate; never required
      NOTES: {line: 11}             # alternative key: 11th line of the label column
    countdown: {x0: 0.78, y0: 0.68, x1: 0.94, y1: 0.94}    # words centred here are ignored
    bars:                           # optional; strips found automatically when no fractions
      colour: {band: top, y0: 0.0, y1: 0.03, x0: 0.0, x1: 1.0, segments: [white, ...]}
      grey: {band: bottom, y_targets: [235, 205, ...]}     # 8-bit Y targets per step

`expect` holds the default expectation (tokens `{job.*}`, `{row.*}`, `{file.*}`); the job
file's `slate.expect` overrides it per field (null/blank = not checked, "<empty>" = must be
empty). `type`: text (default), title, id, duration, date, audio (see compare.py).
"""
from __future__ import annotations

import re
from pathlib import Path

import jsonschema
import yaml

from cmr.config import ConfigError, substitute
from .compare import DATE_FORMATS, TYPES

LAYOUTS = ("aligned", "label_colon")
COLOUR_BARS = ["white", "yellow", "cyan", "green", "magenta", "red", "blue", "black"]
_FRAC = {"type": "number", "minimum": 0, "maximum": 1}
_STRIP = {"type": "object", "additionalProperties": False,
          "properties": {"band": {"enum": ["top", "bottom"]},
                         "x0": _FRAC, "x1": _FRAC, "y0": _FRAC, "y1": _FRAC,
                         "segments": {"type": "array", "items": {"type": "string"}},
                         "y_targets": {"type": "array", "items": {"type": "number"}}}}
SCHEMA = {
    "type": "object", "required": ["template_version", "fields"], "additionalProperties": False,
    "properties": {
        "template_version": {"const": 1},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "layout": {"enum": list(LAYOUTS)},
        "date_formats": {"type": "array", "minItems": 1, "items": {"enum": list(DATE_FORMATS)}},
        "fields": {"type": "object", "minProperties": 1, "additionalProperties": {
            "type": ["object", "null"], "additionalProperties": False,
            "properties": {"aliases": {"type": "array", "items": {"type": "string"}},
                           "type": {"enum": list(TYPES)},
                           "optional": {"type": "boolean"},
                           "line": {"type": "integer", "minimum": 1},
                           "expect": {"type": ["string", "number", "null"]}}}},
        "countdown": {"type": "object", "additionalProperties": False,
                      "properties": {"x0": _FRAC, "x1": _FRAC, "y0": _FRAC, "y1": _FRAC}},
        "bars": {"type": "object", "additionalProperties": False,
                 "properties": {"colour": _STRIP, "grey": _STRIP}},
    },
}


def norm_label(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


class Template:
    def __init__(self, data: dict, path: str = "<template>"):
        self.path, self.name = path, data.get("name") or Path(path).stem
        self.fields: dict[str, dict] = {}
        for name, f in data["fields"].items():
            f = dict(f or {})
            f.setdefault("aliases", [name])
            f.setdefault("type", "text")
            f.setdefault("optional", False)
            self.fields[name] = f
        self.alias = {}
        for name, f in self.fields.items():
            for a in f["aliases"]:
                if norm_label(a) in self.alias and self.alias[norm_label(a)] != name:
                    raise ConfigError(f"slate template {path}: alias {a!r} used by two fields")
                self.alias[norm_label(a)] = name
        self.layout = data.get("layout") or "aligned"
        self.date_formats = list(data.get("date_formats") or [])
        self.countdown = data.get("countdown")
        self.bars = data.get("bars") or {}

    def required(self) -> list[str]:
        return [n for n, f in self.fields.items() if not f["optional"]]

    def field_of_type(self, ftype: str) -> str | None:
        """First field of a type (e.g. the duration field, whatever its label)."""
        return next((n for n, f in self.fields.items() if f["type"] == ftype), None)

    def type(self, name: str) -> str:
        return self.fields.get(name, {}).get("type", "text")

    def expectations(self, overrides: dict | None) -> dict:
        """Field -> expectation expression (template default, job override wins)."""
        out = {n: f.get("expect") for n, f in self.fields.items()}
        for k, v in (overrides or {}).items():
            if k not in self.fields:
                raise ConfigError(f"slate.expect: {k!r} is not a field of template {self.name} "
                                  f"(fields: {', '.join(self.fields)})")
            out[k] = v
        return out


_CACHE: dict = {}


def load(path: str, spec: dict | None = None) -> Template:
    if not path:
        raise ConfigError("slate.template is not set (job file `slate.template`)")
    p = Path(path)
    try:
        key = (str(p.resolve()), p.stat().st_mtime_ns)
    except OSError as e:
        raise ConfigError(f"cannot read slate template {path}: {e}") from e
    if key not in _CACHE:
        try:
            data = yaml.safe_load(p.read_text())
        except (OSError, yaml.YAMLError) as e:
            raise ConfigError(f"slate template {path}: {e}") from e
        try:
            jsonschema.validate(data, SCHEMA)
        except jsonschema.ValidationError as e:
            loc = "/".join(map(str, e.path)) or "<root>"
            raise ConfigError(f"slate template {path}: {loc}: {e.message}") from None
        _CACHE[key] = data
    data = _CACHE[key]
    if spec is not None:    # ${spec.*} refs in the template: load time (D4)
        data = substitute(data, spec)
    return Template(data, str(p))
