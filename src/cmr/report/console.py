"""Human-readable console report. Locations are timecode (plus file frame number)."""
from __future__ import annotations


def _loc(fd: dict) -> str:
    if fd["frame_start"] is None:
        return ""
    if fd["frame_end"] is None or fd["frame_end"] == fd["frame_start"]:
        return f" @ {fd['timecode_start']} (frame {fd['frame_start']})"
    return (f" @ {fd['timecode_start']}-{fd['timecode_end']} "
            f"(frames {fd['frame_start']}-{fd['frame_end']})")


def scope(w: dict) -> str:
    """Category waiver scope, e.g. 'video.blanking kind=dark_edge edge=top frames 30-49 <= WARN'."""
    s = " ".join([w["check_id"]] + [f"{k}={w[k]}" for k in ("kind", "edge", "channel") if w.get(k) is not None])
    if w.get("frame_start") is not None:
        s += f" frames {w['frame_start']}-{w['frame_end']}"
    return f"{s} <= {str(w['max_severity']).upper()}"


def render(report: dict, verbose: bool = False) -> str:
    lines = []
    for f in report["files"]:
        lines.append(f"{f['worst']:7} {f['path']}")
        tl = f.get("timeline")
        if tl:
            lines.append(f"        {tl['fps']} fps, {tl['file_frames']} frames, start TC "
                         f"{tl['start_timecode']} ({tl['timecode_source']})")
        by_check: dict[str, list] = {}
        for fd in f["findings"]:
            by_check.setdefault(fd["check_id"], []).append(fd)
        extra = [x["check_id"] for x in f.get("waived_by_category", []) + f.get("suppressed", [])]
        for cid in list(by_check) + sorted(set(extra) - set(by_check)):
            for fd in by_check.get(cid, []):
                w = fd.get("waiver")
                if fd["severity"] == "PASS" and not fd.get("waiver_ref") and not verbose:
                    continue   # waived findings are never hidden
                fast = " [fast mode]" if fd.get("quality") == "fast" else ""
                sev = "WAIVED" if fd.get("waiver_ref") else fd["severity"]
                lines.append(f"  {sev:7} {fd['check_id']}{fast}: {fd['message']}{_loc(fd)}")
                if fd.get("waiver_ref"):
                    lines.append(f"          was {fd['raw_severity']}; waiver {w['id']}: {w['reason']}")
                elif w:
                    lines.append(f"          {w['note']} (reason was: {w['reason']})")
                if w and w.get("fingerprint_note"):
                    lines.append(f"          waiver {w['id']}: {w['fingerprint_note']}")
            for x in f.get("waived_by_category", []):
                if x["check_id"] == cid:
                    lines.append(f"  {'WAIVED':7} {cid}: {x['count']} finding(s) by category waiver "
                                 f"{x['waiver_id']} (worst was {x['worst_raw_severity']}){_loc(x)}")
            for x in f.get("suppressed", []):
                if x["check_id"] == cid:
                    lines.append(f"  ({x['count']} additional {cid} findings suppressed; worst: "
                                 f"{x['worst_severity']})")
    s = report["summary"]
    counts = ", ".join(f"{k} {v}" for k, v in s["counts"].items() if v)
    sup = f" ({s['suppressed']} not listed but counted; --max-findings 0 lists all)" if s.get("suppressed") else ""
    if s.get("job_id"):
        lines.append(f"JOB {s['job_id']}")
    lines.append(f"RESULT {s['worst']} (exit {s['exit_code']}): {counts}{sup}")
    wv = report.get("waivers")
    if wv:
        keys = ("applied", "content_changed", "stale", "expired") + (("foreign",) if wv.get("foreign") else ())
        lines.append(f"WAIVERS {wv['file']}: " + ", ".join(
            f"{k.replace('_', ' ')} {len(wv[k])}" for k in keys))
        for w in wv["applied"]:
            if w.get("type") == "category":
                lines.append(f"  category {w['id']} {scope(w)}: {w['count']} finding(s) waived: {w['reason']}")
        for k in ("stale", "expired"):
            for w in wv[k]:
                if w.get("type") == "category":
                    lines.append(f"  {k}: {w['id']} category {scope(w)}: {w['reason']}")
                else:
                    lines.append(f"  {k}: {w['id']} {w['check_id']} @ {w['timecode_start']}-"
                                 f"{w['timecode_end']} (frames {w['frame_start']}-{w['frame_end']}): {w['reason']}")
        for w in wv.get("foreign", []):
            lines.append(f"  foreign (job {w['job']}, not applied): {w['id']} {w['check_id']}: {w['reason']}")
    return "\n".join(lines) + "\n"
