"""Slate OCR adapter, pairing, comparison rules, slate.present / slate.consistency
(canned tesseract TSV, no tesseract or media needed)."""
from fractions import Fraction
from pathlib import Path

import pytest

from cmr.checks.slate import ocr, reader, template
from cmr.checks.slate.compare import canon, compare
from cmr.engine import CheckContext, Timeline, load_checks, REGISTRY
from cmr.media import MediaInfo
from cmr.model import Severity
from slate_tsv import FIELDS, tsv, words

ROOT = Path(__file__).resolve().parents[2]
TPL_PATH = ROOT / "examples" / "spot_xr" / "slates" / "xr_standard.yaml"


@pytest.fixture
def tpl():
    return template.load(str(TPL_PATH))


def parsed(ws):
    return ocr.parse_tsv(tsv(ws))


# --- TSV + pairing ------------------------------------------------------------------------
def test_parse_tsv_words_only_and_junk_filter():
    ws = parsed(words(extra=[("—_", 20, 5, 500, 10, 18), ("—", 80, 5, 600, 10, 18)]))
    texts = [w.text for w in ws]
    assert "—_" not in texts and "—" in texts          # junk dropped only below conf 50
    assert ws[0].text == "AGENCY" and ws[0].conf == 95 and ws[0].x == 308 - 6 * 11


def test_pair_all_fields(tpl):
    r = ocr.pair(parsed(words()), tpl)
    assert {k: v.text for k, v in r.items()} == dict(FIELDS)
    assert r["PRODUCT"].text == "" and r["PRODUCT"].words == []      # label found, value empty
    assert r["AD-ID"].line == 5 and r["MISC"].line == 10
    assert r["TITLE"].min_conf == 92


def test_pair_ignores_countdown_logo_and_stray_label(tpl):
    extra = [("4", 90, 1600, 860, 60, 100),               # countdown digit, right of the grid
             ("NW", 93, 205, 113, 120, 90),               # logo
             ("CLIENT", 90, 1200, 1000, 66, 18)]           # label word NOT on the label column
    r = ocr.pair(parsed(words(extra=extra)), tpl)
    assert {k: v.text for k, v in r.items()} == dict(FIELDS)
    ws = ocr.drop_region(parsed(words(extra=extra)), tpl.countdown, 1920, 1080)
    assert "4" not in [w.text for w in ws]


def test_pair_alias_and_missing_label(tpl):
    rows = [(l if l != "AD-ID" else "ADID", v) for l, v in FIELDS if l != "AUDIO"]
    r = ocr.pair(parsed(words(rows)), tpl)
    assert r["AD-ID"].text == "ABCD1234567H" and "AUDIO" not in r


def test_pair_line_alternative(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("template_version: 1\nfields:\n  AGENCY: {}\n  CLIENT: {}\n  THIRD: {line: 3}\n")
    t = template.load(str(p))
    rows = [("AGENCY", "A"), ("CLIENT", "B"), ("NOTES", "third value"), ("X", "Y")]
    r = ocr.pair(parsed(words(rows)), t)
    assert r["THIRD"].text == "third value" and r["THIRD"].line == 3


def test_pair_no_labels(tpl):
    assert ocr.pair(parsed([("hello", 90, 100, 100, 50, 18)]), tpl) == {}
    assert ocr.pair([], tpl) == {}


def test_template_validation(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("template_version: 1\nfields:\n  A: {typ: id}\n")
    with pytest.raises(Exception, match="typ"):
        template.load(str(p))


def test_tesseract_bin_precedence(monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, "/opt/x/tesseract")
    assert ocr.tesseract_bin({}) == "/opt/x/tesseract"
    assert ocr.tesseract_bin({"tesseract": "/p/t"}) == "/p/t"
    monkeypatch.delenv(ocr.ENV_BIN)
    assert ocr.tesseract_bin({}) == "tesseract"
    with pytest.raises(ocr.OCRUnavailable, match="CMR_TESSERACT"):
        ocr.resolve_bin("/nonexistent/tesseract")


# --- comparison rules -----------------------------------------------------------------------
@pytest.mark.parametrize("exp, read, ftype, status", [
    ("Sample Burgers", "sample  burgers", "text", "match"),
    ("Sample Burgers", "Sample Bugrers", "text", "mismatch"),
    ("SB X 2026_ProRes Pat_60", "SB X 2026 ProRes Pat 60", "title", "match"),   # _ -> space
    ("A-B_C", "a b c", "title", "match"),
    ("ABCD1234567H", "ABCD1234567H", "id", "match"),
    ("ABCD1O34567H", "ABCD1034567H", "id", "confusable"),
    ("ABCD1234567H", "ABCDl234S67H", "id", "confusable"),
    ("ABCD1234567H", "ABCD1234568H", "id", "mismatch"),
    ("ABZB", "A82B", "id", "confusable"),
    ("PLAN", "P1AN", "id", "mismatch"),                  # upper L is not in the 1/I/l class
    (":60", "60", "duration", "match"),
    (":60", "760", "duration", "mismatch"),
    ("20261026", "2026-10-26", "date", "match"),
    ("Sample Burgers", "Sample 8urgers", "text", "mismatch"),   # no classes for plain text
])
def test_compare(exp, read, ftype, status):
    assert compare(exp, read, ftype).status == status


def test_confusable_positions():
    r = compare("ABCD1O34567H", "ABCD1034567H", "id")
    assert r.positions == [6]
    assert canon(" a\tb ", "text") == "a b"


# --- checks with a fake reader ---------------------------------------------------------
def make_ctx(tmp_path, check_id, params, slate=(0, 149), frames=2010, w=1920):
    load_checks()
    f = tmp_path / "SB_spot.mov"
    if not f.exists():
        f.write_bytes(b"x")
    v = {"index": 0, "width": w, "height": 1080, "fps": Fraction(30000, 1001), "codec": "prores",
         "codec_profile": "422 HQ", "nb_frames": frames}
    media = MediaInfo(f, "file", {"format": {"tags": {}}}, "mov", video=[v],
                      audio=[{"index": 1, "channels": 2, "codec": "pcm_s24le"}])
    segs = [{"type": "slate", "start": slate[0], "end": slate[1]},
            {"type": "black", "start": slate[1] + 1, "end": slate[1] + 60}] if slate else []
    head = (slate[1] + 61) if slate else 0
    tl = Timeline(Fraction(30000, 1001), False, frames, 0, "file", head, 0, head, frames - 1,
                  segments=segs)
    return REGISTRY[check_id], CheckContext(check_id, media, {}, params, Severity.FAIL, tl)


@pytest.fixture
def fake_ocr(monkeypatch):
    """Per frame canned TSV: set fake_ocr[frame] = word list; default = the clean slate."""
    pages, calls = {}, []
    monkeypatch.setattr(ocr, "resolve_bin", lambda b: "tesseract")

    def ff(args, *a, **k):
        calls.append(args)
        Path(args[-1]).write_bytes(b"")
    monkeypatch.setattr(reader, "ffmpeg", ff)

    def run(img, *a, **k):
        f = int(Path(img).stem.split("_")[1])
        return parsed(pages.get(f, words()))
    monkeypatch.setattr(ocr, "run", run)
    reader._CACHE.clear()
    pages["calls"] = calls
    return pages


PARAMS = {"slate": {"template": str(TPL_PATH)}}


def run_check(chk, ctx):
    return chk.evaluate(ctx, chk.detect(ctx, {}))


def test_frames_second_and_second_to_last(fake_ocr, tmp_path):
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS)
    assert run_check(chk, ctx) == []
    seeks = [a[a.index("-ss") + 1] for a in fake_ocr["calls"]]
    assert seeks == [f"{0.5 * 1001 / 30000:.6f}", f"{147.5 * 1001 / 30000:.6f}"]   # frames 1, 148
    assert reader.slate_frames(ctx.timeline) == [1, 148]
    ctx.timeline.segments[0]["end"] = 0
    assert reader.slate_frames(ctx.timeline) == [0]


def test_present_skipped_without_slate_segment(fake_ocr, tmp_path):
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS, slate=None)
    from cmr.engine import Skip
    with pytest.raises(Skip, match="no slate segment"):
        run_check(chk, ctx)


