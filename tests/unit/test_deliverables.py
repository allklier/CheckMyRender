"""D27 inputs: job file merge, tokens, deliverables CSV, row matching A/B/C/override."""
from pathlib import Path

import pytest
import yaml

from cmr import deliverables as dl
from cmr.config import ConfigError, build_spec, merge_job

CSV = """Ad-ID,Campaign,Spot,Length,Version,Product,Air Date
ABCD0001000,SUMMER,Picnic,30,v01,,20261001
ABCD0002000,SUMMER,Beach,60,v02,<empty>,20261002
ABCD0003000,WINTER,Picnic,30,v01,Cones,20261003
"""
COLUMNS = {"ad_id": "Ad-ID", "spot": "Spot", "length": "Length", "version": "Version"}
REGEX = r"(?P<campaign>[A-Z]+)_(?P<spot>[A-Za-z]+)_(?P<length>\d+)_(?P<ad_id>[A-Z0-9]{11}[HD]?)_(?P<variant>Slated|Unslated)\.mov"
PATTERN = "{row.campaign}_{row.spot}_{row.length}_{row.ad_id}H_{file.variant}.mov"


@pytest.fixture
def csvfile(tmp_path):
    p = tmp_path / "deliverables.csv"
    p.write_text(CSV)
    return str(p)


def cfg(csvfile, **kw):
    return {"csv": csvfile, "columns": COLUMNS, "filename_regex": REGEX, **kw}


# --- tokens ---------------------------------------------------------------------------
def test_render_tokens_blank_and_empty_marker():
    ns = {"job": {"agency": "Bluefin", "product": "", "client": "<empty>"},
          "row": {"length": "60"}, "file": {"stem": "A_B"}}
    assert dl.render("{job.agency}", ns) == "Bluefin"
    assert dl.render(":{row.length}", ns) == ":60"
    assert dl.render("{file.stem}", ns) == "A_B"
    assert dl.render("{job.product}", ns) is None          # blank = not specified
    assert dl.render("{job.missing}", ns) is None          # missing = not specified
    assert dl.render("", ns) is None and dl.render(None, ns) is None
    assert dl.render("<empty>", ns) == dl.EMPTY             # explicit: must be empty
    assert dl.render("{job.client}", ns) == dl.EMPTY
    assert dl.render("literal", ns) == "literal"


def test_norm_key_ad_id_trailing_h_d():
    assert dl.norm_key("abcd0001000h", "ad_id") == dl.norm_key("ABCD0001000", "ad_id")
    assert dl.norm_key("ABCD0001000D", "ad_id") == "ABCD0001000"
    assert dl.norm_key("ABCD0001000H", "text") == "ABCD0001000H"


def test_csv_rows_canonical_and_normalised_headers(csvfile):
    rows = dl.load_rows(csvfile, COLUMNS)
    assert rows[0]["ad_id"] == "ABCD0001000" and rows[0]["air_date"] == "20261001"
    assert rows[0]["product"] == "" and rows[1]["product"] == "<empty>"
    with pytest.raises(ConfigError, match="not in header"):
        dl.load_rows(csvfile, {"x": "Nope"})


# --- row matching -----------------------------------------------------------------------
NAME = "SUMMER_Beach_60_ABCD0002000H_Slated.mov"


def test_match_key_a(csvfile):
    s = dl.select(cfg(csvfile, key="ad_id", match="key"), {}, NAME)
    assert s.number == 2 and s.row["spot"] == "Beach" and s.how == "key" and not s.problems
    assert s.file["variant"] == "Slated" and s.file["stem"] == NAME[:-4]


def test_match_key_no_row_is_a_problem_not_config(csvfile):
    s = dl.select(cfg(csvfile, key="ad_id"), {}, "SUMMER_Beach_60_ABCD0009000H_Slated.mov")
    assert s.row is None and "no deliverables row with ad_id" in s.problems[0]


def test_match_regex_mismatch_reported(csvfile):
    s = dl.select(cfg(csvfile, key="ad_id"), {}, "whatever.mov")
    assert s.row is None and "does not match deliverables.filename_regex" in s.problems[0]


def test_match_generated_name_b(csvfile):
    job = {"naming_pattern": PATTERN}
    s = dl.select(cfg(csvfile, match="generated_name"), job, NAME)
    assert s.number == 2
    s = dl.select(cfg(csvfile, match="generated_name"), job, "SUMMER_Beach_30_ABCD0002000H_Slated.mov")
    assert s.row is None and "generates" in s.problems[0]


