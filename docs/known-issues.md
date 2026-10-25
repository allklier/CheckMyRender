# Known problems and limits

- audio `merge` (Flame mono-per-channel streams) mentioned in the example spec comment is not
  implemented; layouts other than mono/stereo/5.1/7.1 -> SKIPPED.
- Only the first video stream is checked.
- Decode errors are not located to a frame (ffmpeg gives no frame number).
- UNVERIFIED: whether the ProRes decoder derives frame color_range from the bitstream or
  defaults it; H.264 A53 `closed_captions` detection (no A53 test media).
