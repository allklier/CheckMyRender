"""Diagnostic log (D29): one rotating file, a fixed line format, an environment snapshot per run
and redaction of private data, so problems on other people's machines can be debugged from the
log alone. Conventions mirror the LTX_OFX log (one file, rotate at start, same line layout):

  2026-10-26 14:03:11.042 INFO  engine[41233/41233] run=3fa2c1 file=9b1e07aa | main pass: ...

comp = module or check id; run = random id per `cmr` invocation; file = salted hash of the file
being checked ("-" outside a file). Levels debug|info|warn|error|off (`--log-level`, env
CMR_LOG_LEVEL). File: $CMR_LOG_DIR, else $XDG_CACHE_HOME/cmr/logs, else ~/.cache/cmr/logs
(macOS: ~/Library/Logs/cmr, D32) (`--log-file` overrides); rotated at setup when >= 10 MB, 5 old files kept. Each line is one
O_APPEND write, so several processes can share the file (pid in every line).

Privacy (decided): by default private data (paths, file/project names, user/host names, slate
text, CSV/job values) is replaced by `redact(kind, value)` tokens: a salted sha256 prefix (salt
kept in the log dir, so the same input gives the same token across runs) plus non-private facts
(extension, length). Every value redacted once is also scrubbed from every later line (stderr,
tracebacks), and the home directory becomes "~". `--log-private` / CMR_LOG_PRIVATE=1 logs
verbatim. Image/audio data is never logged. Logging never raises into the caller.
"""
from __future__ import annotations

import contextlib
import fcntl
import getpass
import hashlib
import logging
import os
import platform
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path, PurePath

# ON at INFO by default while the tool is young; will later flip to False (OFF by default,
# switched on with --log-level / CMR_LOG_LEVEL when a problem has to be debugged).
LOG_DEFAULT_ENABLED = True
DEFAULT_LEVEL = "info"
MAX_BYTES = 10 * 1024 ** 2
BACKUPS = 5
LOG_NAME = "cmr.log"
SALT_NAME = "salt"          # per-installation redaction salt (never put into a support bundle)
LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warn": logging.WARNING,
          "error": logging.ERROR, "off": None}
_NAMES = {logging.DEBUG: "DEBUG", logging.INFO: "INFO ", logging.WARNING: "WARN ",
          logging.ERROR: "ERROR", logging.CRITICAL: "ERROR"}
STDERR_LINES = 5            # external commands: first/last N stderr lines are logged
# binary directories that name no person or project: tool paths there are logged verbatim
SYSTEM_DIRS = {"/usr/bin", "/bin", "/usr/local/bin", "/usr/sbin", "/opt/homebrew/bin"}

ROOT = logging.getLogger("cmr")
ROOT.addHandler(logging.NullHandler())   # library use without setup(): silent, no lastResort
ROOT.propagate = False
ROOT.setLevel(100)                       # disabled until setup()

_state = {"run": "-", "file": "-", "private": False, "path": None, "level": "off",
          "salt": "", "salt_source": "none"}
_registry: dict[str, str] = {}           # private string -> token (scrubbed from every line)
_pattern: list = [None]                  # compiled scrub regex cache
_static: dict = {}                       # per-process cache of slow environment facts


def get(comp: str) -> logging.Logger:
    """Logger whose lines show `comp` (module name or check id)."""
    return logging.getLogger(f"cmr.{comp}")


def log_dir() -> Path:
    if os.environ.get("CMR_LOG_DIR"):
        return Path(os.environ["CMR_LOG_DIR"])
    if os.environ.get("XDG_CACHE_HOME"):
        return Path(os.environ["XDG_CACHE_HOME"]) / "cmr" / "logs"
    if sys.platform == "darwin":     # D32: where Console.app and users look for logs
        return Path.home() / "Library" / "Logs" / "cmr"
    return Path.home() / ".cache" / "cmr" / "logs"


