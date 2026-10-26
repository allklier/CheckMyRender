"""`LABEL: value` slates (template layout label_colon), composite deliverables keys
(Ad-ID base + length), date/length normalisation, slate/black split tolerance, templates
without bars. Canned TSV only (no tesseract, no media); FICTIONAL values in the two label sets
seen on real Extreme Reach deliveries."""
import datetime
from pathlib import Path

import pytest

from cmr import deliverables as dl
from cmr.checks.slate import ocr, reader, template
from cmr.checks.slate.compare import compare, parse_date, seconds
from cmr.config import ConfigError
from cmr.engine import Skip
from cmr.model import Severity
from slate_tsv import tsv
from test_slate_ocr import fake_ocr, make_ctx, run_check  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[2]
EX = ROOT / "examples" / "spot_xr"
TPL = EX / "slates" / "xr_label_colon.yaml"
REGEX = r"(?P<ad_base>[A-Z]{2,4}\d{3})(?P<length>\d{2})?(?P<hd>[HD])\.mov"
CSV = ("Ad-ID,Title,Length,Air Date\n"
       "SBX017,Kickoff: Night 1/2,:15,11/01/2026\n"
       "SBX017,Kickoff: Night 1/2,:30,11/01/2026\n"
       "SBX018,Kickoff: Overtime,30s,5/7/2026\n")
JOB = {"agency": "Northwind Advertising", "advertiser": "Sample Burgers"}
# label set 1 (Ad-ID / Length "15s" / Advertiser / MM/DD/YYYY) and set 2 (AdID / Brand /
# Length "30" / M/D/YYYY), fictional values
SET1 = [("Ad-ID:", "SBX017"), ("Title:", "Kickoff: Night 1/2"), ("Length:", "15s"),
        ("Audio:", "Stereo"), ("Agency:", "Northwind Advertising"),
        ("Advertiser:", "Sample Burgers"), ("Date:", "10/20/2026")]
SET2 = [("AdID:", "SBX018"), ("Brand:", "Sample Burgers"), ("Title:", "Kickoff: Overtime"),
        ("Length:", "30"), ("Audio:", "Stereo"), ("Agency:", "Northwind Advertising"),
        ("Date:", "5/7/2026")]
CW, H = 14, 30


def colon_words(rows, tight=False, conf=None, label_x=100, value_x=330, top=100, pitch=50):
    """Left-aligned `Label:` words; values in a tab column (value_x) or right after the label
    (tight: one space). -> [(text, conf, x, y, w, h)]"""
    conf, out = conf or {}, []
    for i, (label, value) in enumerate(rows):
        y, x = top + i * pitch, label_x
        for wd in label.split():
            out.append((wd, conf.get(wd, 95), x, y, len(wd) * CW, H))
            x += len(wd) * CW + 8
        x = x if tight else value_x
        for wd in value.split():
            out.append((wd, conf.get(wd, 92), x, y, len(wd) * CW, H))
            x += len(wd) * CW + 8
    return out


@pytest.fixture
def tpl():
    return template.load(str(TPL))


def read(ws, t):
    return {k: v.text for k, v in ocr.pair(ocr.parse_tsv(tsv(ws)), t).items()}


# --- pairing ---------------------------------------------------------------------------
def test_pair_label_set_1_tab_column(tpl):
    assert read(colon_words(SET1), tpl) == {
        "AD-ID": "SBX017", "TITLE": "Kickoff: Night 1/2", "LENGTH": "15s", "AUDIO": "Stereo",
        "AGENCY": "Northwind Advertising", "ADVERTISER": "Sample Burgers", "DATE": "10/20/2026"}


def test_pair_label_set_2_aliases_and_tight_layout(tpl):
    r = read(colon_words(SET2, tight=True), tpl)
    assert r["AD-ID"] == "SBX018" and r["ADVERTISER"] == "Sample Burgers"
    assert r["LENGTH"] == "30" and r["DATE"] == "5/7/2026" and r["TITLE"] == "Kickoff: Overtime"


