#!/usr/bin/env python3
"""Generate test media with ffmpeg lavfi (testsrc2 / sine), prores_ks, -timecode.

    python3 tests/media/make_media.py OUTDIR [CASE ...]     # default: all CASES

Cases: good, wrong_meta, wrong_tc, short, no_captions, corrupt (spec_test.yaml), one_track
(as good but one stereo track and no captions: templates/specs/spec_review_render.yaml).

Each case is written to OUTDIR/<case>/<filename> (the filename is part of what is checked,
so cases live in separate directories). Other test modules can call
`make(outdir, case_name, **overrides)` with any key of BASE, e.g. extra filters to plant
defects:  vf_extra="drawbox=x=0:y=0:w=iw:h=1:color=black:t=fill:enable='between(n,30,34)'".

Baseline (matches tests/media/spec_test.yaml): 1920x1080 ProRes 422 HQ yuv422p10le 23.976p,
bt709 tags, head = 1 slate (grey) + 23 black, program 48 frames testsrc2, tail 24 black,
two stereo pcm_s24le 48 kHz tracks (1 kHz / 1.5 kHz tone at -23 dBFS peak: sine source
peaks at 1/8 = -18.06 dBFS, mono->stereo `-ac 2` -3.01 dB, volume -1.94 dB; track 0 = -23 LUFS
by BS.1770 definition (stereo 1 kHz sine at P dBFS peak = P LUFS)), embedded 608 captions (SCC
stream-copied into MOV, codec tag c608), tmcd start 00:59:59:00 (= 01:00:00:00 - 24 frames).

Verified on ffmpeg 6.1.1: `ffmpeg -h encoder=prores_ks` (profile hq, yuv422p10le),
`-h filter=drawbox` (t=fill via thickness, timeline 'enable'), `-h filter=atrim`
(end_sample), `-h filter=sine`, `-h filter=testsrc2`, `-h muxer=mov` (write_tmcd);
SCC -> MOV stream copy probes as eia_608.
"""
from __future__ import annotations

import random
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

FILENAME = "SHOWNAME_101_HD_2398_ST_v03.mov"

BASE = dict(
    filename=FILENAME,
    width=1920, height=1080, fps="24000/1001",
    # prores_metadata: on ffmpeg 5.1 prores_ks leaves the ProRes frame header colour unknown
    # despite -color_* (MOV colr atom is right) and ffprobe reports the header; tests/CLAUDE.md
    vcodec=["-c:v", "prores_ks", "-profile:v", "hq", "-bsf:v",
            "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709"],
    pix_fmt="yuv422p10le",
    color=["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-color_range", "tv"],
    slate=1, head_black=23, program=48, tail_black=24,
    timecode="00:59:59:00",          # None = no tmcd track
    audio_tracks=2, channels=2, sample_rate=48000, acodec="pcm_s24le",
    captions=True,
    vf_extra=None,                   # appended to the video chain (plant defects here)
    af_extra=None,                   # appended to every audio chain
    extra_args=[],                   # extra output args (e.g. interlace flags)
    corrupt=False,                   # overwrite bytes in the middle of the file afterwards
)

CASES = {
    "good": {},
    "wrong_meta": dict(filename="show_101.mp4", width=1280, height=720, fps="25",
                       vcodec=["-c:v", "libx264", "-preset", "ultrafast"], pix_fmt="yuv420p",
                       color=[], timecode=None, audio_tracks=1, channels=1, sample_rate=44100,
                       acodec="aac", captions=False,
                       extra_args=["-flags", "+ildct+ilme", "-x264opts", "tff=1"]),
    "wrong_tc": dict(timecode="01:00:00:00"),
    "short": dict(program=40),
    "no_captions": dict(captions=False),
    "corrupt": dict(corrupt=True),
    # one stereo track, no captions: the good file in the shape of
    # templates/specs/spec_review_render.yaml (USERGUIDE quick start A)
    "one_track": dict(audio_tracks=1, captions=False),
}

SCC = "Scenarist_SCC V1.0\n\n{tc}\t9420 9420 94ae 94ae 9452 9452 97a2 97a2 c845 4c4c cf80 942c 942c 942f 942f\n\n"


def make(outdir, name: str, **overrides) -> Path:
    o = {**BASE, **overrides}
    d = Path(outdir) / name
    d.mkdir(parents=True, exist_ok=True)
    out = d / o["filename"]
    fps = Fraction(o["fps"])
    head = o["slate"] + o["head_black"]
    total = head + o["program"] + o["tail_black"]
    samples = int(total * o["sample_rate"] / fps)     # exact for 48k @ 23.976 (2002/frame)

    # trim, not -frames:v: on ffmpeg 5.1 -frames:v ends the output early and cuts queued audio
    vchain = [f"trim=end_frame={total}",
              f"drawbox=x=0:y=0:w=iw:h=ih:color=gray:t=fill:enable='lt(n,{o['slate']})'",
              f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(n,{o['slate']},{head - 1})'",
              f"drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='gte(n,{head + o['program']})'"]
    if o["vf_extra"]:
        vchain.append(o["vf_extra"])
    if o["color"] == BASE["color"]:
        # tag the frames as the output is tagged: ffmpeg >= 7 otherwise converts drawbox colours
        # (ffmpeg 9.0.2: RGB 8 grey drew as Y 21 instead of 23); no change on ffmpeg 6.1/5.1
        vchain.insert(0, "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=s={o['width']}x{o['height']}:r={o['fps']}"]
    fc = [f"[0:v]{','.join(vchain)}[v]"]
    for t in range(o["audio_tracks"]):
        cmd += ["-f", "lavfi", "-i", f"sine=f={1000 + 500 * t}:r={o['sample_rate']}"]
        achain = [f"atrim=end_sample={samples}", "volume=-1.94dB"]  # -> -23 dBFS peak, see docstring
        if o["af_extra"]:
            achain.append(o["af_extra"])
        fc.append(f"[{t + 1}:a]{','.join(achain)}[a{t}]")
    maps = ["-map", "[v]"] + sum((["-map", f"[a{t}]"] for t in range(o["audio_tracks"])), [])
    if o["captions"]:
        scc = d / "captions.scc"
        # SCC times are taken as file time: a caption at the TC hour would be dropped
        scc.write_text(SCC.format(tc="00:00:00:01"))
        cmd += ["-i", str(scc)]
        maps += ["-map", f"{o['audio_tracks'] + 1}:0", "-c:s", "copy"]
    cmd += ["-filter_complex", ";".join(fc), *maps,
            *o["vcodec"], "-pix_fmt", o["pix_fmt"], *o["color"],
            "-c:a", o["acodec"], "-ac", str(o["channels"]), *o["extra_args"]]
    if o["timecode"]:
        cmd += ["-timecode", o["timecode"]]
    if o["corrupt"]:
        cmd += ["-movflags", "+faststart"]       # moov first, so damage hits frame data only
    cmd.append(str(out))
    subprocess.run(cmd, check=True)
    if o["corrupt"]:
        size = out.stat().st_size
        with out.open("r+b") as f:
            for off in (size // 3, size // 2):
                f.seek(off)
                f.write(random.Random(off).randbytes(20000))   # deterministic damage
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    outdir, names = argv[0], argv[1:] or list(CASES)
    for n in names:
        print(make(outdir, n, **CASES[n]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