def log_path(log_file: str | None = None) -> Path:
    return Path(log_file) if log_file else log_dir() / LOG_NAME


def private() -> bool:
    return _state["private"]


# --- handlers / formatter -----------------------------------------------------------------
class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        t = record.created
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)) + f".{int(t * 1000) % 1000:03d}"
        comp = record.name
        for pre in ("cmr.", "check."):
            comp = comp[len(pre):] if comp.startswith(pre) else comp
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return (f"{stamp} {_NAMES.get(record.levelno, 'INFO ')} {comp}[{record.process}/"
                f"{threading.get_native_id()}] run={_state['run']} file={_state['file']} | "
                f"{msg if _state['private'] else scrub(msg)}")

    def formatException(self, ei) -> str:
        """Default mode: quoted values in the exception message lines are redacted (source
        lines and frames stay readable)."""
        text = super().formatException(ei)
        if _state["private"]:
            return text
        return "\n".join(l if l.startswith((" ", "Traceback", "During handling", "The above"))
                         else redact_quoted(l) for l in text.splitlines())


class _AppendHandler(logging.Handler):
    """One os.write per line on an O_APPEND fd (lines of concurrent processes do not mix)."""

    def __init__(self, path: Path):
        super().__init__()
        self.fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)

    def emit(self, record):
        try:
            os.write(self.fd, (self.format(record) + "\n").encode("utf-8", "replace"))
        except Exception:   # noqa: BLE001 - logging must never break a run
            pass

    def close(self):
        with contextlib.suppress(OSError):
            os.close(self.fd)
        super().close()


class _StderrHandler(logging.StreamHandler):
    def handleError(self, record):   # never print logging tracebacks into the user's run
        pass


