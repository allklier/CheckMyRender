#!/usr/bin/env python3
"""Multi-stream audio test media (PBS-style masters: one mono PCM stream per channel).

    make_multi(outdir, name, streams, container="mov")

`streams` = one lavfi audio expression per MONO stream, written in order (e.g. 6 streams for
L R C LFE Ls Rs + 2 for a stereo mix). Helpers: `tone(freq, peak_dbfs)`, `SILENT` and
`gated(freq, peak_dbfs, [(a, b) file frames silent])`. Picture
and timing follow tests/media/spec_test.yaml (make_media.BASE: 23.976p, head 1+23, program 48,
tail 24, tmcd 00:59:59:00), so the program range is file frames 24..71. Audio is 48 kHz
pcm_s24le, exactly 2002 samples per frame.

Containers (verified on ffmpeg 6.1.1, `ffmpeg -h muxer=mxf`, `-h muxer=mov`, files probed):
  mov: ProRes 422 HQ like make_media; each audio stream mono pcm_s24le.
  mxf: OP1a with mpeg2video (the mxf muxer's default video codec) 1920x1080 yuv422p; mono
       pcm_s24le streams (ffprobe: channel_layout unknown, 1 channel - like Avid/PBS masters).

Level arithmetic: lavfi `sine` peaks at 1/8 (-18.06 dBFS), so `tone(f, P)` adds P + 18.06 dB.
BS.1770 (and ffmpeg ebur128, measured): a 1 kHz sine at P dBFS peak on ONE channel of weight 1
reads P - 3.01 LUFS; surround channels weigh 1.41 (+1.49 dB); LFE weighs 0.
"""
from __future__ import annotations

import subprocess
from fractions import Fraction
from pathlib import Path

from make_media import BASE

SILENT = "anullsrc=r=48000:cl=mono"


def tone(freq: float, peak_dbfs: float) -> str:
    return f"sine=f={freq}:r=48000,volume={peak_dbfs + 18.0618:.4f}dB"


def gated(freq: float, peak_dbfs: float, silent: list[tuple[int, int]]) -> str:
    """`tone(freq, peak_dbfs)` with digital silence on the FILE frames a..b (inclusive) of each
    range: chunks of exactly one frame (2002 samples at 23.976), `n` = file frame."""
    off = "+".join(f"between(n,{a},{b})" for a, b in silent)
    return f"{tone(freq, peak_dbfs)},asetnsamples=n=2002,volume=0:enable='{off}'"


def make_multi(outdir, name: str, streams: list[str], container: str = "mov",
               filename: str | None = None) -> Path:
    o = BASE
    d = Path(outdir) / name
    d.mkdir(parents=True, exist_ok=True)
    out = d / (filename or f"multi_{name}.{container}")
    fps = Fraction(o["fps"])
    head = o["slate"] + o["head_black"]
    total = head + o["program"] + o["tail_black"]
    samples = int(total * o["sample_rate"] / fps)
    vchain = [f"trim=end_frame={total}",
              f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='lt(n,{head})+gte(n,{head + o['program']})'"]
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=s={o['width']}x{o['height']}:r={o['fps']}"]
    fc = [f"[0:v]{','.join(vchain)}[v]"]
    maps = ["-map", "[v]"]
    for i, src in enumerate(streams):
        cmd += ["-f", "lavfi", "-i", src]
        fc.append(f"[{i + 1}:a]aformat=channel_layouts=mono,atrim=end_sample={samples}[a{i}]")
        maps += ["-map", f"[a{i}]"]
    if container == "mxf":
        vcodec = ["-c:v", "mpeg2video", "-pix_fmt", "yuv422p", "-b:v", "20M"]
    else:
        vcodec = [*o["vcodec"], "-pix_fmt", o["pix_fmt"], *o["color"]]
    cmd += ["-filter_complex", ";".join(fc), *maps, *vcodec, "-c:a", "pcm_s24le",
            "-timecode", o["timecode"], str(out)]
    subprocess.run(cmd, check=True)
    return out
