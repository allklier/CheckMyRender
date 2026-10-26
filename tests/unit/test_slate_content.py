"""slate.content, slate.claims and slate waivers (canned TSV, fake reader; no media)."""
import datetime

import pytest

from cmr import waivers
from cmr.config import ConfigError
from cmr.model import Finding, Severity
from slate_tsv import FIELDS, words
from test_slate_ocr import PARAMS, fake_ocr, make_ctx, run_check  # noqa: F401 (fixture)

STEM = "SB X League 2026_ProRes Generic Submaster_QB Pat_60_ABCD1234567H_1920x1080_Slated"
REGEX = (r"(?P<title>.+)_(?P<length>\d+)_(?P<ad_id>[A-Z0-9]{11}[HD]?)_(?P<resolution>\d+x\d+)"
         r"_(?P<variant>Slated|Unslated)\.mov")
CSV = ("Ad-ID,Title,Length,Version,Air Date\n"
       "ABCD1234567H,SB X League 2026_ProRes Generic Submaster_QB Pat,60,v01,20261026\n"
       "ABCD7654321H,Other Spot,30,v01,20261001\n")
JOB = {"agency": "Northwind Advertising", "client": "Sample Burgers", "product": "<empty>",
       "artist": "Alex Example", "audio": "Stereo"}


def ctx_for(tmp_path, check_id, name=STEM, extra=None, expect=None, **kw):
    (tmp_path / "d.csv").write_text(CSV)
    params = {"slate": {**PARAMS["slate"], **({"expect": expect} if expect else {})}, "job": JOB,
              "deliverables": {"csv": str(tmp_path / "d.csv"), "key": "ad_id",
                               "columns": {"ad_id": "Ad-ID", "title": "Title", "length": "Length"},
                               "filename_regex": REGEX},
              "date": {"window_days": 30}, **(extra or {})}
    chk, ctx = make_ctx(tmp_path, check_id, params, **kw)
    real = tmp_path / f"{name}.mov"
    real.write_bytes(b"x")
    ctx.media.path = real
    ctx.media.raw["format"]["tags"]["creation_time"] = "2026-10-26T14:13:32.000000Z"
    return chk, ctx


def kinds(out):
    return {f.evidence[0]["kind"]: f for f in out}


def page(**over):
    return words([(l, over.get(l, v)) for l, v in FIELDS], conf=over.get("_conf"))


def test_clean_slate_passes(fake_ocr, tmp_path):
    chk, ctx = ctx_for(tmp_path, "slate.content")
    assert run_check(chk, ctx) == []


def test_errors_slate(fake_ocr, tmp_path):
    bad = page(CLIENT="Sample Bugrers", **{"AD-ID": "ABCD1234568H"}, DATE="20250921",
               TITLE=STEM.replace("4567H", "4568H"))
    fake_ocr[1] = fake_ocr[148] = bad
    chk, ctx = ctx_for(tmp_path, "slate.content")
    out = kinds(run_check(chk, ctx))
    assert set(out) == {"CLIENT", "AD-ID", "DATE", "TITLE"}
    assert "wrong slate or renamed file" in out["TITLE"].message
    assert out["AD-ID"].message.startswith("slate does not match the file")
    assert out["AD-ID"].severity == Severity.FAIL and out["CLIENT"].severity == Severity.FAIL
    assert out["DATE"].severity == Severity.WARN and "400 days before" in out["DATE"].message
    assert out["CLIENT"].evidence[0]["text"] == "Sample Bugrers"


