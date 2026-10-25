"""Human-readable console report. Locations are timecode (plus file frame number)."""
from __future__ import annotations


def _loc(fd: dict) -> str:
    if fd["frame_start"] is None:
        return ""
    if fd["frame_end"] is None or fd["frame_end"] == fd["frame_start"]:
        return f" @ {fd['timecode_start']} (frame {fd['frame_start']})"
    return (f" @ {fd['timecode_start']}-{fd['timecode_end']} "
            f"(frames {fd['frame_start']}-{fd['frame_end']})")


def render(report: dict, verbose: bool = False) -> str:
    lines = []
    for f in report["files"]:
        lines.append(f"{f['worst']:7} {f['path']}")
        tl = f.get("timeline")
        if tl:
            lines.append(f"        {tl['fps']} fps, {tl['file_frames']} frames, start TC "
                         f"{tl['start_timecode']} ({tl['timecode_source']})")
        for fd in f["findings"]:
            if fd["severity"] == "PASS" and not verbose:
                continue
            lines.append(f"  {fd['severity']:7} {fd['check_id']}: {fd['message']}{_loc(fd)}")
    s = report["summary"]
    counts = ", ".join(f"{k} {v}" for k, v in s["counts"].items() if v)
    lines.append(f"RESULT {s['worst']} (exit {s['exit_code']}): {counts}")
    return "\n".join(lines) + "\n"