def _rotate(path: Path) -> None:
    """LTX style: at start only, when the file reached MAX_BYTES; cmr.log.1 .. .BACKUPS.
    A lock file serialises two processes starting at the same moment."""
    with open(path.parent / ".rotate.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            if not path.exists() or path.stat().st_size < MAX_BYTES:
                return
            with contextlib.suppress(OSError):
                os.remove(f"{path}.{BACKUPS}")
            for i in range(BACKUPS - 1, 0, -1):
                with contextlib.suppress(OSError):
                    os.rename(f"{path}.{i}", f"{path}.{i + 1}")
            os.rename(path, f"{path}.1")
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _load_salt(d: Path | None) -> None:
    if d is not None:
        try:
            f = d / SALT_NAME
            if not f.exists():
                with contextlib.suppress(FileExistsError):
                    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    os.write(fd, secrets.token_hex(16).encode())
                    os.close(fd)
            s = f.read_text().strip()
            if s:
                _state.update(salt=s, salt_source="persistent")
                return
        except OSError:
            pass
    _state.update(salt=secrets.token_hex(16), salt_source="ephemeral (log dir unwritable)")


def setup(level: str | None = None, log_file: str | None = None,
          private_mode: bool | None = None) -> Path | None:
    """Configure the log for one `cmr` invocation. Returns the log file (None: off/stderr)."""
    for h in ROOT.handlers[:]:
        ROOT.removeHandler(h)
        h.close()
    ROOT.addHandler(logging.NullHandler())
    name = (level or os.environ.get("CMR_LOG_LEVEL")
            or (DEFAULT_LEVEL if LOG_DEFAULT_ENABLED else "off")).lower()
    name = {"warning": "warn"}.get(name, name)
    if name not in LEVELS:
        name = DEFAULT_LEVEL
    if private_mode is None:
        private_mode = os.environ.get("CMR_LOG_PRIVATE", "").lower() in ("1", "true", "yes", "on")
    _state.update(run=secrets.token_hex(3), file="-", private=bool(private_mode), level=name,
                  path=None)
    _registry.clear()
    _pattern[0] = None
    if LEVELS[name] is None:
        ROOT.setLevel(100)
        return None
    path = log_path(log_file)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate(path)
        h = _AppendHandler(path)
        _state["path"] = path
    except OSError as e:
        h = _StderrHandler(sys.stderr)
        h.setLevel(logging.WARNING)
        sys.stderr.write(f"cmr: cannot write the log {path} ({e}); logging warnings to stderr\n")
        path = None
    h.setFormatter(_Formatter())
    ROOT.addHandler(h)
    ROOT.setLevel(LEVELS[name])
    _load_salt(path.parent if path else None)
    if not private_mode:
        with contextlib.suppress(Exception):
            _register(getpass.getuser(), "user")
        host = socket.gethostname()
        _register(host, "host")
        _register(host.split(".")[0], "host")
    return path


def run_id() -> str:
    """Id of this invocation (the `run=` field of every line; '-' before setup())."""
    return _state["run"]


def set_file(path) -> str:
    """Set the file id of the following lines (None -> '-'). Returns the id."""
    _state["file"] = "-" if path is None else _hash(str(Path(path).resolve()))
    return _state["file"]


# --- redaction ----------------------------------------------------------------------------
def _hash(s: str) -> str:
    return hashlib.sha256((_state["salt"] + s).encode("utf-8", "replace")).hexdigest()[:8]


# kinds whose values are scrubbed from later lines; "value"/"row" (profile params, quoted message
# parts, CSV cells) are only tokenised where passed: they are often ordinary words or numbers
SCRUB_KINDS = {"path", "name", "text", "host", "user", "dir", "arg"}


def _register(s: str, kind: str, token: str | None = None) -> str:
    token = token or f"<{kind}#{_hash(s)}>"
    if (kind in SCRUB_KINDS and len(s) >= 4 and re.search(r"[A-Za-z]", s)
            and s not in _registry):
        _registry[s] = token
        _pattern[0] = None
    return token


def redact(kind: str, value) -> str:
    """The ONE helper for private values. Private mode: verbatim. Default: a stable token,
    e.g. path -> `<path#1a2b3c4d.mov>`, tool -> `<dir#..>/ffmpeg`, text/value/name/row ->
    `<text#1a2b3c4d len=12>`. The value is also scrubbed from every later log line."""
    if value is None or _state["private"]:
        return str(value)
    s = str(value)
    try:
        if kind == "path":
            # the full value and the base name (with extension) are scrubbed from later lines;
            # not the bare stem, which can be an ordinary word ("default", "black")
            p = PurePath(s)
            if p.name and p.name != s:
                _register(p.name, "name", f"<name#{_hash(p.name)}{p.suffix}>")
            return _register(s, kind, f"<path#{_hash(s)}{p.suffix}>")
        if kind == "tool":
            p = PurePath(s)
            if str(p.parent) in (".", "") or str(p.parent) in SYSTEM_DIRS:
                return s
            return f"{_register(str(p.parent), 'dir')}/{p.name}"
        tok = f"<{kind}#{_hash(s)} len={len(s)}>"
        _register(s, kind, tok)
        return tok
    except Exception:   # noqa: BLE001
        return f"<{kind}>"


def scrub(text: str) -> str:
    """Remove every registered private string (and the home directory) from text."""
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    if not _registry:
        return text
    if _pattern[0] is None:
        keys = sorted(_registry, key=len, reverse=True)
        _pattern[0] = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, keys)) + r")(?!\w)")
    return _pattern[0].sub(lambda m: _registry.get(m.group(0), "<redacted>"), text)


_QUOTED = re.compile(r"'([^'\n]{1,500})'|\"([^\"\n]{1,500})\"")


def redact_quoted(text: str) -> str:
    """Messages that quote values (finding messages, config errors use repr()): every quoted
    part becomes a redacted token in default mode."""
    if _state["private"]:
        return text
    return _QUOTED.sub(lambda m: "'" + redact("value", m.group(1) or m.group(2)) + "'", str(text))


