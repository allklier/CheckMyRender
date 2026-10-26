from pathlib import Path

import pytest
import yaml

from cmr.config import ConfigError, load_profile_raw, load_spec, resolve_profile, substitute

ROOT = Path(__file__).resolve().parents[2]
SPEC = {"video": {"width": 1920, "fps": "24000/1001", "color": {"range": "tv"}},
        "audio": {"tracks": [{"index": 0, "layout": "stereo"}]}, "job": {"title": "X"}}


def w(d: Path, name: str, data: dict) -> Path:
    p = d / f"{name}.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


def test_typed_substitution():
    out = substitute({"a": "${spec.video.width}", "b": "${spec.video.color}",
                      "c": "${spec.audio.tracks}", "d": "${spec.audio.tracks.0.layout}",
                      "e": "w=${spec.video.width} t=${spec.job.title}", "f": 5}, SPEC)
    assert out == {"a": 1920, "b": {"range": "tv"}, "c": [{"index": 0, "layout": "stereo"}],
                   "d": "stereo", "e": "w=1920 t=X", "f": 5}
    assert isinstance(out["a"], int)


def test_substitution_returns_copies():
    out = substitute({"b": "${spec.video.color}"}, SPEC)
    out["b"]["range"] = "pc"
    assert SPEC["video"]["color"]["range"] == "tv"


@pytest.mark.parametrize("bad", ["${spec.video.height}", "${spec.audio.tracks.3}",
                                 "${profile.x}", "x ${spec.video.color} y", "${spec}"])
def test_substitution_errors(bad):
    with pytest.raises(ConfigError):
        substitute({"k": bad}, SPEC)


def test_extends_merge_and_list_replace(tmp_path):
    w(tmp_path, "base", {"profile_version": 1, "name": "base", "checks": {
        "a": {"severity": "fail", "x": 1, "lst": [1, 2], "nested": {"p": 1, "q": 2}},
        "b": {"y": "${spec.video.width}"}, "c": {"y": "${spec.video.width}"}}})
    child = w(tmp_path, "child", {"profile_version": 1, "name": "child", "extends": "base",
                                  "checks": {"a": {"lst": [9], "nested": {"q": 3}},
                                             "b": {"enabled": False}}})
    merged, shas = load_profile_raw(str(child))
    assert merged["name"] == "child" and "extends" not in merged
    assert merged["checks"]["a"] == {"severity": "fail", "x": 1, "lst": [9], "nested": {"p": 1, "q": 3}}
    assert merged["checks"]["b"] == {"y": "${spec.video.width}", "enabled": False}
    assert len(shas) == 2
    resolved, _ = resolve_profile(str(child), SPEC)       # substitution after extends
    assert resolved["checks"]["c"]["y"] == 1920
    assert resolved["checks"]["b"]["y"] == "${spec.video.width}"   # disabled: lazy, not looked up


def test_extends_cycle(tmp_path):
    w(tmp_path, "a", {"profile_version": 1, "name": "a", "extends": "b", "checks": {}})
    w(tmp_path, "b", {"profile_version": 1, "name": "b", "extends": "a", "checks": {}})
    with pytest.raises(ConfigError, match="cycle"):
        load_profile_raw(str(tmp_path / "a.yaml"))


def test_profile_schema_rejects_unknown_top_level(tmp_path):
    p = w(tmp_path, "p", {"profile_version": 1, "name": "p", "checks": {}, "chekcs": {}})
    with pytest.raises(ConfigError, match="invalid"):
        load_profile_raw(str(p))


def test_bundled_profiles_resolve_against_example_spec():
    spec, _ = load_spec(ROOT / "examples" / "spec_hd_broadcast.yaml")
    for name in ("final", "review", "release"):
        prof, _ = resolve_profile(name, spec)
        assert prof["checks"]["video.fps"]["expect"] == "24000/1001"
        assert prof["checks"]["video.resolution"]["width"] == 1920
    prof, _ = resolve_profile("review", spec)
    assert prof["checks"]["filename"]["severity"] == "info"
    assert prof["checks"]["captions.presence"]["enabled"] is False


@pytest.mark.parametrize("timing,msg", [
    ({"head_layout": [{"type": "program"}, {"type": "black", "frames": 2}]}, "last"),
    ({"head_layout": [{"type": "black"}, {"type": "program"}]}, "needs 'frames'"),
    ({"tail_layout": [{"type": "program"}]}, "tail_layout"),
    ({"head_layout": [{"type": "leader", "frames": 2}, {"type": "program"}]}, "invalid"),
])
def test_spec_layout_validation(tmp_path, timing, msg):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"spec_version": 1, "timing": timing}))
    with pytest.raises(ConfigError, match=msg):
        load_spec(p)


def test_spec_rejects_float_fps(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(yaml.safe_dump({"spec_version": 1, "video": {"fps": 23.976}}))
    with pytest.raises(ConfigError):
        load_spec(p)
