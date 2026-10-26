"""cmr doctor: a note (never a problem) when ffmpeg/ffprobe are not the tested 7.1.x."""
from __future__ import annotations

import pytest

from cmr import cli, engine


@pytest.mark.parametrize("line, warn", [
    ("ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers", False),
    ("ffprobe version n7.1 Copyright (c) 2007-2024", False),
    ("ffmpeg version 7.1.2-static https://johnvansickle.com", False),
    ("ffprobe version 5.1.10 Copyright (c) 2007-2025", True),
    ("ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023", True),
    ("ffmpeg version 9.0.2 Copyright (c) 2000-2026", True),
    ("ffmpeg version 7.10.0", True),
    ("ffmpeg version N-112233-gabcdef Copyright", True),
    ("unknown", True),
])
def test_untested_ffmpeg_note(line, warn):
    note = cli.untested_ffmpeg_note("ffmpeg", line)
    assert (note is not None) == warn
    if warn:
        assert note.startswith("WARNING: ffmpeg ") and "tested version 7.1.1" in note


def test_doctor_notes_untested_ffmpeg_but_does_not_fail(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc_real = cli.main(["doctor"])                     # whatever this machine has
    capsys.readouterr()
    monkeypatch.setattr(engine, "ffmpeg_version",
                        lambda b="ffmpeg": f"{'ffprobe' if 'ffprobe' in b else 'ffmpeg'} version 5.1.10 x")
    assert cli.main(["doctor"]) == rc_real             # the note never changes the verdict
    o = capsys.readouterr().out
    assert "WARNING: ffmpeg 5.1 is not the tested version 7.1.1" in o
    assert "WARNING: ffprobe 5.1 is not the tested version 7.1.1" in o
    monkeypatch.setattr(engine, "ffmpeg_version", lambda b="ffmpeg": "ffmpeg version 7.1.1 x")
    cli.main(["doctor"])
    assert "not the tested version" not in capsys.readouterr().out


def test_doctor_fails_on_invalid_job_yaml(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc_clean = cli.main(["doctor"])
    capsys.readouterr()
    (tmp_path / "job.yaml").write_text("job_version: 1\nbogus_key: 1\n")
    rc = cli.main(["doctor"])
    o = capsys.readouterr().out
    assert "INVALID" in o and "PROBLEMS FOUND" in o
    assert rc == cli.EXIT_ERROR
    assert rc_clean in (cli.EXIT_PASS, cli.EXIT_ERROR)
