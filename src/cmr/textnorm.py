"""Unicode NFC normalisation of text that is compared (D32). macOS (APFS/HFS+, SMB shares
mounted there) can hand out file names in decomposed form (NFD: "e" + U+0301), while the same
name typed in a spec, a CSV or read by OCR is composed (NFC: U+00E9). Python compares code
points, so both are normalised to NFC where they enter cmr: media file names
(`MediaInfo.name`/`.stem`), spec/job/slate-template YAML, deliverables CSV cells, OCR words
and waiver text fingerprints. Paths used to OPEN files are never changed."""
from __future__ import annotations

import unicodedata


def nfc(value):
    """NFC of a string; dicts (keys and values) and lists are normalised recursively; any other
    value is returned unchanged."""
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        return {nfc(k): nfc(v) for k, v in value.items()}
    if isinstance(value, list):
        return [nfc(v) for v in value]
    return value
