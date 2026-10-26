"""D32 NFC normalisation: decomposed (NFD, as macOS APFS/SMB can return) and composed (NFC,
as typed) forms of the same text compare equal wherever cmr compares names or text. Runs on
Linux: the NFD strings are built explicitly."""
import unicodedata
from fractions import Fraction
from pathlib import Path

from cmr import deliverables as dl
from cmr import waivers
from cmr.checks.filename import Filename
from cmr.checks.slate import ocr
from cmr.config import _load_yaml
from cmr.engine import CheckContext, Timeline
from cmr.media import MediaInfo
from cmr.model import Severity
from cmr.textnorm import nfc

COMPOSED = "Caf\u00e9_R\u00e9sum\u00e9"                 # what a user types
DECOMPOSED = unicodedata.normalize("NFD", COMPOSED)     # what an APFS listing can return
assert COMPOSED != DECOMPOSED


def is_nfc(s):
    return unicodedata.is_normalized("NFC", s)


def test_nfc_recursive_and_passthrough():
    out = nfc({DECOMPOSED: [DECOMPOSED, 3, None, {"k": DECOMPOSED}]})
    assert out == {COMPOSED: [COMPOSED, 3, None, {"k": COMPOSED}]}
    assert nfc(5) == 5 and nfc(None) is None


def test_media_name_is_nfc_but_path_unchanged():
    m = MediaInfo(Path(f"/x/{DECOMPOSED}.mov"), "file", {}, "mov")
    assert m.name == f"{COMPOSED}.mov" and m.stem == COMPOSED
    assert str(m.path).endswith(f"{DECOMPOSED}.mov")      # the real path still opens the file


def test_yaml_spec_and_job_strings_are_nfc(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(f"job:\n  filename: {DECOMPOSED}.mov\n  {DECOMPOSED}: x\n", encoding="utf-8")
    data, _ = _load_yaml(p)
    assert data["job"]["filename"] == f"{COMPOSED}.mov" and COMPOSED in data["job"]


def test_csv_cells_and_headers_are_nfc(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text(f"Ad-ID,Tıtle {DECOMPOSED}\nABCD0001000,{DECOMPOSED}\n", encoding="utf-8")
    rows = dl.load_rows(str(p), {"title": f"Tıtle {COMPOSED}"})
    assert rows[0]["title"] == COMPOSED
    assert all(is_nfc(k) and is_nfc(v) for k, v in rows[0].items())


def test_file_tokens_nfd_name_matches_nfc_regex():
    tok, ok = dl.file_tokens(f"{DECOMPOSED}_30.mov", rf"(?P<title>{COMPOSED})_(?P<len>\d+)\.mov")
    assert ok and tok["title"] == COMPOSED and tok["stem"] == f"{COMPOSED}_30"


def test_ocr_words_are_nfc():
    hdr = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    tsv = f"{hdr}\n5\t1\t1\t1\t1\t1\t10\t20\t30\t40\t95.0\t{DECOMPOSED}\n"
    assert [w.text for w in ocr.parse_tsv(tsv)] == [COMPOSED]


def test_waiver_text_fingerprint_nfd_vs_nfc():
    a = waivers.text_fingerprint([{"kind": "TITLE", "text": DECOMPOSED}])
    b = waivers.text_fingerprint([{"kind": "TITLE", "text": COMPOSED}])
    assert waivers.distance(a, b) == 0
    c = waivers.text_fingerprint([{"kind": "TITLE", "text": "Cafe_Resume"}])
    assert waivers.distance(a, c) == 1


def test_filename_check_nfd_file_vs_nfc_spec():
    tl = Timeline(Fraction(24), False, 10, 0, "none", 0, 0, 0, 9)
    media = MediaInfo(Path(f"/x/{DECOMPOSED}.mov"), "file", {}, "mov")
    ctx = CheckContext("filename", media, {}, {"job": {"filename": f"{COMPOSED}.mov"}},
                       Severity.FAIL, tl)
    assert Filename().evaluate(ctx, []) == []
    ctx.params = {"job": {"filename": "other.mov"}}
    assert len(Filename().evaluate(ctx, [])) == 1


def test_media_name_is_the_name_stored_on_disk(tmp_path, monkeypatch):
    """Case-insensitive volumes (APFS default, SMB) open `clip.mov` for `CLIP.mov`: the
    filename check must compare the stored name. Simulated with a listing on any OS."""
    import cmr.media as m
    real = tmp_path / "SHOWNAME_101.mov"
    real.write_bytes(b"")
    assert MediaInfo(real, "file", {}, "mov").name == "SHOWNAME_101.mov"
    asked = tmp_path / "showname_101.mov"        # what the user typed on a case-insensitive volume
    assert MediaInfo(asked, "file", {}, "mov").name == "SHOWNAME_101.mov"
    (tmp_path / f"{DECOMPOSED}.mov").write_bytes(b"")
    assert MediaInfo(tmp_path / f"{COMPOSED}.mov", "file", {}, "mov").name == f"{COMPOSED}.mov"
    (tmp_path / "Showname_101.mov").write_bytes(b"")  # 2nd entry on a case-sensitive volume only
    import os
    if len([n for n in os.listdir(tmp_path) if n.lower() == "showname_101.mov"]) == 2:
        assert MediaInfo(asked, "file", {}, "mov").name == "showname_101.mov"     # ambiguous: as given
    assert MediaInfo(tmp_path / "nodir" / "x.mov", "file", {}, "mov").name == "x.mov"


def test_unsafe_tmpdir_falls_back_to_tmp(tmp_path, monkeypatch):
    from cmr import engine
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path / "with space"))
    assert engine._tap_tmp_base() == "/tmp"
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    assert engine._tap_tmp_base() is None
