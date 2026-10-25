# Verified ffmpeg/ffprobe facts (ffmpeg 6.1.1, Ubuntu dev box)

Rocky build is UNVERIFIED.

- `-filter_complex` with all branches ending in `nullsink` is rejected ("A filtergraph has
  zero outputs") -> engine labels and maps every branch to `-f null -`.
- `metadata=mode=print:file=F` format, `metadata=mode=add` (frame counter), signalstats in
  native bit depth (10-bit), split/asplit/ametadata options: `ffmpeg -h filter=...`.
- prores profile names (Proxy/LT/Standard/HQ/4444/XQ), `field_order=progressive`, missing
  `color_range` for ProRes-in-MOV, tmcd `timecode` tag, `eia_608` subtitle stream from SCC
  stream-copied into MOV: generated media + ffprobe JSON.
- Decode errors (corrupted ProRes) print on stderr at `-v error` with exit code 0 and no frame
  number.
- ProRes-in-MOV: no stream-level `color_range`, but `-show_frames -read_intervals %+#10` in the
  same ffprobe call reports `tv` on the first decoded frame (H.264 untagged reports nothing).
- SCC timestamps are taken as file time when stream-copied: a caption at 00:59:59:xx is beyond
  the file end and gets dropped (generator uses 00:00:00:01).
- libx264 `-flags +ildct+ilme -x264opts tff=1` -> ffprobe field_order interlaced (tt).
