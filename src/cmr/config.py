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
- Lazy: entries of checks with `enabled: false` are not substituted (refs kept verbatim).
  Params a check lists in `lazy_params` (filename: `tags`) are substituted per entry; an entry
  with a missing key becomes {"$missing": text}, an error only when the check uses it.
- Per check: reserved keys `enabled` (default true), `severity` (default fail),
  `quality` (fast|high, default high) and `max_findings` (report cap, 0 = all; default
  from `cmr check --max-findings`, 10); all other keys are check params.
"""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import importlib.resources
import os
import re
from pathlib import Path

import jsonschema
import yaml

from .schemas import JOB_SCHEMA, SPEC_SCHEMA, PROFILE_SCHEMA
from .textnorm import nfc



def stock_profiles_dir() -> Path:
    """Folder of the stock profiles. Installed (`pip install .`): package data `cmr/profiles`
    (pyproject maps the repo's profiles/ to package `cmr.profiles`); source tree and editable
    install: the repo's top-level profiles/ (src/cmr has no profiles folder there)."""
    pkg = Path(str(importlib.resources.files("cmr").joinpath("profiles")))
    if pkg.is_dir():
        return pkg
    return Path(__file__).resolve().parents[2] / "profiles"


PROFILES_DIR = stock_profiles_dir()
_REF = re.compile(r"\$\{([^}]*)\}")


class ConfigError(ValueError):
    pass


class MissingSpecKey(ConfigError):
    """A `${spec.*}` reference whose key the spec does not have (D4). `field` = the dotted
    spec field (e.g. audio.loudness), `missing` = the first absent part, `where` = who
    references it (set by the caller). `explain()` gives the user-facing text (D31)."""

    def __init__(self, ref: str, field: str, missing: str):
        self.ref, self.field, self.missing, self.where = ref, field, missing, None
        super().__init__(f"missing spec key ${{{ref}}} (no '{missing}')")

    def explain(self, spec_path=None, job_path=None) -> str:
        top = self.field.split(".")[0]
        yaml_path = ": ".join(self.field.split(".")) + ":"
        sheet = "the spec sheet" + (f" {spec_path}" if spec_path else "")
        lines = [f"missing spec key ${{{self.ref}}}" + (f", used by {self.where}" if self.where else ""),
                 f"  the spec has no '{self.missing}'."]
        if top in JOB_SECTIONS:   # job-level data: normally in the job file (D27, D31)
            lines.append(f"  Fill in `{yaml_path}` in the job file " + (
                f"{job_path}" if job_path else "(none is in use: pass --job, or run `cmr check` "
                f"in the folder of {JOB_FILE})") + f" or in {sheet}")
        else:
            lines.append(f"  Fill in `{yaml_path}` in {sheet}")
        if self.where and self.where.startswith("profile check"):
            lines.append("  or switch that check off in the profile (`enabled: false`).")
        lines.append("  Spec fields are explained in USERGUIDE.md, \"Spec sheets: the job facts\".")
        return "\n".join(lines)


def _load_yaml(path: Path) -> tuple[dict, str]:
    try:
        text = Path(path).read_text()
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    try:
        data = nfc(yaml.safe_load(text))     # D32: spec/job strings compared in NFC
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
    return resolve_paths(spec, Path(path).resolve().parent), sha


JOB_SECTIONS = ("job", "deliverables", "slate")
_PATH_KEYS = (("deliverables", "csv"), ("slate", "template"))   # relative to the file that sets them
JOB_FILE = "job.yaml"            # D31: found in the current folder (or --job / --job-dir)
JOB_WAIVERS = "waivers.yaml"     # D31: default waiver file next to job.yaml
_RUN_KEYS = ("spec", "waivers", "output")   # job.yaml top-level paths (D31); `profile` see below


def resolve_paths(data: dict, base: Path) -> dict:
    """deliverables.csv / slate.template given relative to `base` (the file's folder)."""
    for sec, key in _PATH_KEYS:
        v = (data.get(sec) or {}).get(key)
        if isinstance(v, str) and v and not Path(v).is_absolute():
            data[sec][key] = str((Path(base) / v).resolve())
    return data


def load_job(path: Path) -> tuple[dict, str]:
    """Job file (D27): job constants, deliverables CSV settings, slate template + expectations."""
    job, sha = _load_yaml(path)
    _validate(job, JOB_SCHEMA, f"job file {path}")
    return resolve_paths(job, Path(path).resolve().parent), sha


def find_job(job: str | None = None, job_dir: str | None = None, no_job: bool = False,
             cwd: Path | None = None) -> Path | None:
    """The job file of a run (D31): `--job PATH`, else `--job-dir DIR` (DIR/job.yaml), else
    ./job.yaml when it exists (not with `--no-job`). An explicit one must exist."""
    if job:
        p = Path(job)
    elif job_dir:
        p = Path(job_dir) / JOB_FILE
    else:
        p = Path(cwd or Path.cwd()) / JOB_FILE
        return None if no_job or not p.is_file() else p
    if not p.is_file():
        raise ConfigError(f"job file {p} not found")
    return p


def job_settings(path: Path) -> dict:
    """What a job file says about the run (D31): {path (as given), dir, id, spec, profile,
    waivers, waivers_named, output, input ({folder, pattern, pick} or None)}. Other paths are absolute (relative ones resolve from the job file's
    folder). `profile` stays a stock profile name unless it looks like a path (has `/` or ends
    in .yaml/.yml). `waivers` defaults to waivers.yaml next to the job file (it may not exist
    yet: no waivers); `waivers_named` says whether the job file set it."""
    data, _ = load_job(path)
    base = Path(path).resolve().parent
    out = {"path": str(path), "dir": str(base),
           "id": (data.get("job") or {}).get("id")}
    for k in _RUN_KEYS:   # `~` allowed; an absolute path stays as it is
        out[k] = str((base / Path(data[k]).expanduser()).resolve()) if data.get(k) else None
    prof = data.get("profile")
    if prof and ("/" in prof or Path(prof).suffix in (".yaml", ".yml")):
        prof = str((base / Path(prof).expanduser()).resolve())
    out["profile"] = prof
    inp = data.get("input")
    out["input"] = None if not inp else {
        "folder": str((base / Path(inp["folder"]).expanduser()).resolve()),
        "pattern": inp.get("pattern", "*"), "pick": inp.get("pick", "latest")}
    out["waivers_named"] = out["waivers"] is not None
    if out["waivers"] is None:
        out["waivers"] = str(base / JOB_WAIVERS)
    return out


def _merge_strict(base, add, where: str):
    """Deep merge; the same key with a DIFFERENT scalar/list value in both is a ConfigError
    (a value is never typed twice: spec and job file must not contradict each other)."""
    if isinstance(base, dict) and isinstance(add, dict):
        out = dict(base)
        for k, v in add.items():
            out[k] = _merge_strict(base[k], v, f"{where}.{k}") if k in base else copy.deepcopy(v)
        return out
    if base != add:
        raise ConfigError(f"{where} is set in both the spec ({base!r}) and the job file ({add!r})")
    return copy.deepcopy(add)


def merge_job(spec: dict, job: dict) -> dict:
    """The job file is the job-level part of the spec: merged into one spec (sections job,
    deliverables, slate); contradicting values are a ConfigError."""
    out = copy.deepcopy(spec)
    for sec in JOB_SECTIONS:
        if sec in job:
            out[sec] = _merge_strict(out.get(sec) or {}, job[sec] or {}, sec)
    return out


def build_spec(spec_path, job_path=None, deliverables_csv=None, row=None) -> tuple[dict, dict]:
    """The one merged spec: spec sheet + job file (D27) + run-time `--deliverables`/`--row`.
    `${spec.*}` refs inside the job sections are resolved here (load time, D4); `{ns.*}`
    tokens stay for run time. Returns (spec, inputs {spec: {path, sha256}, job: {...}})."""
    spec, sha = load_spec(Path(spec_path))
    inputs = {"spec": {"path": str(spec_path), "sha256": sha}}
    if job_path:
        job, jsha = load_job(Path(job_path))
        spec = merge_job(spec, job)
        inputs["job"] = {"path": str(job_path), "sha256": jsha}
    if deliverables_csv:
        spec.setdefault("deliverables", {})["csv"] = str(Path(deliverables_csv).resolve())
    if row is not None:
        spec.setdefault("deliverables", {})["row"] = str(row)
    for sec in JOB_SECTIONS:
        if sec in spec:
            try:
                spec[sec] = substitute(spec[sec], spec)
            except MissingSpecKey as e:
                e.where = f"`{sec}:` section of the spec sheet or job file"
                raise
    filename_regex(spec.get("job") or {})   # invalid regex / conflicting name keys: exit 3
    return spec, inputs


def filename_regex(job: dict) -> re.Pattern | None:
    """The compiled spec `job.filename_regex` (matched with fullmatch against the file name),
    or None. ConfigError for an invalid regex, or when job.filename / job.naming_pattern is
    given as well (only one way to say what the name must be)."""
    rx = job.get("filename_regex")
    if not rx:
        return None
    both = [k for k in ("filename", "naming_pattern") if job.get(k)]
    if both:
        raise ConfigError(f"spec job.filename_regex and job.{both[0]} are both set: give only "
                          "one (filename_regex for a pattern, filename/naming_pattern for one "
                          "exact name)")
    try:
        return re.compile(rx)
    except re.error as e:
        raise ConfigError(f"spec job.filename_regex '{rx}' is not a valid regular expression: "
                          f"{e}") from None


VERSION_GROUP = "version"   # named group of job.filename_regex that `pick: latest` sorts by
REGEX_EXAMPLE = r"^Color_6K_v(?P<version>\d+)\.mov$"


def pick_input(inp: dict, job: dict) -> dict:
    """job.yaml `input:` (D31 addendum): the file to check in `inp['folder']`. Candidates =
    names matching the glob `pattern` (fnmatch, case-sensitive, not recursive; dot-files such
    as macOS `._x` AppleDouble files ignored; NFC-compared). `pick: latest` = the HIGHEST
    number in the `version` group of `job.filename_regex` (numeric, not newest mtime).
    Candidates that do not match the regex are not considered and come back as WARN notices
    (never skipped silently). ConfigError: no folder, regex without a version group, no
    conforming candidate (lists what the glob found), two candidates with one version.
    Returns {folder, pattern, pick, picked (name), path, version, conforming, notices}."""
    folder, pattern, pick = Path(inp["folder"]), inp.get("pattern") or "*", inp.get("pick") or "latest"
    if not folder.is_dir():
        raise ConfigError(f"job input folder {folder} not found (job.yaml `input: folder:`)")
    rx = filename_regex(job)
    if rx is None or VERSION_GROUP not in rx.groupindex:
        raise ConfigError(
            f"job input `pick: {pick}` needs the spec's job.filename_regex with a named group "
            f"(?P<{VERSION_GROUP}>\\d+) holding the version number, e.g. '{REGEX_EXAMPLE}'" +
            (f" (the spec's regex '{rx.pattern}' has none)" if rx else " (the spec has none)"))
    names = sorted(n for n in os.listdir(folder) if not n.startswith(".")
                   and fnmatch.fnmatchcase(nfc(n), pattern) and (folder / n).is_file())
    notices, ok = [], {}
    for n in names:
        m = rx.fullmatch(nfc(n))
        v = m and m.group(VERSION_GROUP)
        if not m:
            why = f"does not match job.filename_regex '{rx.pattern}'"
        elif not (v and v.isascii() and v.isdigit()):
            why = f"the version group of job.filename_regex is {v!r}, not a number"
        else:
            ok.setdefault(int(v), []).append(n)
            continue
        notices.append({"severity": "WARN", "file": n,
                        "message": f"{why}: not considered (check the name)"})
    if not ok:
        found = ", ".join(names) if names else "nothing"
        raise ConfigError(f"job input: no file in {folder} matching {pattern!r} matches "
                          f"job.filename_regex '{rx.pattern}' (the pattern found: {found})")
    dup = {v: ns for v, ns in ok.items() if len(ns) > 1}
    if dup:
        raise ConfigError("job input: several files have the same version number: " + "; ".join(
            f"version {v}: {', '.join(ns)}" for v, ns in sorted(dup.items())))
    version = max(ok)
    name = ok[version][0]
    return {"folder": str(folder), "pattern": pattern, "pick": pick, "picked": name,
            "path": str(folder / name), "version": version,
            "conforming": sum(map(len, ok.values())), "notices": notices}


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
            raise MissingSpecKey(ref.strip(), ".".join(parts[1:]), ".".join(parts[1:i + 1]))
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


def is_disabled(cfg) -> bool:
    return isinstance(cfg, dict) and cfg.get("enabled", True) is False


MISSING = "$missing"   # marker of a lazy param entry whose ${spec.*} key the spec lacks


def _lazy_params() -> dict[str, tuple[str, ...]]:
    """{check_id: Check.lazy_params} of the check plugins (imported late: engine imports
    this module)."""
    from . import engine
    engine.load_checks()
    return {cid: c.lazy_params for cid, c in engine.REGISTRY.items() if c.lazy_params}


def _substitute_check(cfg, spec: dict, lazy: tuple[str, ...]):
    """substitute() for one enabled check entry. Params named in the check's `lazy_params`
    (mappings) are substituted entry by entry: an entry whose ${spec.*} key is missing becomes
    {MISSING: "<error text>"} instead of failing the load; the check raises it only if it
    actually uses that entry (e.g. filename `tags.audio_tag` on a picture-only spec)."""
    if not lazy or not isinstance(cfg, dict):
        return substitute(cfg, spec)
    out = {}
    for k, v in cfg.items():
        if k in lazy and isinstance(v, dict):
            out[k] = {}
            for name, entry in v.items():
                try:
                    out[k][name] = substitute(entry, spec)
                except MissingSpecKey as e:
                    out[k][name] = {MISSING: str(e)}
        else:
            out[k] = substitute(v, spec)
    return out


def resolve_profile(name_or_path: str, spec: dict) -> tuple[dict, list[str]]:
    """Merged profile with ${spec.*} substituted. Lazy for checks with `enabled: false`: their
    entries keep the refs verbatim (never looked up, so a spec without e.g. an audio section
    loads a profile whose audio checks are switched off). Enabled checks: a missing key is a
    ConfigError (D4), except inside entries of a check's `lazy_params` (_substitute_check)."""
    merged, shas = load_profile_raw(name_or_path)
    lazy = _lazy_params()
    out = {}
    for k, v in merged.items():
        if k == "checks":
            out[k] = {}
            for cid, cfg in (v or {}).items():
                try:
                    out[k][cid] = (copy.deepcopy(cfg) if is_disabled(cfg) else
                                   _substitute_check(cfg, spec, lazy.get(cid, ())))
                except MissingSpecKey as e:
                    e.where = f"profile check {cid} (profile {merged.get('name')!r})"
                    raise
        else:
            try:
                out[k] = substitute(v, spec)
            except MissingSpecKey as e:
                e.where = f"profile key {k!r} (profile {merged.get('name')!r})"
                raise
    return out, shas
