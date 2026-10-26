"""Video waiver fingerprint v2 on generated media (D17): re-encodes stay within tolerance, dim
stripes are not black, the extracted plane is the decoder's own luma (no conversion)."""
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from cmr import waivers

FRAMES = waivers.sample_frames(2, 20, 8)
MB = "mandelbrot=s=640x360:r=24,trim=end_frame=24,setpts=PTS-STARTPTS"
ST = ",".join(f"drawbox=x=iw*{k}/9:y=0:w=iw/9:h=ih:color=0x080808:t=fill" for k in (1, 3, 5, 7))
CASES = {   # name: (input args, output args)
    "mb_prores10.mov": (["-f", "lavfi", "-i", MB], ["-c:v", "prores_ks", "-profile:v", "3"]),
    "mb_crf18.mov": (["-i", "{mb_prores10.mov}"], ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]),
    "mb_crf28.mov": (["-i", "{mb_prores10.mov}"], ["-c:v", "libx264", "-crf", "28", "-pix_fmt", "yuv420p"]),
    "mb_h264_10.mov": (["-i", "{mb_prores10.mov}"], ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p10le"]),
    "mb_rgb24.mov": (["-i", "{mb_prores10.mov}"], ["-c:v", "png", "-pix_fmt", "rgb24"]),
    "black10.mov": (["-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=1"],
                    ["-c:v", "prores_ks", "-profile:v", "3"]),
    "stripes10.mov": (["-f", "lavfi", "-i", "color=black:s=640x360:r=24:d=1"],
                      ["-vf", ST, "-c:v", "prores_ks", "-profile:v", "3"]),
    "stripes8.mov": (["-i", "{stripes10.mov}"], ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]),
}


@pytest.fixture(scope="module")
def media():
    out = Path(os.environ.get("CMR_TEST_MEDIA", Path(tempfile.gettempdir()) / "cmr_test_media")) / "fingerprint"
    out.mkdir(parents=True, exist_ok=True)
    for name, (inp, args) in CASES.items():
        if not (out / name).exists():
            inp = [str(out / a[1:-1]) if a.startswith("{") else a for a in inp]
            subprocess.run(["ffmpeg", "-v", "error", "-y", *inp, *args, str(out / name)], check=True)
    return {n: waivers.Fingerprinter(out / n, 24).fingerprint("video.x", 0, FRAMES) for n in CASES} | {"_dir": out}


def test_reencodes_within_tolerance(media):
    ref, tol = media["mb_prores10.mov"], waivers.DEFAULTS["video_max_bits"]
    assert ref["version"] == 2 and ref["plane"] == "y" and ref["ffmpeg"].startswith("ffmpeg version")
    for n in ("mb_crf18.mov", "mb_crf28.mov", "mb_h264_10.mov", "mb_rgb24.mov"):
        assert waivers.distance(ref, media[n]) <= tol, n
    assert media["mb_rgb24.mov"]["plane"] == "rgb"


def test_dim_stripes_are_not_black(media):
    tol = waivers.DEFAULTS["video_max_bits"]
    for n in ("stripes10.mov", "stripes8.mov"):              # 10-bit ProRes and 8-bit H.264
        assert waivers.distance(media[n], media["black10.mov"]) > tol, n
    assert waivers.distance(media["stripes10.mov"], media["stripes8.mov"]) == 0


def test_extracted_plane_is_native_luma(media):
    """extractplanes=y output == the Y part of a native rawvideo decode (no conversion)."""
    f = media["_dir"] / "mb_prores10.mov"                   # yuv422p10le: Y plane first, 2 bytes
    run = lambda *vf: subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-frames:v", "1", *vf,
                                      "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    y = run("-vf", "extractplanes=y")
    assert len(y) == 640 * 360 * 2 and run()[:len(y)] == y
