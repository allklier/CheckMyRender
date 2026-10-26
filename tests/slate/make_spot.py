#!/usr/bin/env python3
"""Slated spot test media for the proposed slate checks (D27, Extreme Reach short form).

    PY tests/slate/make_spot.py OUTDIR   (PY = a Python with numpy and Pillow) [--values FILE] [--short] [--defect NAME ...]
                                              [--layout aligned|label_colon]

Default: OUTDIR/clean/<TITLE>.mov and OUTDIR/errors/<name>.mov (all ERRORS_SET defects).
--defect NAME (repeatable): one file in OUTDIR/<NAME+NAME>/ with only those defects.
Every run writes OUTDIR/values.yaml (slate text per file) and OUTDIR/manifest.yaml (planted
errors: frames, timecode, checks that should catch them). OUTDIR must be outside the repo.
--values: slate field values (default tests/slate/values_fictional.yaml; real-client values
belong in a private file outside the repo). TITLE = filename stem.

File: 1920x1080 ProRes 422 HQ yuv422p10le, 30000/1001 progressive, bt709 tags + tv range,
NDF tmcd 00:59:53:00. Video: slate 150 frames (5 s, countdown 5..1) + black 60 (2 s) + program
1800 (:60) from frame 210 = 01:00:00:00, no tail (2010 frames). Audio: one stereo stream,
48 kHz pcm_s24le; silent slate and black except a 2-pop (1 kHz, 1 frame, -20 dBFS peak) at
frame 150 (2 s before program); program = band-limited noise + chord tones with level
variation, gain tuned with ffmpeg ebur128 to -24 LUFS integrated (true peak reported).
--short: program 90 frames (3 s; DURATION ":03", title token "03"); head, slate, bars, 2-pop,
timecode unchanged; program defects sit at the same fractions of the program.

--layout label_colon: the "Label: value" slate seen on real Extreme Reach deliveries: white
text on flat black, left-aligned labels with a colon (x 100) and values in a second column
(x 330), one field per line, no bars, grey steps, logo or countdown. Fields Ad-ID (BASE code),
Title, Length (`60s`), Audio, Agency, Advertiser, Date (MM/DD/YYYY); values from
tests/slate/values_label_colon.yaml (default for this layout). Filename = Ad-ID base + seconds
+ H (`SBX01760H.mov`). Head as on the real files: slate 145 + black 65 (the split differs from
the spec's 150 + 60, program still at frame 210), 2-pop at frame 150. Defects: those that do
not need bars or a technical TITLE, plus `length_wrong` (slate Length 15s on a longer spot, a
real case).

Pictures are built as 10-bit Y'CbCr planes (Pillow renders the text mask, numpy the rest) and
piped as rawvideo, so bars/steps are exact code values with no RGB conversion.

ffmpeg options verified on 6.1.1: `-h demuxer=rawvideo` (pixel_format, video_size,
framerate), `-h demuxer=f32le` (sample_rate, ch_layout), `-h encoder=prores_ks` (profile hq,
yuv422p10le), `-h bsf=prores_metadata`, `-h full` (-timecode, -color_*, -metadata),
`-h filter=ebur128` (peak=true), `-h filter=atrim` (start_sample).
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import yaml
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[2]
# Fonts: $CMR_TEST_FONT / $CMR_TEST_FONT_REGULAR, else the first that exists: Linux DejaVu
# (Debian/Ubuntu, Fedora, Rocky 9 DejaVuLGC), then macOS Arial (/System/Library/Fonts/Supplemental).
# A different font gives a different slate picture, so OCR results are only comparable across
# machines on byte-identical media (generate the slate media once and copy it; tests/CLAUDE.md).
FONTS_BOLD = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/dejavu-lgc-sans-fonts/DejaVuLGCSans-Bold.ttf",      # Rocky 9
              "/System/Library/Fonts/Supplemental/Arial Bold.ttf")
FONTS_REGULAR = ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf",
                 "/usr/share/fonts/dejavu-lgc-sans-fonts/DejaVuLGCSans.ttf",
                 "/System/Library/Fonts/Supplemental/Arial.ttf")


def find_font(env: str, candidates: tuple) -> str:
    for f in ([os.environ[env]] if os.environ.get(env) else []) + list(candidates):
        if Path(f).is_file():
            return f
    sys.exit(f"make_spot: no font found; set ${env} to a .ttf file (tried {', '.join(candidates)})")


FONT = find_font("CMR_TEST_FONT", FONTS_BOLD)
FONT_REGULAR = find_font("CMR_TEST_FONT_REGULAR", FONTS_REGULAR)
W, H = 1920, 1080
FPS_NUM, FPS_DEN, TC_FPS = 30000, 1001, 30          # NDF timecode counts 30 frames per second
SR = 48000
START_TC = "00:59:53:00"                             # 01:00:00:00 - 210 frames
SLATE, BLACK, PROGRAM, SHORT_PROGRAM = 150, 60, 1800, 90
STRIP = 32                                           # bar / step strip height (px)

# BT.709 limited-range 8-bit Y/Cb/Cr (written x4 as 10-bit)
BARS = [(235, 128, 128), (219, 16, 138), (188, 154, 16), (173, 42, 26),
        (78, 214, 230), (63, 102, 240), (32, 240, 118), (16, 128, 128)]
STEPS = [(round(235 - i * 219 / 7), 128, 128) for i in range(8)]   # 235 204 172 141 110 79 47 16
LABELS = ["AGENCY", "CLIENT", "PRODUCT", "TITLE", "AD-ID", "DURATION", "AUDIO", "DATE", "ARTIST", "MISC"]

# Planted defects: id -> (manifest ref, checks that should catch it). Locations are computed in make().
DEFECTS = {
    "adid_wrong":   ("(a)+(filename)", ["slate.content"]),
    "frame_short":  ("(b)", ["timing.frame_count", "slate.claims"]),
    "audio_mono":   ("(c)", ["slate.claims"]),
    "title_res":    ("(d)", ["slate.claims", "filename"]),
    "bars_black":   ("(e1)", ["slate.bars"]),
    "bars_hue":     ("(e2)", ["slate.bars"]),
    "date_old":     ("(f)", ["slate.content"]),
    "late_differ":  ("(g)", ["slate.consistency"]),
    "client_typo":  ("(h)", ["slate.content"]),
    # timing.layout/timecode_start pass: they place the program by the spec layout, not by content
    "slate_4s":     ("(i)", ["timing.frame_count", "roadmap: head segment content (slate length, "
                             "program start found in the picture)"]),
    "edge_line":    ("(j1)", ["video.blanking"]),
    "clipping":     ("(j2)", ["audio.clipping", "audio.loudness"]),
    "black_run":    ("(j3)", ["video.black_runs"]),
    "loud":         ("(j4)", ["audio.loudness"]),
    "slate_tone":   ("(k)", ["roadmap: slate audio silent except 2-pop"]),
    "length_wrong": ("(l)", ["slate.claims", "slate.content"]),      # label_colon layout only
}
ERRORS_SET = [d for d in DEFECTS if d not in ("slate_4s", "length_wrong")]   # slate_4s shifts every later position
# label_colon layout (real XR "Label: value" slates)
LC_LABELS = ["Ad-ID", "Title", "Length", "Audio", "Agency", "Advertiser", "Date"]
LC_SLATE, LC_POP = 145, 150                          # slate 145 + black 65: program still at 210
LC_ONLY = ("length_wrong",)
LC_UNSUPPORTED = ("title_res", "bars_black", "bars_hue", "slate_4s")
LC_ERRORS_SET = [d for d in DEFECTS if d not in LC_UNSUPPORTED]


def sample(f):                    # first audio sample of file frame f (1601.6 samples per frame)
    return f * SR * FPS_DEN // FPS_NUM


def tc(f):
    h, m, s, fr = (int(x) for x in START_TC.split(":"))
    n = ((h * 60 + m) * 60 + s) * TC_FPS + fr + f
    return f"{n // (3600 * TC_FPS):02d}:{n // (60 * TC_FPS) % 60:02d}:{n // TC_FPS % 60:02d}:{n % TC_FPS:02d}"


def bump_digit(s):                # wrong slate: last digit of the Ad-ID + 1
    i = max(i for i, c in enumerate(s) if c.isdigit())
    return s[:i] + str((int(s[i]) + 1) % 10) + s[i + 1:]


def swap_typo(s):                 # swap two adjacent different letters at ~2/3 of the string
    j = len(s) * 2 // 3
    while not (s[j].isalpha() and s[j + 1].isalpha() and s[j] != s[j + 1]):
        j += 1
    return s[:j] + s[j + 1] + s[j] + s[j + 2:]


# ---------------------------------------------------------------- pictures (10-bit planes)
def planes(y, cb=512, cr=512):
    return (np.full((H, W), y, np.uint16), np.full((H, W // 2), cb, np.uint16),
            np.full((H, W // 2), cr, np.uint16))


def strip(p, rows, colours):
    seg = W // len(colours)
    for i, (y, cb, cr) in enumerate(colours):
        p[0][rows, i * seg:(i + 1) * seg] = y * 4
        p[1][rows, i * seg // 2:(i + 1) * seg // 2] = cb * 4
        p[2][rows, i * seg // 2:(i + 1) * seg // 2] = cr * 4


def slate_background():
    shapes = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shapes)
    d.polygon([(0, 80), (1150, 760), (1920, 260), (1920, 420), (1150, 900), (0, 230)], fill=255)
    d.polygon([(1600, 210), (1920, 210), (1700, 1080), (1400, 1080)], fill=255)
    y = 72 + np.asarray(shapes, np.float32) / 255 * 24                    # 8-bit ~18, shapes ~24
    ramp = np.clip((np.arange(H) - 880) / (H - STRIP - 880), 0, 1) ** 2 * 100   # glow above steps
    return y + ramp[:, None]


def slate_frame(bg, fields, count, bars, steps):
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    font = ImageFont.truetype(FONT, 27)
    d.text((205, 113), fields["_logo"], font=ImageFont.truetype(FONT, 130), fill=255, anchor="lt")
    for i, label in enumerate(LABELS):
        y = 345 + 61 * i
        d.text((308, y), label, font=font, fill=255, anchor="rm")
        if fields[label]:
            d.text((370, y), fields[label], font=font, fill=255, anchor="lm")
    cx, cy, r = 1631, 877, 92
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=255, width=5)
    d.text((cx, cy), str(count), font=ImageFont.truetype(FONT, 140), fill=255, anchor="mm")
    a = np.asarray(mask, np.float32) / 255
    p = planes(0)
    p[0][:] = np.rint(bg + a * (940 - bg))
    strip(p, slice(0, STRIP), bars)
    strip(p, slice(H - STRIP, H), steps)
    return p


def colon_slate_frame(fields):
    """label_colon slate: white text on flat black, labels with a colon at x 100, values at x 330."""
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    font = ImageFont.truetype(FONT_REGULAR, 30)
    for i, label in enumerate(LC_LABELS):
        y = 110 + 50 * i
        d.text((100, y), f"{label}:", font=font, fill=255, anchor="lm")
        if fields[label]:
            d.text((330, y), fields[label], font=font, fill=255, anchor="lm")
    a = np.asarray(mask, np.float32) / 255
    p = planes(64)
    p[0][:] = np.rint(64 + a * (940 - 64))
    return p


class Program:
    """Synthetic picture with motion: luma 360..720 (8-bit 90..180) pattern scrolling sideways,
    a bright moving box, slowly changing chroma. No dark areas, nothing near black at the edges."""
    def __init__(self):
        x, y = np.arange(W), np.arange(H)
        self.y = 540 + 180 * np.outer(np.cos(2 * np.pi * 2 * y / H), np.sin(2 * np.pi * 3 * x / W))
        self.cb = 512 + 160 * np.sin(2 * np.pi * np.arange(W // 2) / (W // 2))
        self.cy = 2 * np.pi * y / H

    def frame(self, i):
        yy = np.roll(self.y, 6 * i, axis=1)
        bx, by = 200 + (7 * i) % 1400, int(440 + 200 * np.sin(i / 40))
        yy[by:by + 200, bx:bx + 200] = 880
        cb = np.broadcast_to(np.roll(self.cb, 3 * i), (H, W // 2))
        cr = np.broadcast_to((512 + 160 * np.cos(self.cy + i / 50))[:, None], (H, W // 2))
        return tuple(np.rint(a).astype(np.uint16) for a in (yy, cb, cr))


# ---------------------------------------------------------------- audio (float, -1..1)
def program_audio(n, seed=7):
    rng = np.random.default_rng(seed)
    f = np.fft.rfftfreq(n, 1 / SR)
    shape = np.where((f > 80) & (f < 10000), 1 / np.sqrt(np.maximum(f, 1)), 0)   # pink-ish band
    noise = [np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * shape, n) for _ in range(3)]
    noise = [x / x.std() for x in noise]
    t = np.arange(n) / SR
    chords = [(220, 277.18, 329.63), (196, 246.94, 293.66), (174.61, 220, 261.63), (246.94, 311.13, 369.99)]
    tones = np.zeros((2, n))
    for k in range(int(t[-1] // 2) + 1):                     # new chord every 2 s
        sl = slice(k * 2 * SR, min(n, (k + 1) * 2 * SR))
        for j, fr in enumerate(chords[k % len(chords)]):
            tones[0, sl] += 0.3 * np.sin(2 * np.pi * fr * t[sl])
            tones[1, sl] += 0.3 * np.sin(2 * np.pi * fr * 1.002 * t[sl] + j)
    env = 10 ** ((4 * np.sin(2 * np.pi * t / 7.3) + 2 * np.sin(2 * np.pi * t / 2.9)) / 20)
    return np.stack([env * (noise[0] + 0.5 * noise[1] + tones[0]),
                     env * (noise[0] + 0.5 * noise[2] + tones[1])])


def ebur128(path, start_sample=0):
    """Integrated loudness (LUFS) and true peak (dBTP) of a stereo f32le file, from `start_sample`."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-f", "f32le", "-sample_rate", str(SR),
                        "-ch_layout", "stereo", "-i", str(path), "-af",
                        f"atrim=start_sample={start_sample},ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, check=True, stdin=subprocess.DEVNULL)
    summary = r.stderr[r.stderr.rindex("Summary:"):]
    return (float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary).group(1)),
            float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary).group(1)))


