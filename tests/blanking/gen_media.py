"""Generate blanking test media with planted defects (ffmpeg lavfi).

Usage: python3 tests/blanking/gen_media.py OUTDIR

Writes one clip per case plus OUTDIR/manifest.json (ground truth).
Frame numbers are 0-based, ranges inclusive. Defects are drawn with
drawbox + timeline `enable='between(n,a,b)'` (both verified via
`ffmpeg -h filter=drawbox`, ffmpeg 6.1.1).
"""
import json
import subprocess
import sys
from pathlib import Path

W, H, FPS, N = 1920, 1080, "24000/1001", 96
SRC = f"testsrc2=s={W}x{H}:r={FPS}"  # length set by -frames:v
PRORES = ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
H264 = ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]

# 1px strip geometry per edge for drawbox (depth d)
BOX = {
    "top": lambda d: f"x=0:y=0:w=iw:h={d}",
    "bottom": lambda d: f"x=0:y=ih-{d}:w=iw:h={d}",
    "left": lambda d: f"x=0:y=0:w={d}:h=ih",
    "right": lambda d: f"x=iw-{d}:y=0:w={d}:h=ih",
}
# 1, 3 and 10 frame defects, well separated
SPANS = [(10, 10), (40, 42), (70, 79)]


def box(edge, depth, a, b, color="black"):
    return f"drawbox={BOX[edge](depth)}:c={color}:t=fill:enable='between(n,{a},{b})'"


# partial 1px line: share of the row/column, placed differently per edge
PARTIAL = [(10, 12, 0.3), (30, 32, 0.5), (50, 52, 0.7), (70, 72, 1.0)]
PART_BOX = {
    "top": lambda c: f"x=0:y=0:w=iw*{c}:h=1",                      # from the left
    "bottom": lambda c: f"x=(iw-iw*{c})/2:y=ih-1:w=iw*{c}:h=1",    # centred
    "left": lambda c: f"x=0:y=0:w=1:h=ih*{c}",                     # from the top
    "right": lambda c: f"x=iw-1:y=ih-ih*{c}:w=1:h=ih*{c}",         # from the bottom
}


def part_box(edge, c, a, b):
    return f"drawbox={PART_BOX[edge](c)}:c=black:t=fill:enable='between(n,{a},{b})'"


def cases():
    out = []
    out.append(dict(name="clean", vf=[], defects=[]))
    # partial-line defects; below the default coverage (0.5) a miss is expected
    for edge in PART_BOX:
        out.append(dict(
            name=f"partial_{edge}",
            vf=[part_box(edge, c, a, b) for a, b, c in PARTIAL],
            defects=[dict(edge=edge, start=a, end=b, depth=1, coverage=c, expect=c >= 0.5)
                     for a, b, c in PARTIAL]))
    for edge in BOX:
        out.append(dict(
            name=f"edge_{edge}",
            vf=[box(edge, 1, a, b) for a, b in SPANS],
            defects=[dict(edge=edge, start=a, end=b, depth=1) for a, b in SPANS]))
    # same as edge_right but H.264 4:2:0 (odd column with chroma subsampling)
    out.append(dict(name="edge_right_h264", src="edge_right", enc=H264,
                    defects=[dict(edge="right", start=a, end=b, depth=1) for a, b in SPANS]))
    # depth: 2px, 6px, 20px (20px reaches the default inner line at 16px -> reported as
    # a transient "matte", still a hit)
    out.append(dict(name="depth_right",
                    vf=[box("right", 2, 10, 12), box("right", 6, 40, 42), box("right", 20, 70, 72)],
                    defects=[dict(edge="right", start=10, end=12, depth=2),
                             dict(edge="right", start=40, end=42, depth=6),
                             dict(edge="right", start=70, end=72, depth=20)]))
    # genuinely dark content on the left 200px (RGB 0x101010 ~ Y 30 tv-range)
    # and on the top 120px (RGB 0x080808 ~ Y 23), 1px defect on each for 3 frames,
    # plus a 60% partial on the dark left column
    out.append(dict(name="dark_content",
                    vf=["drawbox=x=0:y=0:w=200:h=ih:c=0x101010:t=fill",
                        "drawbox=x=0:y=0:w=iw:h=120:c=0x080808:t=fill",
                        box("left", 1, 40, 42), box("top", 1, 70, 72),
                        part_box("left", 0.6, 10, 12)],
                    defects=[dict(edge="left", start=40, end=42, depth=1),
                             dict(edge="top", start=70, end=72, depth=1),
                             dict(edge="left", start=10, end=12, depth=1, coverage=0.6)]))
    # fade in from black (frames 0-23), fade out to black (60-83), black 84-95
    out.append(dict(name="fade",
                    vf=["fade=t=in:s=0:n=24", "fade=t=out:s=60:n=24"], defects=[]))
    # temporal grain over the picture (FP control), then a clean-black 1px defect
    # on the bottom edge for 3 frames (repositioned plate after grain)
    out.append(dict(name="grain",
                    vf=["noise=alls=20:allf=t+u", box("bottom", 1, 40, 42)],
                    defects=[dict(edge="bottom", start=40, end=42, depth=1)]))
    # intentional mattes for the whole clip (expected: persistent matte, not a defect)
    out.append(dict(name="letterbox",
                    vf=["scale=1920:800", "pad=1920:1080:0:140:black"], defects=[],
                    mattes=["top", "bottom"]))
    out.append(dict(name="pillarbox",
                    vf=["scale=1440:1080", "pad=1920:1080:240:0:black"], defects=[],
                    mattes=["left", "right"]))
    # letterbox plus a transient 1px defect on the left edge (defect inside matted clip)
    out.append(dict(name="letterbox_defect",
                    vf=["scale=1920:800", "pad=1920:1080:0:140:black", box("left", 1, 40, 42)],
                    defects=[dict(edge="left", start=40, end=42, depth=1)],
                    mattes=["top", "bottom"]))
    return out


def main(outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    manifest = {"width": W, "height": H, "fps": FPS, "frames": N, "clips": {}}
    by_name = {c["name"]: c for c in cases()}
    for c in by_name.values():
        enc = c.get("enc", PRORES)
        ext = ".mp4" if enc is H264 else ".mov"
        dst = outdir / (c["name"] + ext)
        vf = by_name[c.get("src", c["name"])]["vf"]
        graph = ",".join([SRC, "format=yuv422p"] + vf)
        cmd = ["ffmpeg", "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i", graph,
               "-frames:v", str(N), *enc, str(dst)]
        subprocess.run(cmd, check=True)
        manifest["clips"][c["name"]] = {
            "file": dst.name, "defects": c["defects"], "mattes": c.get("mattes", [])}
        print("wrote", dst)
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