def test_match_both_selects_by_key(csvfile):
    s = dl.select(cfg(csvfile, key="ad_id", match="both"), {"naming_pattern": PATTERN}, NAME)
    assert s.number == 2 and s.how == "both"


def test_match_fields_c_needs_exactly_one(csvfile):
    c = cfg(csvfile, filename_regex=r"(?P<spot>[A-Za-z]+)_(?P<length>\d+)_(?P<version>v\d+)\.mov")
    assert dl.select(c, {}, "Beach_60_v02.mov").number == 2
    with pytest.raises(ConfigError, match="--row"):
        dl.select(c, {}, "Picnic_30_v01.mov")          # rows 1 and 3
    with pytest.raises(ConfigError, match="--row"):
        dl.select(c, {}, "Picnic_99_v01.mov")          # no row


def test_override_d(csvfile):
    c = cfg(csvfile, filename_regex=r"(?P<spot>[A-Za-z]+)_(?P<length>\d+)_(?P<version>v\d+)\.mov")
    assert dl.select({**c, "row": "#3"}, {}, "Picnic_30_v01.mov").number == 3
    assert dl.select({**c, "row": "Air Date=20261003"}, {}, "Picnic_30_v01.mov").number == 3
    assert dl.select({**c, "key": "ad_id", "row": "ABCD0001000H"}, {}, "x.mov").number == 1
    with pytest.raises(ConfigError):
        dl.select({**c, "row": "#9"}, {}, "x.mov")


def test_key_ambiguous_is_config_error(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Ad-ID,Spot\nABCD0001000,A\nABCD0001000H,B\n")
    with pytest.raises(ConfigError, match="2 deliverables rows"):
        dl.select({"csv": str(p), "columns": {"ad_id": "Ad-ID"}, "key": "ad_id",
                   "filename_regex": r"(?P<ad_id>\w+)\.mov"}, {}, "ABCD0001000H.mov")


def test_render_name_d20_and_namespaces():
    ns = {"job": {"title_code": "TC"}, "row": {"spot": "Beach"}, "file": {"variant": "Slated"}}
    tags = {"fps_tag": {"from": "30000/1001", "map": {"30000/1001": "2997"}}}
    assert dl.render_name("{title_code}_{row.spot}_{fps_tag}_{file.variant}.mov", ns, tags) == \
        "TC_Beach_2997_Slated.mov"
    with pytest.raises(KeyError):
        dl.render_name("{row.nope}.mov", ns, tags)


# --- job file merge -------------------------------------------------------------------
def test_merge_job_conflict_and_union():
    spec = {"spec_version": 1, "job": {"title": "X", "client": "A"}}
    out = merge_job(spec, {"job": {"client": "A", "agency": "B"}, "slate": {"template": "t"}})
    assert out["job"] == {"title": "X", "client": "A", "agency": "B"} and out["slate"]["template"] == "t"
    with pytest.raises(ConfigError, match="job.client"):
        merge_job(spec, {"job": {"client": "Other"}})


def test_build_spec_paths_tokens_and_overrides(tmp_path, csvfile):
    spec = tmp_path / "spec.yaml"
    spec.write_text(yaml.safe_dump({"spec_version": 1, "job": {"title": "T"},
                                    "audio": {"tracks": [{"index": 0, "layout": "stereo"}]}}))
    sub = tmp_path / "job"
    sub.mkdir()
    job = sub / "job.yaml"
    job.write_text(yaml.safe_dump({
        "job_version": 1, "job": {"agency": "Bluefin", "audio": "${spec.audio.tracks.0.layout}"},
        "deliverables": {"csv": "../deliverables.csv", "key": "ad_id"},
        "slate": {"template": "slates/t.yaml", "expect": {"TITLE": "{file.stem}"}}}))
    s, inputs = build_spec(spec, job, row="#2")
    assert s["job"]["audio"] == "stereo"                       # ${spec.*}: load time
    assert s["slate"]["expect"]["TITLE"] == "{file.stem}"      # {..}: run time, untouched
    assert Path(s["deliverables"]["csv"]) == Path(csvfile).resolve()
    assert Path(s["slate"]["template"]) == (sub / "slates" / "t.yaml").resolve()
    assert s["deliverables"]["row"] == "#2" and inputs["job"]["sha256"]
    s2, _ = build_spec(spec, job, deliverables_csv=str(job))
    assert s2["deliverables"]["csv"] == str(job.resolve())
    bad = sub / "bad.yaml"
    bad.write_text(yaml.safe_dump({"job_version": 1, "slate": {"tempalte": "x"}}))
    with pytest.raises(ConfigError, match="tempalte"):
        build_spec(spec, bad)
