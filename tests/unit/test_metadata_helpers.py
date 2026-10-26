from fractions import Fraction

from cmr.checks.metadata import bit_depth_comparable, effective_aspects


def test_untagged_aspect_is_square_pixels():
    v = {"sample_aspect_ratio": "N/A", "display_aspect_ratio": "N/A", "width": 1920, "height": 1080}
    assert effective_aspects(v) == (Fraction(1), Fraction(16, 9))


def test_tagged_aspect_is_kept_and_untagged_display_uses_pixel_aspect():
    v = {"sample_aspect_ratio": "32:27", "display_aspect_ratio": "unknown", "width": 720, "height": 576}
    assert effective_aspects(v) == (Fraction(32, 27), Fraction(720, 576) * Fraction(32, 27))
    v = {"sample_aspect_ratio": "1:1", "display_aspect_ratio": "16:9", "width": 1920, "height": 1080}
    assert effective_aspects(v) == (Fraction(1), Fraction(16, 9))


def test_bit_depth_only_for_pcm():
    assert bit_depth_comparable({"codec": "pcm_s24le"})
    assert not bit_depth_comparable({"codec": "aac"})