def redact_cmd(cmd: list) -> str:
    """Command line with private parts redacted: binary dir, inputs/outputs (anything after -i,
    containing a path separator or naming an existing file) and `file=` paths in filtergraphs."""
    out, prev = [], None
    for i, a in enumerate(map(str, cmd)):
        if i == 0:
            a = redact("tool", a)
        elif prev in ("-filter_complex", "-vf", "-af"):
            a = re.sub(r"file=([^:,\[;]+)", lambda m: "file=" + redact("path", m.group(1)), a)
        elif not a.startswith("-") and (prev == "-i" or os.sep in a or os.path.exists(a)):
            a = redact("path", a)
        out.append(a)
        prev = a
    return shlex.join(out)


def redact_argv(argv: list[str], safe_opts: set[str], safe_words: set[str]) -> str:
    """cmr's own command line: options kept, values of `safe_opts` and words in `safe_words`
    (subcommands, built-in profile names) kept, everything else redacted."""
    out, prev = [], None
    for a in argv:
        if a.startswith("-"):
            opt, eq, val = a.partition("=")
            out.append(a if not eq or opt in safe_opts else f"{opt}={redact('arg', val)}")
        elif prev in safe_opts or a in safe_words:
            out.append(a)
        else:
            out.append(redact("path" if os.sep in a or os.path.exists(a) else "arg", a))
        prev = a
    return " ".join(out)