def test_present_missing_label_and_blank_frame(fake_ocr, tmp_path):
    fake_ocr[1] = words([r for r in FIELDS if r[0] != "AD-ID"])
    fake_ocr[148] = []
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS)
    out = run_check(chk, ctx)
    msgs = sorted(f.message for f in out)
    assert any("no slate found on frame 148" in m for m in msgs)
    assert any("label AD-ID not found on frame 1" in m for m in msgs)
    assert all(f.severity == Severity.FAIL for f in out)
    ad = next(f for f in out if "AD-ID" in f.message)
    assert ad.evidence[0]["kind"] == "AD-ID" and (ad.frame_start, ad.frame_end) == (1, 1)


def test_present_optional_label_may_be_missing(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = words([r for r in FIELDS if r[0] != "MISC"])
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS)
    assert run_check(chk, ctx) == []


def test_tesseract_missing_is_error_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv(ocr.ENV_BIN, str(tmp_path / "no_tesseract"))
    reader._CACHE.clear()
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS)
    (f,) = run_check(chk, ctx)
    assert f.severity == Severity.ERROR and "tesseract not found" in f.message


def test_consistency_change_uncertain_and_confusable(fake_ocr, tmp_path):
    late = [(l, {"ARTIST": "Alex Exampe", "DURATION": "760", "AD-ID": "ABCD1234S67H"}.get(l, v))
            for l, v in FIELDS]
    fake_ocr[148] = words(late, conf={"760": 41})
    chk, ctx = make_ctx(tmp_path, "slate.consistency", PARAMS)
    out = {f.evidence[0]["kind"]: f for f in run_check(chk, ctx)}
    assert set(out) == {"ARTIST", "DURATION", "AD-ID"}
    assert out["ARTIST"].severity == Severity.FAIL and "changes during the slate" in out["ARTIST"].message
    assert out["ARTIST"].evidence[0]["text"] == "Alex Example | Alex Exampe"
    assert out["DURATION"].severity == Severity.WARN and "uncertain" in out["DURATION"].message
    assert out["AD-ID"].severity == Severity.INFO and "confusable" in out["AD-ID"].message
    assert (out["ARTIST"].frame_start, out["ARTIST"].frame_end) == (1, 148)


def test_consistency_one_frame_skipped(fake_ocr, tmp_path):
    from cmr.engine import Skip
    chk, ctx = make_ctx(tmp_path, "slate.consistency", PARAMS, slate=(0, 0))
    with pytest.raises(Skip):
        run_check(chk, ctx)


def test_ocr_cached_per_file(fake_ocr, tmp_path):
    chk, ctx = make_ctx(tmp_path, "slate.present", PARAMS)
    run_check(chk, ctx)
    chk2, ctx2 = make_ctx(tmp_path, "slate.consistency", PARAMS)
    ctx2.media = ctx.media
    run_check(chk2, ctx2)
    assert len(fake_ocr["calls"]) == 2          # two frames decoded once for both checks