def test_empty_marker_and_blank_semantics(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(PRODUCT="Cones", ARTIST="Somebody Else", MISC="x")
    chk, ctx = ctx_for(tmp_path, "slate.content")
    ctx.params["job"] = {**JOB, "artist": ""}         # blank = not specified = not checked
    out = kinds(run_check(chk, ctx))
    assert set(out) == {"PRODUCT"} and "must be empty" in out["PRODUCT"].message


def test_value_expected_but_slate_empty(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(AGENCY="")
    chk, ctx = ctx_for(tmp_path, "slate.content")
    assert "is empty" in kinds(run_check(chk, ctx))["AGENCY"].message


def test_job_expect_override_and_null(fake_ocr, tmp_path):
    chk, ctx = ctx_for(tmp_path, "slate.content", expect={"AGENCY": None, "MISC": "edit-bay-04"})
    fake_ocr[1] = fake_ocr[148] = page(AGENCY="Anything")
    assert set(kinds(run_check(chk, ctx))) == {"MISC"}
    chk, ctx = ctx_for(tmp_path, "slate.content", expect={"NOPE": "x"})
    with pytest.raises(ConfigError, match="NOPE"):
        run_check(chk, ctx)


def test_confusable_is_info_and_uncertain_is_warn(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(**{"AD-ID": "ABCDl234S67H"}, CLIENT="Sample Burgrs",
                                       _conf={"Burgrs": 40})
    chk, ctx = ctx_for(tmp_path, "slate.content")
    out = kinds(run_check(chk, ctx))
    assert out["AD-ID"].severity == Severity.INFO and "positions [5, 9]" in out["AD-ID"].message
    assert out["CLIENT"].severity == Severity.WARN and "uncertain" in out["CLIENT"].message


def test_better_frame_wins(fake_ocr, tmp_path):
    fake_ocr[1] = page(CLIENT="Sample Burgrs", _conf={"Burgrs": 30})
    chk, ctx = ctx_for(tmp_path, "slate.content")
    assert run_check(chk, ctx) == []       # frame 148 read it right with higher confidence


def test_title_separator_class(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(TITLE=STEM.replace("_", " "))
    chk, ctx = ctx_for(tmp_path, "slate.content")
    assert run_check(chk, ctx) == []


def test_row_problems_and_filename_disagreement(fake_ocr, tmp_path):
    chk, ctx = ctx_for(tmp_path, "slate.content", name=STEM.replace("4567H", "9999H"))
    out = run_check(chk, ctx)
    assert any("no deliverables row with ad_id" in f.message for f in out)
    chk, ctx = ctx_for(tmp_path, "slate.content", extra={})
    ctx.params["deliverables"]["row"] = "#2"          # --row picks another spot
    out = kinds(run_check(chk, ctx))
    assert "ad_id differs" in out["row"].message and "AD-ID" in out and "DURATION" in out


def test_date_policies(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(DATE="2026/10/26")
    chk, ctx = ctx_for(tmp_path, "slate.content")
    assert "not a YYYYMMDD date" in kinds(run_check(chk, ctx))["DATE"].message
    chk, ctx = ctx_for(tmp_path, "slate.content", extra={"date": {"mode": "ignore"}})
    assert run_check(chk, ctx) == []
    fake_ocr[1] = fake_ocr[148] = page(DATE="20261001")
    chk, ctx = ctx_for(tmp_path, "slate.content", extra={"date": {"mode": "equals_row"}})
    ctx.params["deliverables"]["columns"]["date"] = "Air Date"
    assert "row date '20261026'" in kinds(run_check(chk, ctx))["DATE"].message


# --- claims ------------------------------------------------------------------------------
def test_claims_clean(fake_ocr, tmp_path):
    chk, ctx = ctx_for(tmp_path, "slate.claims")
    assert run_check(chk, ctx) == []


def test_claims_errors(fake_ocr, tmp_path):
    name = STEM.replace("1920x1080", "1280x720")
    fake_ocr[1] = fake_ocr[148] = page(AUDIO="Mono", TITLE=name)
    chk, ctx = ctx_for(tmp_path, "slate.claims", name=name, frames=2009)
    out = run_check(chk, ctx)
    msgs = " / ".join(f.message for f in out)
    assert "DURATION ':60' = 1800 frames (60 s x 30 timecode frames/s) but the program is 1799" in msgs
    assert "slate AUDIO 'Mono' (1 ch) but the file's first audio stream is stereo (2 ch)" in msgs
    assert "slate TITLE and filename claims resolution 1280x720 but the file is 1920x1080" in msgs
    assert all(f.severity == Severity.FAIL for f in out) and len(out) == 3


def test_claims_uncertain_duration_and_tokens(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(DURATION="760", _conf={"760": 41})
    chk, ctx = ctx_for(tmp_path, "slate.claims")
    (f,) = run_check(chk, ctx)
    assert f.severity == Severity.WARN and "uncertain read" in f.message
    from cmr.checks.slate.claims import duration_seconds, tokens
    assert [duration_seconds(x) for x in (":60", "60", "1:00", ":30", "abc")] == [60, 60, 60, 30, None]
    assert set(tokens("X_ProRes HQ_1080p_29.97_25fps_H.264_2997")) == {
        ("codec", "prores"), ("codec_profile", "422 HQ"), ("height", "1080"), ("fps", "29.97"),
        ("fps", "25"), ("codec", "h264")}


# --- waivers by exact OCR text --------------------------------------------------------------
def test_slate_waiver_by_text(tmp_path):
    def finding(text):
        return Finding("slate.content", Severity.FAIL, "x", stream=0, frame_start=1, frame_end=148,
                       evidence=[{"kind": "CLIENT", "text": text, "frames": [1, 148]}])
    f = finding("Sample Bugrers")
    fd = {**f.to_dict(), "timecode_start": "a", "timecode_end": "b"}
    assert waivers.waivable(fd)
    w = waivers.make_waiver("W0001", fd, waivers.text_fingerprint(fd["evidence"]), "client ok",
                            datetime.date(2026, 10, 26))
    assert w["kind"] == "CLIENT" and w["fingerprint"]["values"] == ["CLIENT", "Sample Bugrers"]
    p = tmp_path / "w.yaml"
    waivers.save(p, [w])
    wl = waivers.load(p)
    s, today = waivers.settings({}), datetime.date(2026, 10, 27)
    never = lambda w: pytest.fail("slate waivers must not decode media")   # noqa: E731
    same, changed, other = finding("Sample Bugrers"), finding("Sample Burgerz"), finding("Sample Bugrers")
    other.evidence[0]["kind"] = "AGENCY"
    state = {}
    waivers.apply([same, changed, other], wl, never, s, today, state)
    assert same.severity == Severity.PASS and same.waiver_ref == "W0001"
    assert changed.severity == Severity.FAIL and changed.waiver["status"] == "content_changed"
    assert other.severity == Severity.FAIL and other.waiver is None       # other field
    cat = waivers.make_category("W0002", "slate.content", "fail", "agency typo ok", today,
                                kind="AGENCY")
    waivers.apply([other], [cat], never, s, today, {})
    assert other.severity == Severity.PASS
