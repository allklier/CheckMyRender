"""Config loader: spec sheets (job data) and profiles (rules) in YAML.

Profile rules (CLAUDE.md "Substitution rules"):
- `extends: <name>` chains are resolved first (cycle = error), then ${spec.*} refs.
- Merge: mappings deep-merge (child wins per key); lists and scalars are REPLACED
  wholesale by the child (no list concatenation).
- A string that is exactly "${spec.a.b}" is replaced by the referenced value with
  its type kept (int, list, mapping...). A ref embedded in a longer string is
  interpolated with str(); the referenced value must then be a scalar.
- Path segments that are integers index into lists (${spec.audio.tracks.0.layout}).
- Missing key = ConfigError. Only the `spec.` namespace exists; no expressions.
- Per check: reserved keys `enabled` (default true), `severity` (default fail) and
  `quality` (fast|high, default high); all other keys are check params.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

import jsonschema
import yaml

from .schemas import SPEC_SCHEMA, PROFILE_SCHEMA

PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"
_REF = re.compile(r"\$\{([^}]*)\}")


class ConfigError(ValueError):
    pass


def _load_yaml(path: Path) -> tuple[dict, str]:
    try:
        text = Path(path).read_text()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML error in {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data, hashlib.sha256(text.encode()).hexdigest()


def _validate(data: dict, schema: dict, what: str) -> None:
    v = jsonschema.Draft202012Validator(schema)
    errs = sorted(v.iter_errors(data), key=lambda e: list(e.path))
    if errs:
        msgs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errs]
        raise ConfigError(f"{what} invalid:\n  " + "\n  ".join(msgs))


def load_spec(path: Path) -> tuple[dict, str]:
    spec, sha = _load_yaml(path)
    _validate(spec, SPEC_SCHEMA, f"spec {path}")
    _check_layout_shape(spec.get("timing") or {})
    return spec, sha


def _check_layout_shape(timing: dict) -> None:
    head = timing.get("head_layout") or []
    progs = [i for i, s in enumerate(head) if s["type"] == "program"]
    if head and progs != [len(head) - 1]:
        raise ConfigError("spec timing.head_layout: exactly one 'program' segment, and it must be last")
    for name in ("head_layout", "tail_layout"):
        for s in timing.get(name) or []:
            if s["type"] != "program" and "frames" not in s:
                raise ConfigError(f"spec timing.{name}: segment {s['type']!r} needs 'frames'")
    if any(s["type"] == "program" for s in timing.get("tail_layout") or []):
        raise ConfigError("spec timing.tail_layout must not contain 'program'")


def deep_merge(base, child):
    if isinstance(base, dict) and isinstance(child, dict):
        out = dict(base)
        for k, v in child.items():
            out[k] = deep_merge(base[k], v) if k in base else copy.deepcopy(v)
        return out
    return copy.deepcopy(child)


def find_profile(name_or_path: str) -> Path:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.exists():
        return p
    cand = PROFILES_DIR / f"{name_or_path}.yaml"
    if cand.exists():
        return cand
    raise ConfigError(f"profile {name_or_path!r} not found (looked in {PROFILES_DIR})")


def list_profiles() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.yaml"))


def load_profile_raw(name_or_path: str) -> tuple[dict, list[str]]:
    """Resolve `extends`. Returns merged profile + list of sha256 of each file (base first)."""
    chain, shas, seen = [], [], []
    cur = name_or_path
    while cur is not None:
        path = find_profile(cur).resolve()
        if path in seen:
            raise ConfigError("profile extends cycle: " + " -> ".join(str(s.stem) for s in seen + [path]))
        seen.append(path)
        data, sha = _load_yaml(path)
        _validate(data, PROFILE_SCHEMA, f"profile {path}")
        chain.append(data)
        shas.append(sha)
        nxt = data.get("extends")
        # 'extends' resolves next to the extending file first, then via PROFILES_DIR
        if nxt is None:
            cur = None
        else:
            sib = path.parent / (nxt if Path(nxt).suffix else f"{nxt}.yaml")
            cur = str(sib) if sib.exists() else nxt
    merged: dict = {}
    for data in reversed(chain):
        merged = deep_merge(merged, data)
    merged.pop("extends", None)
    merged["name"] = chain[0]["name"]
    return merged, list(reversed(shas))


def lookup(spec: dict, ref: str):
    parts = ref.strip().split(".")
    if parts[0] != "spec" or len(parts) < 2:
        raise ConfigError(f"unsupported reference ${{{ref}}} (only ${{spec.*}})")
    cur = spec
    for i, part in enumerate(parts[1:], 1):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise ConfigError(f"missing spec key ${{{ref}}} (no '{'.'.join(parts[:i + 1])}')")
    return copy.deepcopy(cur)


def substitute(obj, spec: dict):
    if isinstance(obj, dict):
        return {k: substitute(v, spec) for k, v in obj.items()}
    if isinstance(obj, list):
        return [substitute(v, spec) for v in obj]
    if isinstance(obj, str):
        m = _REF.fullmatch(obj)
        if m:
            return lookup(spec, m.group(1))

        def repl(mm):
            v = lookup(spec, mm.group(1))
            if isinstance(v, (dict, list)):
                raise ConfigError(f"${{{mm.group(1)}}} is a {type(v).__name__}; "
                                  "only scalars can be embedded in a string")
            return str(v)
        return _REF.sub(repl, obj)
    return obj


def resolve_profile(name_or_path: str, spec: dict) -> tuple[dict, list[str]]:
    merged, shas = load_profile_raw(name_or_path)
    return substitute(merged, spec), shas
