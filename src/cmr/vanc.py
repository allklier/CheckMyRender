"""SMPTE 436M VANC caption detection (MXF data tracks), read-only and bounded.

ffmpeg 6.1.1 / 5.1.10 have no decoder for 436M. ffprobe shows the track as a data stream with
tag `data_type` = 'vbi_vanc_smpte_436M' and prints the raw packet bytes with `-show_data`
(hexdump text). `read_436m` reads only the first `PACKETS` packets of that stream
(`-select_streams <index> -show_packets -show_data -read_intervals %+#N`): measured 0.3 s on a
26 GB file (ffmpeg 6.1.1). Only counts are kept; caption text is never stored or logged.

436M ANC element (one ffmpeg packet; SMPTE 436M-2006, layout checked against a real master):
    u16 packet count, then per ANC packet: u16 line number, u8 wrapping type,
    u8 payload sample coding, u16 sample count, u32 array count, u32 array element size (1),
    array count bytes (padded to a multiple of 4).
Payload (8-bit codings 4/5/6/10/11/12): DID, SDID, DC, DC user words, checksum.
10-bit codings 7/8/9 (three 10-bit samples per big-endian u32): low 8 bits kept; UNVERIFIED
(no such file seen). DID 0x61 SDID 0x01 = CEA-708 CDP (SMPTE 334-2), SDID 0x02 = CEA-608 (334-1).
CDP: 0x96 0x69, length, frame rate, flags (0x80 time code, 0x40 ccdata, 0x20 svcinfo,
0x02 caption service active), u16 sequence counter, [0x71 + 4 bytes time code],
0x72 + (0xE0 | cc_count) + cc_count x 3 bytes: (0xF8 | cc_valid<<2 | cc_type, b1, b2).
cc_type 0/1 = 608 compatibility bytes (field 1/2; 0x80 0x80 = null padding), 2/3 = DTVCC (708).
"""
from __future__ import annotations

import json
import subprocess

from . import log

LOG = log.get("media")

DATA_TYPE_436M = "vbi_vanc_smpte_436M"
PACKETS = 30            # first N packets of the data track (about 1 s); bounded IO
TIMEOUT = 60

_8BIT = {4, 5, 6, 10, 11, 12}
_10BIT = {7, 8, 9}


def hexdump_to_bytes(dump: str) -> bytes:
    """ffprobe `-show_data` text ('00000000: 0001 0009 ...  ascii') -> bytes. The hex part is
    the fixed 39-character column after the 10-character offset (ASCII column ignored)."""
    out = bytearray()
    for line in dump.splitlines():
        if len(line) < 11 or line[8:10] != ": ":
            continue
        out += bytes.fromhex(line[10:49].replace(" ", ""))
    return bytes(out)


def _unpack10(b: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(b) - 3, 4):
        w = int.from_bytes(b[i:i + 4], "big")
        out += bytes(((w >> 20) & 0xFF, (w >> 10) & 0xFF, w & 0xFF))
    return bytes(out)


def anc_packets(data: bytes) -> list[dict]:
    """One 436M element -> [{line, coding, did, sdid, udw}] (truncated data ends the list)."""
    out = []
    if len(data) < 2:
        return out
    n, pos = int.from_bytes(data[0:2], "big"), 2
    for _ in range(n):
        if pos + 14 > len(data):
            break
        line = int.from_bytes(data[pos:pos + 2], "big")
        coding = data[pos + 3]
        count = int.from_bytes(data[pos + 4:pos + 6], "big")
        size = int.from_bytes(data[pos + 6:pos + 10], "big") * int.from_bytes(data[pos + 10:pos + 14], "big")
        payload = data[pos + 14:pos + 14 + size]
        pos += 14 + size
        if coding in _10BIT:
            payload = _unpack10(payload)[:count]
        elif coding in _8BIT:
            payload = payload[:count]
        else:
            continue
        if len(payload) < 3:
            continue
        dc = payload[2]
        out.append({"line": line, "coding": coding, "did": payload[0], "sdid": payload[1],
                    "udw": payload[3:3 + dc]})
    return out


def parse_cdp(udw: bytes) -> dict | None:
    """CEA-708 CDP -> {active, cc608_valid, cc608_nonnull, cc708_valid}; None if not a CDP."""
    if len(udw) < 7 or udw[0:2] != b"\x96\x69":
        return None
    flags, pos = udw[4], 7
    r = {"active": bool(flags & 0x02), "cc608_valid": 0, "cc608_nonnull": 0, "cc708_valid": 0}
    if flags & 0x80:
        if pos < len(udw) and udw[pos] == 0x71:
            pos += 5
        else:
            return r
    if not flags & 0x40 or pos + 1 >= len(udw) or udw[pos] != 0x72:
        return r
    cc_count = udw[pos + 1] & 0x1F
    pos += 2
    for i in range(cc_count):
        t = udw[pos + 3 * i:pos + 3 * i + 3]
        if len(t) < 3:
            break
        valid, typ = bool(t[0] & 0x04), t[0] & 0x03
        if not valid:
            continue
        if typ in (0, 1):
            r["cc608_valid"] += 1
            if (t[1] & 0x7F, t[2] & 0x7F) != (0, 0):
                r["cc608_nonnull"] += 1
        else:
            r["cc708_valid"] += 1
    return r


def summarize(packets: list[bytes]) -> dict:
    """Counts over the sampled 436M packets (no caption text)."""
    s = {"packets_read": len(packets), "anc_packets": 0, "cdp": 0, "cdp_active": 0,
         "cc608_valid": 0, "cc608_nonnull": 0, "cc708_valid": 0, "s334_608": 0}
    for data in packets:
        for a in anc_packets(data):
            s["anc_packets"] += 1
            if a["did"] != 0x61:
                continue
            if a["sdid"] == 0x02:
                s["s334_608"] += 1
            elif a["sdid"] == 0x01 and (c := parse_cdp(a["udw"])) is not None:
                s["cdp"] += 1
                s["cdp_active"] += c["active"]
                for k in ("cc608_valid", "cc608_nonnull", "cc708_valid"):
                    s[k] += c[k]
    return s


def read_436m(path, stream_index: int, ffprobe_bin: str = "ffprobe", packets: int = PACKETS) -> dict:
    """Read the first `packets` packets of one 436M data stream and summarise them.
    Returns the summary plus `stream`; on failure {"stream", "error"}."""
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-select_streams", str(stream_index),
           "-show_packets", "-show_data", "-read_intervals", f"%+#{packets}",
           "-show_entries", "packet=stream_index,pts,data", str(path)]
    try:
        p = log.run(LOG, cmd, "ffprobe 436M packets", capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"stream": stream_index, "error": f"ffprobe timed out after {TIMEOUT}s reading 436M packets"}
    if p.returncode != 0:
        return {"stream": stream_index, "error": f"ffprobe failed reading 436M packets: {p.stderr.strip()[:200]}"}
    try:
        pk = json.loads(p.stdout).get("packets", [])
    except json.JSONDecodeError as e:
        return {"stream": stream_index, "error": f"bad ffprobe JSON: {e}"}
    s = summarize([hexdump_to_bytes(x.get("data", "")) for x in pk
                   if x.get("stream_index") == stream_index][:packets])
    s["stream"] = stream_index
    LOG.info("436M stream %d: %s", stream_index,
             ", ".join(f"{k}={v}" for k, v in s.items() if k != "stream"))
    return s