# --- helpers for instrumentation ----------------------------------------------------------
def tail(stderr, n: int = STDERR_LINES) -> str:
    """'' or '; stderr (K lines):' + the first/last n lines (each cut at 300 chars)."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    lines = [l[:300] for l in (stderr or "").splitlines() if l.strip()]
    if not lines:
        return ""
    total = len(lines)
    if total > 2 * n:
        lines = lines[:n] + [f"... ({total - 2 * n} lines omitted) ..."] + lines[-n:]
    return f"; stderr ({total} lines):\n    " + "\n    ".join(lines)


def run(logger: logging.Logger, cmd: list, what: str | None = None, level: int = logging.INFO,
        warn_stderr: bool = True, **kw) -> subprocess.CompletedProcess:
    """subprocess.run(cmd, **kw) with one log line: exit code, duration, truncated stderr
    (WARN on failure, or with stderr output when warn_stderr). Full command at DEBUG.
    Exceptions of subprocess.run are logged and re-raised unchanged."""
    name = what or os.path.basename(str(cmd[0]))
    with contextlib.suppress(Exception):
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("exec %s: %s", name, redact_cmd(cmd))
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, **kw)
    except subprocess.TimeoutExpired:
        logger.warning("%s: timed out after %.1f s", name, time.monotonic() - t0)
        raise
    except OSError as e:
        logger.warning("%s: cannot start: %s", name, e)
        raise
    with contextlib.suppress(Exception):
        err = tail(p.stderr) if kw.get("capture_output") or kw.get("stderr") else ""
        lvl = logging.WARNING if p.returncode != 0 or (warn_stderr and err) else level
        if logger.isEnabledFor(lvl):
            logger.log(lvl, "%s: exit %s in %.3f s%s", name, p.returncode, time.monotonic() - t0, err)
    return p


@contextlib.contextmanager
def phase(logger: logging.Logger, name: str, level: int = logging.INFO):
    """Log `name: X.XXX s` when the block ends (`(failed)` if it raised)."""
    t0, ok = time.monotonic(), False
    try:
        yield
        ok = True
    finally:
        if logger.isEnabledFor(level):
            logger.log(level, "phase %s: %.3f s%s", name, time.monotonic() - t0, "" if ok else " (failed)")


def memory() -> str:
    """RSS / peak of this process (Linux /proc; elsewhere the peak from getrusage) and the
    peak of finished children."""
    import resource
    unit = 1024 * 1024 if sys.platform == "darwin" else 1024    # ru_maxrss: bytes on macOS, KB on Linux
    parts = []
    with contextlib.suppress(OSError):
        st = dict(l.split(":", 1) for l in Path("/proc/self/status").read_text().splitlines() if ":" in l)
        parts.append(f"rss {int(st['VmRSS'].split()[0]) // 1024} MB peak "
                     f"{int(st['VmHWM'].split()[0]) // 1024} MB")
    if not parts:
        with contextlib.suppress(Exception):
            parts.append(f"peak {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // unit} MB")
    with contextlib.suppress(Exception):
        parts.append(f"children peak {resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // unit} MB")
    return ", ".join(parts) or "memory n/a"


def sha256_file(path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as e:
        return f"unreadable ({e.strerror})"


# --- environment snapshot -----------------------------------------------------------------
def _cmd_out(cmd: list, timeout: float = 5) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (p.stdout or p.stderr or "").strip()
    return out if p.returncode == 0 and out else None


def sysctl(name: str) -> str | None:
    """`sysctl -n NAME` (macOS facts; None when absent or failing). /usr/sbin is not always on
    PATH for scripted shells, hence the fallback."""
    binary = shutil.which("sysctl") or "/usr/sbin/sysctl"
    return _cmd_out([binary, "-n", name])


def performance_cores() -> int | None:
    """macOS on Apple Silicon: physical performance cores (hw.perflevel0), else None."""
    v = sysctl("hw.perflevel0.physicalcpu") if sys.platform == "darwin" else None
    return int(v) if v and v.isdigit() else None


def _darwin_facts() -> dict:
    """Chip, P/E cores, macOS version, Rosetta flag, RAM (D32). Each value guarded."""
    pc, ec = sysctl("hw.perflevel0.physicalcpu"), sysctl("hw.perflevel1.physicalcpu")
    mem = sysctl("hw.memsize")
    rosetta = {"1": "YES (translated: cmr refuses Rosetta, D32)", "0": "no"}.get(
        sysctl("sysctl.proc_translated") or "", "n/a")
    return {"chip": sysctl("machdep.cpu.brand_string") or "unknown",
            "cores": f"{pc or '?'} performance + {ec or '?'} efficiency",
            "macos": platform.mac_ver()[0] or "unknown", "arch": platform.machine(),
            "rosetta": rosetta,
            "ram": f"{int(mem) / 1024 ** 3:.1f} GB" if mem and mem.isdigit() else None}


def _static_facts() -> dict:
    """Slow, machine-wide facts, cached per process (tests call main() many times)."""
    if not _static:
        from . import __version__
        git = _cmd_out(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"])
        gpu = (_cmd_out(["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
                         "--format=csv,noheader"]) if shutil.which("nvidia-smi") else None)
        mem = {}
        with contextlib.suppress(OSError):
            mem = dict(l.split(":", 1) for l in Path("/proc/meminfo").read_text().splitlines() if ":" in l)
        try:
            ram = f"{os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / 1024 ** 3:.1f} GB"
        except (ValueError, OSError, AttributeError):
            ram = "unknown"
        if "MemAvailable" in mem:
            ram += f" (available {int(mem['MemAvailable'].split()[0]) / 1024 ** 2:.1f} GB)"
        libc = platform.libc_ver()
        if sys.platform == "darwin":
            mac = _darwin_facts()
            ram = mac.pop("ram") or ram
            _static["mac"] = mac
        _static.update(version=__version__, git=(git or "unknown").replace("\n", " "),
                       os=platform.platform(), kernel=platform.release(),
                       libc=" ".join(x for x in libc if x) or "n/a", ram=ram,
                       gpu=(gpu or "none found (nvidia-smi absent or failed)").replace("\n", "; "))
    return _static


def _tool(binary: str, version_arg: str = "-version") -> str:
    path = shutil.which(binary)
    if not path:
        return f"NOT FOUND ({redact('tool', binary)})"
    key = ("tool", path, version_arg)
    if key not in _static:
        out = _cmd_out([path, version_arg])
        _static[key] = out.splitlines()[0] if out else "version unknown"
    return f"{redact('tool', path)}: {_static[key]}"


def environment(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", filters: set | None = None) -> list[str]:
    """`key: value` lines of the environment snapshot (log and support bundle environment.txt).
    Never raises; missing optional tools are stated, not errors."""
    lines: list[str] = []

    def add(k, fn):
        try:
            lines.append(f"{k}: {fn()}")
        except Exception as e:   # noqa: BLE001
            lines.append(f"{k}: unavailable ({type(e).__name__}: {e})")

    s = _static_facts()
    add("cmr", lambda: f"{s['version']} (git {s['git']})")
    add("privacy", lambda: "PRIVATE (verbatim paths, names, slate text)" if private() else
        f"default (private data replaced by salted hashes; salt {_state['salt_source']})")
    add("log", lambda: f"{_state['path'] or 'stderr/off'} level={_state['level']}")
    add("host", lambda: redact("host", socket.gethostname()))
    add("user", lambda: redact("user", getpass.getuser()))
    add("os", lambda: f"{s['os']} kernel {s['kernel']} libc {s['libc']}")
    if "mac" in s:
        add("macos", lambda: (f"{s['mac']['macos']} {s['mac']['arch']}, chip {s['mac']['chip']}, "
                              f"{s['mac']['cores']} cores, Rosetta {s['mac']['rosetta']}"))
    add("python", lambda: f"{platform.python_version()} {redact('tool', sys.executable)}")
    add("ffmpeg", lambda: _tool(ffmpeg))
    add("ffprobe", lambda: _tool(ffprobe))

    def _filters():
        from . import engine
        fs = filters if filters is not None else engine.available_filters(ffmpeg)
        engine.load_checks()
        need = {f for c in engine.REGISTRY.values() for f in c.requires_filters}
        digest = hashlib.sha256("\n".join(sorted(fs)).encode()).hexdigest()[:12]
        miss = sorted(need - set(fs))
        bad = "; plugin import errors: " + ", ".join(engine.IMPORT_ERRORS) if engine.IMPORT_ERRORS else ""
        return f"{len(fs)} (sha256 {digest}); missing required: {', '.join(miss) or 'none'}{bad}"
    add("filters", _filters)

    def _tess():
        from .checks.slate import ocr
        try:
            return _tool(ocr.resolve_bin(ocr.tesseract_bin({})), "--version")
        except ocr.OCRUnavailable:
            return "NOT FOUND (slate OCR checks will report ERROR)"
    add("tesseract", _tess)
    add("cpu", lambda: f"{os.cpu_count()} logical"
        + (f", {len(os.sched_getaffinity(0))} usable" if hasattr(os, "sched_getaffinity") else ""))
    add("ram", lambda: s["ram"])
    add("gpu", lambda: s["gpu"])

    add("limits", limits)
    return lines if private() else [scrub(l) for l in lines]


def limits() -> str:
    """Effective priority and memory limits of this process (inherited by ffmpeg children)."""
    try:
        import resource
        soft = resource.getrlimit(resource.RLIMIT_AS)[0]
        io = _cmd_out(["ionice", "-p", str(os.getpid())]) if shutil.which("ionice") else "n/a"
        if sys.platform == "darwin":
            return f"nice {os.nice(0)}, ionice n/a, no memory cap on macOS (RLIMIT_AS not enforced)"
        return (f"nice {os.nice(0)}, ionice {io}, RLIMIT_AS "
                f"{'unlimited' if soft == resource.RLIM_INFINITY else f'{soft / 1024 ** 3:.1f} GB'}")
    except Exception as e:   # noqa: BLE001
        return f"limits unavailable ({e})"


def snapshot(logger: logging.Logger, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
             filters: set | None = None) -> None:
    """Log the environment snapshot (INFO). Never raises."""
    if not logger.isEnabledFor(logging.INFO):
        return
    with contextlib.suppress(Exception):
        for line in environment(ffmpeg, ffprobe, filters):
            logger.info("env %s", line)
