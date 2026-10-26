"""Generate test media for the blanking CONTEXT analyser (stage 2, context.py).

Usage: python3 tests/blanking/gen_context.py OUTDIR

960x540 25 fps H.264 (crf 16, yuv420p), 96 frames each, plus OUTDIR/manifest.json:
per clip the expected context class of the stage-1 hits on given edges/frames.
Filters (ffmpeg 6.1.1, `ffmpeg -h filter=X`): zoompan (z, x, y, d, s, fps), xfade
(transition, duration, offset), fade (t, s, n), loop (loop, size), geq (lum with N, X, Y),
pad, drawbox (enable='between(n,a,b)'; x takes t, not n), eq.
Ground truth:
- kenburns_zoom: pillarboxed 4:3 still (bars 120 px), zoom-in 1 -> 1.384: both bars shrink
  steadily and vanish (~frame 83): hits = animation.
- kenburns_pan: same still at zoom 1.15 panned left -> right: the left bar shrinks, the
  right one grows (asymmetric): hits = animation.
- fade_dark_border: picture with a 6 px Y~40 border on the left fades to black (frames
  60-83): the border goes below black before the picture -> transient band = transition.
- cut_field: hard cut at frame 48; frame 47 has a 6 px black band on the even rows only
  (a field-mixed cut frame): the band switches on abruptly (onset jump), so it is NOT
  excused as a transition: steady, stays FAIL (a 1-frame mask error at a cut looks the same).
- steady_moving: moving picture with a 2 px black column on the left for frames 40-44:
  steady (must stay FAIL).
- steady_near_fade: as fade_dark_border, plus a 2 px black column on the RIGHT edge for
  frames 50-54 (outside the fade): steady (must stay FAIL) while the fade hits are transition.
- dark_scene: dimmed moving picture with a black shape sliding out at the left, and a 3 px
  black column on the right for frames 30-32: steady (must stay FAIL). (The sliding shape
  gave no stage-1 hit: its frames are "dark" for the matte rule.)
"""
import json
import subprocess
import sys
from pathlib import Path

W, H, FPS, N = 960, 540, 25, 96
ENC = ["-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-g", "25", "-bf", "2"]
# a 4:3 still (testsrc2 frame 0) padded to 16:9 with 120 px black bars
STILL = (f"testsrc2=s=720x540:r={FPS},trim=end_frame=1,setpts=PTS-STARTPTS,"
         f"pad={W}:{H}:120:0:black")
MOVING = f"testsrc2=s={W}x{H}:r={FPS}"


def box(x, w, a, b):
    return f"drawbox=x={x}:y=0:w={w}:h=ih:c=black:t=fill:enable='between(n,{a},{b})'"


CLIPS = {
    "kenburns_zoom": dict(
        graph=f"{STILL},zoompan=z='1+0.004*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
              f":d={N}:s={W}x{H}:fps={FPS}",
        expect=[dict(edge="left", cls="animation"), dict(edge="right", cls="animation")]),
    "kenburns_pan": dict(
        graph=f"{STILL},zoompan=z=1.15:x='(iw-iw/zoom)*on/{N - 1}':y='(ih-ih/zoom)/2'"
              f":d={N}:s={W}x{H}:fps={FPS}",
        expect=[dict(edge="left", cls="animation"), dict(edge="right", cls="animation")]),
    "fade_dark_border": dict(
        graph=f"{MOVING},drawbox=x=0:y=0:w=6:h=ih:c=0x282828:t=fill,fade=t=out:s=60:n=24",
        expect=[dict(edge="left", cls="transition", frames=[60, 95])]),
    "cut_field": dict(
        graph=f"{MOVING},geq=lum='if(eq(N,47)*lt(X,6)*eq(mod(Y,2),0),16,lum(X,Y))'"
              f":cb='cb(X,Y)':cr='cr(X,Y)',"
              f"drawbox=x=0:y=0:w=iw:h=ih:c=0x406080:t=fill:enable='gte(n,48)',"
              f"drawbox=x=200:y=100:w=300:h=200:c=white:t=fill:enable='gte(n,48)'",
        expect=[dict(edge="left", cls="steady", frames=[47, 47], fail=True)]),
    "steady_moving": dict(
        graph=f"{MOVING},{box(0, 2, 40, 44)}",
        expect=[dict(edge="left", cls="steady", frames=[40, 44], fail=True)]),
    "steady_near_fade": dict(
        graph=f"{MOVING},drawbox=x=0:y=0:w=6:h=ih:c=0x282828:t=fill,{box('iw-2', 2, 50, 54)},"
              f"fade=t=out:s=60:n=24",
        expect=[dict(edge="right", cls="steady", frames=[50, 54], fail=True),
                dict(edge="left", cls="transition", frames=[60, 95])]),
    "dark_scene": dict(
        graph=f"{MOVING},eq=brightness=-0.2:contrast=0.5,"
              f"drawbox=x='40-50*t':y=100:w=100:h=300:c=black:t=fill,{box('iw-3', 3, 30, 32)}",
        expect=[dict(edge="right", cls="steady", frames=[30, 32], fail=True)]),
}


def main(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    man = {"width": W, "height": H, "fps": FPS, "frames": N, "clips": {}}
    for name, c in CLIPS.items():
        dst = outdir / f"{name}.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                        c["graph"], "-frames:v", str(N), *ENC, str(dst)], check=True)
        man["clips"][name] = {"file": dst.name, "expect": c["expect"]}
        print("wrote", dst)
    (outdir / "manifest.json").write_text(json.dumps(man, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