def test_pair_glued_colon_empty_value_and_lines(tpl):
    rows = [("Ad-ID:SBX017", ""), ("Title:", ""), ("Agency:", "A: B/C")]
    r = ocr.pair(ocr.parse_tsv(tsv(colon_words(rows))), tpl)
    assert r["AD-ID"].text == "SBX017"                       # `Ad-ID:SBX017` read as one word
    assert r["TITLE"].text == "" and r["TITLE"].words == []  # label found, value empty
    assert r["AGENCY"].text == "A: B/C" and r["AGENCY"].line == 3


def test_pair_colon_not_read_and_unknown_label(tpl):
    rows = [("Ad-ID", "SBX017"), ("Notes:", "Date: not a label"), ("Audio:", "Stereo")]
    r = read(colon_words(rows), tpl)
    assert r == {"AD-ID": "SBX017", "AUDIO": "Stereo"}       # value segment never a label


def test_pair_two_label_columns_and_line_key(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("template_version: 1\nlayout: label_colon\nfields:\n  AGENCY: {}\n"
                 "  DATE: {}\n  THIRD: {line: 3}\n")
    t = template.load(str(p))
    ws = colon_words([("Agency:", "North"), ("x:", "y"), ("Misc:", "third value")])
    ws += [("Date:", 95, 900, 100, 70, H), ("1/2/2026", 92, 1000, 100, 112, H)]
    r = ocr.pair(ocr.parse_tsv(tsv(ws)), t)
    assert r["AGENCY"].text == "North" and r["DATE"].text == "1/2/2026"
    assert r["THIRD"].text == "third value" and r["THIRD"].line == 3


def test_aligned_template_still_aligned(tpl):
    t = template.load(str(EX / "slates" / "xr_standard.yaml"))
    assert t.layout == "aligned" and tpl.layout == "label_colon"
    assert tpl.date_formats == ["MM/DD/YYYY", "M/D/YYYY"] and not tpl.bars


def test_template_rejects_unknown_layout_and_date_format(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("template_version: 1\nlayout: colon\nfields: {A: {}}\n")
    with pytest.raises(ConfigError, match="layout"):
        template.load(str(p))
    p.write_text("template_version: 1\ndate_formats: [DD/MM]\nfields: {A: {}}\n")
    with pytest.raises(ConfigError, match="date_formats"):
        template.load(str(p))


# --- normalisation ---------------------------------------------------------------------
@pytest.mark.parametrize("exp,rd,ftype,status", [
    ("15", "15s", "duration", "match"), (":30", "30", "duration", "match"),
    ("15", "30s", "duration", "mismatch"), ("60", "1:00", "duration", "match"),
    ("05/07/2026", "5/7/2026", "date", "match"), ("5/7/2026", "5/17/2026", "date", "mismatch"),
])
def test_compare_length_and_date(exp, rd, ftype, status):
    assert compare(exp, rd, ftype).status == status


def test_seconds_and_dates():
    assert [seconds(x) for x in ("15s", "30", ":15", "15 sec", "1:00", "abc", "")] == [15, 30, 15, 15, 60, None, None]
    d = datetime.date(2026, 5, 7)
    assert parse_date("5/7/2026", "M/D/YYYY") == d == parse_date("05/07/2026", "M/D/YYYY")
    assert parse_date("5/7/2026", "MM/DD/YYYY") is None          # MM/DD needs two digits
    assert parse_date("05/07/2026", "MM/DD/YYYY") == d
    assert parse_date("20260507", "YYYYMMDD") == d and parse_date("13/01/2026", "MM/DD/YYYY") is None


# --- deliverables: composite key -------------------------------------------------------
@pytest.fixture
def csvfile(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text(CSV)
    return str(p)


def dcfg(csvfile, **kw):
    return {"csv": csvfile, "columns": {"ad_base": "Ad-ID", "title": "Title", "length": "Length"},
            "key": ["ad_base", "length"], "match": "key", "filename_regex": REGEX, **kw}


def test_composite_key_selects_base_plus_length(csvfile):
    s = dl.select(dcfg(csvfile), {}, "SBX01730H.mov")
    assert s.number == 2 and s.file["ad_base"] == "SBX017" and s.file["length"] == "30"
    assert dl.select(dcfg(csvfile), {}, "SBX01715H.mov").number == 1
    assert dl.key_parts(dcfg(csvfile)) == [("ad_base", "ad_id"), ("length", "duration")]


def test_composite_key_no_row_and_optional_part(csvfile):
    s = dl.select(dcfg(csvfile), {}, "SBX01731H.mov")          # 31: no :31 row
    assert s.row is None and "ad_base 'SBX017' + length '31'" in s.problems[0]
    assert dl.select(dcfg(csvfile), {}, "SBX018H.mov").number == 3   # no length part: base only
    with pytest.raises(ConfigError, match="2 deliverables rows"):
        dl.select(dcfg(csvfile), {}, "SBX017H.mov")              # base alone is ambiguous


def test_composite_key_config_errors(csvfile):
    with pytest.raises(ConfigError, match="composite"):
        dl.select(dcfg(csvfile, key_type="ad_id"), {}, "SBX01715H.mov")
    with pytest.raises(ConfigError, match="no named group"):
        dl.select(dcfg(csvfile, key=["ad_base", "spot"]), {}, "SBX01715H.mov")
    with pytest.raises(ConfigError, match="composite"):
        dl.select(dcfg(csvfile, row="SBX017"), {}, "SBX01715H.mov")
    assert dl.select(dcfg(csvfile, row="length=30s"), {}, "SBX01715H.mov").number == 3
    assert dl.select(dcfg(csvfile, key_type={"length": "text"}), {}, "SBX01715H.mov").row is None


def test_norm_key_short_ids():
    assert dl.norm_key("AB00215H", "ad_id") == "AB00215" == dl.norm_key("ab00215", "ad_id")
    assert dl.norm_key("ABCD123456D", "ad_id") == "ABCD123456D"      # 11-char Ad-ID kept whole
    assert dl.norm_key("ABCD1234567H", "ad_id") == "ABCD1234567"
    assert dl.norm_key("XYZH", "ad_id") == "XYZH"                    # no digit before H
    assert dl.norm_key(":15", "duration") == "15" == dl.norm_key("15s", "duration")


# --- checks with a fake reader ----------------------------------------------------------
def ctx_for(tmp_path, check_id, name="SBX01715H", frames=210 + 450, extra=None, **kw):
    (tmp_path / "d.csv").write_text(CSV)
    params = {"slate": {"template": str(TPL)}, "job": JOB,
              "deliverables": dcfg(str(tmp_path / "d.csv")), "date": {"window_days": 30}, **(extra or {})}
    chk, ctx = make_ctx(tmp_path, check_id, params, frames=frames, **kw)
    real = tmp_path / f"{name}.mov"
    real.write_bytes(b"x")
    ctx.media.path = real
    ctx.media.raw["format"]["tags"]["creation_time"] = "2026-10-26T14:13:32.000000Z"
    return chk, ctx


def kinds(out):
    return {f.evidence[0]["kind"]: f for f in out}


def page(rows=SET1, **over):
    return colon_words([(l, over.get(l.rstrip(":"), v)) for l, v in rows])


def test_clean_label_colon_slate(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page()
    for cid in ("slate.present", "slate.consistency", "slate.content", "slate.claims"):
        reader._CACHE.clear()
        chk, ctx = ctx_for(tmp_path, cid)
        assert run_check(chk, ctx) == [], cid


def test_label_set_2_clean_with_m_d_date(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(SET2, Date="10/2/2026")
    chk, ctx = ctx_for(tmp_path, "slate.content", name="SBX018H", frames=210 + 900)
    out = run_check(chk, ctx)
    assert out == [], [f.message for f in out]


def test_length_claim_15s_on_a_30_file_fails_with_both_values(fake_ocr, tmp_path):
    """Real known case: a :30 file whose slate says 15s."""
    fake_ocr[1] = fake_ocr[148] = page(Length="15s")
    chk, ctx = ctx_for(tmp_path, "slate.claims", name="SBX01730H", frames=210 + 900)
    (f,) = run_check(chk, ctx)
    assert f.severity == Severity.FAIL and f.evidence[0]["kind"] == "LENGTH"
    assert "slate LENGTH '15s' = 450 frames (15 s x 30 timecode frames/s) but the program is 900" in f.message
    assert (f.expected, f.actual) == (450, 900)
    reader._CACHE.clear()
    chk, ctx = ctx_for(tmp_path, "slate.content", name="SBX01730H", frames=210 + 900)
    out = kinds(run_check(chk, ctx))
    assert set(out) == {"LENGTH"} and out["LENGTH"].expected == "30"     # filename length part


def test_base_ad_id_vs_filename_and_wrong_values(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(**{"Ad-ID": "SBX019", "Advertiser": "Sample Bugrers",
                                          "Date": "13/45/2026", "Audio": "Mono"})
    chk, ctx = ctx_for(tmp_path, "slate.content")
    out = kinds(run_check(chk, ctx))
    assert set(out) == {"AD-ID", "ADVERTISER", "DATE"}
    assert out["AD-ID"].message.startswith("slate does not match the file") and out["AD-ID"].expected == "SBX017"
    assert "MM/DD/YYYY or M/D/YYYY" in out["DATE"].message and out["DATE"].severity == Severity.WARN
    reader._CACHE.clear()
    chk, ctx = ctx_for(tmp_path, "slate.claims")
    assert set(kinds(run_check(chk, ctx))) == {"AUDIO"}


def test_row_vs_filename_composite(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page()
    chk, ctx = ctx_for(tmp_path, "slate.content", extra={})
    ctx.params["deliverables"]["row"] = "#2"                 # the :30 row for a :15 file
    out = kinds(run_check(chk, ctx))
    assert "length differs" in out["row"].message and out["row"].severity == Severity.FAIL


def test_date_equals_row_m_d(fake_ocr, tmp_path):
    fake_ocr[1] = fake_ocr[148] = page(SET2)
    chk, ctx = ctx_for(tmp_path, "slate.content", name="SBX018H", frames=210 + 900,
                       extra={"date": {"mode": "equals_row"}})
    ctx.params["deliverables"]["columns"]["date"] = "Air Date"
    assert run_check(chk, ctx) == []                          # 5/7/2026 == row 5/7/2026
    fake_ocr[1] = fake_ocr[148] = page(SET2, Date="05/08/2026")
    reader._CACHE.clear()
    assert "row date '5/7/2026'" in kinds(run_check(chk, ctx))["DATE"].message


# --- head layout: no bars, slate/black split ---------------------------------------------
def test_bars_skipped_without_bars(tmp_path):
    chk, ctx = ctx_for(tmp_path, "slate.bars")
    with pytest.raises(Skip, match="defines no bars"):
        run_check(chk, ctx)


def test_split_tolerance_moves_the_last_frame_read(fake_ocr, tmp_path):
    fake_ocr[1] = page()
    fake_ocr[148] = []                                        # real slate ended at 144: black
    chk, ctx = ctx_for(tmp_path, "slate.present")
    assert any("no slate found on frame 148" in f.message for f in run_check(chk, ctx))
    reader._CACHE.clear()
    fake_ocr[143] = page()
    chk, ctx = ctx_for(tmp_path, "slate.present", extra={"split_tolerance_frames": 5})
    assert run_check(chk, ctx) == []
    assert reader.slate_frames(ctx.timeline, 5) == [1, 143]
    assert reader.slate_frames(ctx.timeline, 500) == [1]      # never before the first frame
    assert reader.slate_frames(ctx.timeline) == [1, 148]      # default strict