# ---------------------------------------------------------------- one file
def make(outdir, values, defects=(), short=False, subdir=None, now=None, layout="aligned"):
    """Render one spot into outdir/subdir; return its values/manifest entries."""
    defects = list(defects)
    colon = layout == "label_colon"
    for d in defects:
        if d not in DEFECTS:
            raise SystemExit(f"unknown defect {d!r}; known: {', '.join(DEFECTS)}")
        if d in (LC_UNSUPPORTED if colon else LC_ONLY):
            raise SystemExit(f"defect {d!r} is not available with --layout {layout}")
    now = now or dt.datetime.now(dt.timezone.utc)
    prog_nominal = SHORT_PROGRAM if short else PROGRAM
    slate = 120 if "slate_4s" in defects else SLATE
    head = slate + BLACK
    if colon:
        slate = LC_SLATE                              # head (210) unchanged: black 65
    prog = prog_nominal - ("frame_short" in defects)
    total = head + prog
    secs = f"{prog_nominal // TC_FPS:02d}"

    # slate text: what the file is (filename) vs what the slate says
    res = "1280x720" if "title_res" in defects else f"{W}x{H}"
    date = (now - dt.timedelta(days=400)) if "date_old" in defects else now
    if colon:
        file_ad = values["ad_base"]
        name = f"{file_ad}{secs}H.mov"
        slate_ad = bump_digit(file_ad) if "adid_wrong" in defects else file_ad
        k_date, k_late, k_client = "Date", "Agency", "Advertiser"
        early = {"Ad-ID": slate_ad, "Title": values["title"],
                 "Length": ("15s" if secs != "15" else "30s") if "length_wrong" in defects else f"{int(secs)}s",
                 "Audio": "Mono" if "audio_mono" in defects else values["audio"],
                 "Agency": values["agency"],
                 "Advertiser": swap_typo(values["advertiser"]) if "client_typo" in defects else values["advertiser"],
                 "Date": date.strftime("%m/%d/%Y")}
    else:
        file_ad = values["ad_id"]
        name = values["title"].format(seconds=secs, ad_id=file_ad, resolution=res) + ".mov"
        slate_ad = bump_digit(file_ad) if "adid_wrong" in defects else file_ad
        k_date, k_late, k_client = "DATE", "ARTIST", "CLIENT"
        early = {"AGENCY": values["agency"],
                 "CLIENT": swap_typo(values["client"]) if "client_typo" in defects else values["client"],
                 "PRODUCT": values["product"],
                 "TITLE": values["title"].format(seconds=secs, ad_id=slate_ad, resolution=res),
                 "AD-ID": slate_ad, "DURATION": f":{secs}",
                 "AUDIO": "Mono" if "audio_mono" in defects else values["audio"],
                 "DATE": date.strftime("%Y%m%d"), "ARTIST": values["artist"], "MISC": values["misc"]}
    late = dict(early)
    if "late_differ" in defects:
        late[k_late] = early[k_late][:-2] + early[k_late][-1]
    bars = list(BARS)
    if "bars_black" in defects:
        bars[7] = (32, 128, 128)
    if "bars_hue" in defects:
        bars[5] = (63, 102, 228)

    # program-relative defect positions (fractions of the nominal program, same in --short)
    edge0, black0, clip0 = prog_nominal // 4, prog_nominal // 2 - 10, prog_nominal * 7 // 10
    edge = range(head + edge0, head + edge0 + 5) if "edge_line" in defects else range(0)
    blk = range(head + black0, head + black0 + 20) if "black_run" in defects else range(0)

    d = Path(outdir) / (subdir or "+".join(defects) or "clean")
    d.mkdir(parents=True, exist_ok=True)
    out = d / name

    # audio
    raw = d / ".audio.f32"
    a = np.zeros((2, sample(total)))
    pop = sample(LC_POP if colon else slate)
    a[:, pop:pop + 1602] = 0.1 * np.sin(2 * np.pi * 1000 * np.arange(1602) / SR)      # -20 dBFS peak
    if "slate_tone" in defects:
        s0, s1 = sample(30), sample(90)
        a[:, s0:s1] = 0.1 * np.sin(2 * np.pi * 440 * np.arange(s1 - s0) / SR)
    p = program_audio(sample(total) - sample(head))
    for _ in range(3):                                   # summary I has 0.1 LU resolution
        p.T.astype("<f4").tofile(raw)
        tuned = ebur128(raw)
        if tuned[0] == -24.0:
            break
        p *= 10 ** ((-24 - tuned[0]) / 20)
    if "loud" in defects:
        p *= 10 ** (4 / 20)
    if "clipping" in defects:
        c0, c1 = sample(head + clip0) - sample(head), sample(head + clip0 + 5) - sample(head)
        p[:, c0:c1] = np.clip(p[:, c0:c1] * (1.25 / np.abs(p[:, c0:c1]).max()), -1, 1)   # peaks +2 dB over FS
    a[:, sample(head):] = p
    a.T.astype("<f4").tofile(raw)
    measured = ebur128(raw, sample(head))

    # video
    created = now.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y",
           "-f", "rawvideo", "-pixel_format", "yuv422p10le", "-video_size", f"{W}x{H}",
           "-framerate", f"{FPS_NUM}/{FPS_DEN}", "-i", "pipe:0",
           "-f", "f32le", "-sample_rate", str(SR), "-ch_layout", "stereo", "-i", str(raw),
           "-map", "0:v", "-map", "1:a",
           # tag the raw input as what it is (BT.709 limited): ffmpeg >= 7 converts untagged input
           # to the output's -colorspace (ffmpeg 9.0.2 shifted the bars by up to 10 codes)
           "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
           "-c:v", "prores_ks", "-profile:v", "hq", "-pix_fmt", "yuv422p10le", "-bsf:v",
           "prores_metadata=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-color_range", "tv", "-c:a", "pcm_s24le", "-timecode", START_TC,
           "-metadata", f"creation_time={created}", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    bg, pic = slate_background(), Program()
    cache = {}
    black = planes(64)
    for f in range(total):
        if f < slate:
            key = ((slate - f - 1) // TC_FPS + 1, f >= slate - TC_FPS)
            if key not in cache and colon:
                cache[key] = colon_slate_frame(late if key[1] else early)
            elif key not in cache:
                cache[key] = slate_frame(bg, {**(late if key[1] else early), "_logo": values["logo"]},
                                         key[0], bars, STEPS)
            fr = cache[key]
        elif f < head or f in blk:
            fr = black
        else:
            fr = pic.frame(f - head)
            if f in edge:
                fr[0][:, 0] = 64
        for plane in fr:
            enc.stdin.write(plane.astype("<u2").tobytes())
    enc.stdin.close()
    if enc.wait():
        raise SystemExit(f"ffmpeg failed ({enc.returncode})")
    raw.unlink()

    def loc(f0, f1, **kw):
        return {"frames": [f0, f1], "timecode": [tc(f0), tc(f1)], **kw}

    where = {
        "adid_wrong": loc(0, slate - 1, desc=f"wrong slate: AD-ID {slate_ad} vs {file_ad} in the filename"
                          if colon else f"wrong slate: AD-ID {slate_ad} (and the AD-ID token of TITLE) "
                          f"vs {file_ad} in the filename/values; TITLE differs from the filename "
                          f"stem in this one token"),
        "frame_short": loc(total, total, desc=f"program {prog} frames, one short of :{secs} = {prog_nominal}; "
                           f"missing frame would be file frame {total} ({tc(total)}); slate says :{secs}"),
        "audio_mono": loc(0, slate - 1, desc="slate AUDIO says Mono; file has one stereo stream"),
        "title_res": loc(0, slate - 1, desc=f"slate TITLE and filename claim 1280x720; file is {W}x{H}"),
        "bars_black": loc(0, slate - 1, desc="colour bars black segment (8th) at Y=32 instead of 16 "
                          f"(x {7 * W // 8}-{W - 1}, rows 0-{STRIP - 1})"),
        "bars_hue": loc(0, slate - 1, desc="red bar (6th) Cr=228 instead of 240 "
                        f"(x {5 * W // 8}-{6 * W // 8 - 1}, rows 0-{STRIP - 1})"),
        "date_old": loc(0, slate - 1, desc=f"slate DATE {early[k_date]}, 400 days before the render/file date "
                        f"{now:%Y%m%d}"),
        "late_differ": loc(slate - TC_FPS, slate - 1, desc=f"{k_late} reads {late[k_late]!r} on the last "
                           f"countdown second (incl. 2nd-to-last frame {slate - 2}) vs {early[k_late]!r} "
                           f"on frame 1"),
        "client_typo": loc(0, slate - 1, desc=f"{k_client} {early[k_client]!r} on all slate frames; values say "
                           f"{values['advertiser' if colon else 'client']!r}"),
        "slate_4s": loc(0, slate - 1, desc=f"slate 4 s (120 frames) + 2 s black: program content starts at file "
                        f"frame {head} = {tc(head)} instead of 01:00:00:00 (timecode start unchanged); the file is "
                        f"30 frames short, so a check that trusts the spec layout sees a short program"),
        "edge_line": loc(edge.start, edge.stop - 1, desc="1px black line (Y=64 10-bit) on the left edge, x=0, "
                         "5 frames"),
        "clipping": loc(head + clip0, head + clip0 + 4, desc="audio raised so its peaks reach +2 dB over full "
                        "scale and hard clipped at 0 dBFS, both channels, 5 frames"),
        "black_run": loc(blk.start, blk.stop - 1, desc="20 frames of black (Y=64 10-bit) mid-program; "
                         "audio continues"),
        "loud": loc(head, total - 1, desc=f"program audio +4 dB: {measured[0]:.1f} LUFS integrated "
                    f"(clean target -24)"),
        "slate_tone": loc(30, 89, desc="440 Hz tone at -20 dBFS in the slate audio (slate should be silent "
                          "except the 2-pop)"),
        "length_wrong": loc(0, slate - 1, desc=f"slate Length {early.get('Length')!r} on a :{secs} spot "
                            f"(filename and program say {int(secs)} s)"),
    }
    rel = str(out.relative_to(outdir))
    manifest = {"path": rel, "errors": [
        {"id": k, "ref": DEFECTS[k][0], "description": where[k].pop("desc"), **where[k],
         "checks": DEFECTS[k][1],
         "detectable_now": any(not c.startswith(("slate.", "roadmap")) for c in DEFECTS[k][1])}
        for k in defects]}
    info = {"path": rel, "defects": defects, "creation_time": created,
            "frames": {"total": total, "slate": [0, slate - 1], "black": [slate, head - 1],
                       "program": [head, total - 1]},
            "timecode": {"start": START_TC, "program_start": tc(head), "last": tc(total - 1)},
            "layout": layout,
            "two_pop": loc(LC_POP if colon else slate, LC_POP if colon else slate, dbfs_peak=-20, hz=1000),
            "audio_program": {"tuned_clean": {"lufs": tuned[0], "true_peak_dbtp": tuned[1]},
                              "as_written": {"lufs": measured[0], "true_peak_dbtp": measured[1]}},
            "slate_text": {"early": {"frames": [0, slate - TC_FPS - 1], **early},
                           "late": {"frames": [slate - TC_FPS, slate - 1], **late}}}
    info["two_pop"].pop("desc", None)
    return info, manifest


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outdir")
    ap.add_argument("--values", help="slate values (default values_fictional.yaml, or "
                    "values_label_colon.yaml with --layout label_colon)")
    ap.add_argument("--layout", choices=["aligned", "label_colon"], default="aligned")
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--defect", action="append", default=[], choices=list(DEFECTS))
    a = ap.parse_args(argv)
    outdir = Path(a.outdir).resolve()
    if outdir == REPO or REPO in outdir.parents:
        raise SystemExit(f"refusing to write media into the repo ({REPO})")
    a.values = a.values or str(Path(__file__).with_name(
        "values_label_colon.yaml" if a.layout == "label_colon" else "values_fictional.yaml"))
    values = yaml.safe_load(Path(a.values).read_text())
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    errors = LC_ERRORS_SET if a.layout == "label_colon" else ERRORS_SET
    runs = [(a.defect, None)] if a.defect else [([], "clean"), (errors, "errors")]
    infos, manifests = [], []
    for defects, sub in runs:
        i, m = make(outdir, values, defects, a.short, sub, now, a.layout)
        infos.append(i)
        manifests.append(m)
        print(outdir / i["path"])
    common = {"render_date": f"{now:%Y%m%d}", "short": a.short, "layout": a.layout, "fps": f"{FPS_NUM}/{FPS_DEN}",
              "timecode_mode": "non-drop"}
    (outdir / "values.yaml").write_text(yaml.safe_dump(
        {**common, "values_file": str(Path(a.values).resolve()), "values": values, "files": infos},
        sort_keys=False, allow_unicode=True, width=120, default_flow_style=None))
    (outdir / "manifest.yaml").write_text(yaml.safe_dump(
        {**common, "files": manifests}, sort_keys=False, allow_unicode=True, width=120, default_flow_style=None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
