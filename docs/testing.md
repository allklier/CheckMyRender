# Test media and test runs

`python3 tests/media/make_media.py OUTDIR [case...]` - cases good, wrong_meta, wrong_tc, short,
no_captions, corrupt; matches tests/media/spec_test.yaml (48-frame program, 1+23 head, 24 tail).
`make_media.make(outdir, name, vf_extra=..., af_extra=..., ...)` for planting content defects.
Tests use $CMR_TEST_MEDIA (default <tmp>/cmr_test_media); media is never written to the repo.
