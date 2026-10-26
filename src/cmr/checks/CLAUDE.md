# src/cmr/checks - check plugins

Every module here is auto-imported by `engine.load_checks()`. Contract: `../CLAUDE.md`.
Blanking has its own folder: `blanking/CLAUDE.md`; the slate checks (D27): `slate/CLAUDE.md`. Decisions: ../../../CLAUDE.md.

## Metadata checks
Check ids (profiles/final.yaml): container.format, video.codec (codec + profile via value map),
video.resolution (w/h, pixel aspect, display aspect), video.pixel_format, video.fps (r_frame_rate
and avg_frame_rate, exact rational), video.scan, video.color (unknown -> unknown_severity),
audio.format (codec/sample rate/bit depth per stream), audio.layout (stream count + channels per
spec track), filename (explicit filename beats naming_pattern; D27: `{row.*}`/`{file.*}`/`{job.*}`
placeholders with the deliverables row, see slate/CLAUDE.md), timing.frame_count (program
frames = file - head - tail), timing.layout (layout fits; first program frame TC ==
program_start_timecode), timing.timecode_start (embedded TC + DF/NDF vs spec, derived if no
file_start_timecode), captions.presence (embedded_608/708, vanc_436m), decode.errors,
video.field_order and container.op (optional spec keys). video.fps: NTSC-equivalent rule below.
Profiles: `final`, `review` and `release` (both extend final).

### Probe/metadata/captions gap fixes (2026-10-26; tests `tests/unit/test_probe_gaps.py`, `tests/test_probe_gaps_media.py`)
- Start timecode: a stream `timecode` tag (MOV tmcd/video), else the FORMAT tag (MXF material
  package; ffmpeg writes MXF timecode only there, and so did both real MXF masters).
  `media.timecode_source` = `stream N` | `format`, named in the timing.timecode_start message.
  DF/NDF from ';' vs ':' as before.
- video.fps: exact match passes; an NTSC-equivalent rational (`timecode.fps_equivalent`: same
  `round(fps x 1001/1000)` AND < 0.001 fps apart) passes with ONE INFO finding per field
  (r_frame_rate / avg_frame_rate) naming both values. 2997/125 == 24000/1001, 2997/100 ==
  30000/1001, 5994/100 == 60000/1001; 24 != 23.976, 30 != 29.97, 25 != 24. Time model (D6)
  unchanged: Timeline/frame numbers use the probe's exact value (2997/125 -> nominal 24 TC,
  frames = pts x time base x 2997/125; differs from 24000/1001 by 1e-6 relative, < 0.1 frame
  per hour, and both the frame number and the TC come from the same value). One change:
  drop-frame is now accepted for any rate within 0.001 fps of 30000/1001 or 60000/1001
  (`timecode.is_ntsc`), so an Avid 2997/100 DF file no longer falls back to non-drop.
- video.color: `key_severity: {primaries|transfer|matrix|range: SEV}` sets the severity of a
  MISMATCH per key (others: check severity; untagged: `unknown_severity`); unknown key or
  severity = configuration error. `yuvj420p/422p/444p/440p/411p` are mapped in media.py to the
  base format (`pixel_format`, ffprobe name kept in `pixel_format_raw`) plus range `pc` when
  the stream has no range tag (`color_source.color_range = pixel_format`), so a full-range
  H.264 is reported once, by video.color range, not also by video.pixel_format.
- video.field_order (new): params `video: "${spec.video}"`, `unknown_severity` (warn); spec
  `video.field_order` progressive|tt|bb|tb|bt (ffprobe values; tt = top field first, bb =
  bottom first, tb/bt = coded/displayed differ); absent = not checked (PASS, like other
  optional metadata values); other value = configuration error.
- container.op (new): params `video: "${spec.video}"`; spec `video.operational_pattern`
  (OP1a.., OPAtom; '-' and case ignored); absent = not checked; non-MXF file = SKIPPED. Value from
  format tag `operational_pattern_ul` (byte 13 item 1-3, byte 14 package 1-3 -> a/b/c, byte 13
  0x10 = OPAtom).
- video.codec profile: DNxHR 'DNXHR HQ' -> 'DNxHR HQ' (LB/SQ/HQ/HQX/444). DNxHD ('DNXHD' in
  ffprobe) -> class from the first frame's `pkt_size` (fixed per CID) x fps x 8 = Mbps, nearest
  Avid name (36 45 60 75 80 90 100 110 115 120 145 175 185 220 240 290 350 365 440) within 5 %,
  else the rounded Mbps; `x` for 10-bit pix_fmt: 917504 B @23.976 = 'DNxHD 175' / '175x',
  @25 = 185, @29.97 = 220; 606208 B = 115; 188416 B = 36. Fallback: stream bit_rate (MOV).
- audio: channel_layout missing (MXF PCM) with 2 channels -> 'stereo',
  `channel_layout_source: inferred` (1 channel stays 'unknown': not asked; the check uses channel
  counts anyway).
- captions.presence formats: `embedded_608` (MOV c608, A53, or 436M 608: CDP cc_type 0/1 valid
  or a SMPTE 334-1 DID 0x61/SDID 0x02 packet), `embedded_708` (A53, or 436M CDP with valid
  DTVCC cc_type 2/3), `vanc_436m` (a 436M track with a CDP or 334-1 packet). 436M: media.py
  calls `vanc.read_436m` for each data stream tagged `data_type: vbi_vanc_smpte_436M`:
  `ffprobe -select_streams N -show_packets -show_data -read_intervals %+#30` (first 30 packets,
  ~1 s; 0.3-0.8 s on a 26 GB master, 6.1.1 and 5.1.10 same counts), hexdump parsed, CDP header
  parsed; only counts kept (`media.data[].captions_436m`: packets_read, anc_packets, cdp,
  cdp_active, cc608_valid, cc608_nonnull, cc708_valid, s334_608), never caption text; counts are
  the finding evidence. A 436M track that cannot be read -> ERROR "not verified", not FAIL.
  Limits: captions are looked for only in the first 30 frames (a track whose CDPs start later,
  or carry only padding there, reads as missing 708 / 608: cc608_valid counts valid 608 bytes
  incl. null padding, which encoders mark valid); 10-bit 436M sample coding (7/8/9) parsed but
  UNVERIFIED (no file); no 436M can be generated with ffmpeg 6.1/5.1, so the parser is tested on
  hand-built bytes and the reader command on a MOV tmcd data stream.

## video.black_runs
`src/cmr/checks/black_runs.py`, tests `tests/test_black_runs.py`, `tests/test_quality_modes.py`.
Tap `format=yuv420p,signalstats` (quality high; YHIGH identical to the former yuv444p chain) or
`scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats` (quality fast; every frame,
see src/cmr/checks/CLAUDE.md) (8-bit, tv range kept); frame is black when YHIGH <= `black_luma` (default 24; tv black = 16).
Runs found in Python on integer frames, clipped to program_start..program_end (head/tail layout
black ignored; a run touching program start is counted from program start). Candidate only if
run length > profile `max_black_run_frames` (required; final.yaml sets 12). Severity = profile
`severity` (default fail). Finding gives frame range + timecode + actual run length.
Registration/profile change outside the check: one entry `video.black_runs` in profiles/final.yaml.
Edge black borders stay with video.blanking. Not verified: behaviour on real-world noisy/film-grain
black (YHIGH threshold may need tuning); a frame missing from the signalstats output breaks a run.

### Content on black (logo / end card / credits; user decision: not a black run)
- Params (profile, all optional): `content_on_black` (bool, default true), `content_luma`
  (8-bit tv code, default 100), `content_min_fraction` (default 0.0001 = 0.01% of the frame,
  207 px at 1080p). A dark frame (YHIGH <= black_luma) whose share of pixels above
  `content_luma` is >= `content_min_fraction` is content, not black.
- Second tap (same in fast and high; full-resolution threshold so 1-2 px strokes count):
  `lutyuv=y='if(gt(val,minval+(T-16)*(maxval-minval)/219),maxval,minval)',scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats`,
  key YAVG; fraction = (YAVG-16)/219. Verified: lutyuv minval/maxval follow the input range
  (yuv420p 16/235, 10-bit 64/940, 12-bit 256/3760, yuvj420p and gray 0/255; measured with
  signalstats); a 1/32 white box gave YAVG 22.8438 (= 16 + 219/32) on yuvj420p, yuv420p,
  yuv422p10le and yuv444p12le. signalstats prints YAVG with 6 significant digits (resolution
  ~5e-7 of the frame). `ffmpeg -h filter=lutyuv` (y/u/v expressions; the val/minval/maxval
  constants are not listed by -h, verified by the runs above). `blackframe` was rejected
  (integer percent, prints only frames above `amount`).
- Cost (1440 frames 1080p ProRes HQ, single pass): black high 9.6 s -> 10.5 s with the content
  tap; fast 1.47 -> 2.31 s; the tap alone 1.67 s (decode 0.78). Thresholding after the
  quarter-res downscale would save ~0.15 s but loses thin text (a 1 px line averages to ~71).
- Report: raw dark runs longer than the limit that contained content frames give ONE INFO
  finding each, evidence `kind: content_on_black`, `dark_run_frames`, `content_frames`,
  `content_ranges`, `max_content_fraction`, `black_runs_left` (pure-black pieces still judged
  and reported as usual, e.g. a logo followed by 16 black frames -> INFO + FAIL on the black
  piece). The kind keeps waivers of the two apart.
- Measured on real material (end-of-file excerpts only, two MXF masters; signalstats tv codes):
  logo end card (DNxHD 1080p 23.976): YHIGH 16, YMAX 235, bright share 0.93% (51 frames);
  scrolling credits on black (MPEG-2 1080i 29.97, 1029 frames): YHIGH 16-18 (high), YMAX
  212-222 (fast 168-211), share 0.14-7.3% (minimum 0.142% when the last line scrolls out);
  true digital black in the same file: YMAX 16, share 0; a 3-frame dip through near-black:
  YMAX 16-92, share 0. So 100 / 0.01% sits between fades (<= 92) and text (>= 168 fast, 212
  high), with a 14x margin under the sparsest credits frame. Before -> after on those excerpts:
  logo 93-143 FAIL -> INFO; credits 0-1028 FAIL (fast: three runs) -> INFO; the 30-frame true
  black run stays FAIL. Fast mode: quarter-res YHIGH on dense credits rose to 65-71 (already
  not black), sparse credits stay dark and are excused by the content tap.
- Synthetic (tests/test_black_content.py, high and fast identical): 200x100 logo on black
  30-49 FAIL -> INFO; three 2 px x 400 px moving lines (0.12%) FAIL -> INFO; logo 30-44 +
  black 45-60: FAIL 30-60 -> INFO 30-60 + FAIL 45-60; fade-out to black 40-71 FAIL (unchanged);
  true black 30-49 FAIL (unchanged); `content_min_fraction: 0.005` makes the credits a FAIL again.
- Limits: a dim logo (all of it <= content_luma, e.g. grey 60) still counts as black (raise
  sensitivity by lowering `content_luma`); a hot-pixel cluster >= 0.01% would excuse a black
  frame (not seen on real material); real film grain on black not measured.

## Audio content checks
`src/cmr/checks/audio_content.py`; tests `tests/unit/test_audio_content.py` (no media),
`tests/test_audio_media.py` (generated media with planted defects).
- All four measure the PROGRAM RANGE only: `atrim=start_sample:end_sample` with
  start = program_start*sr/fps, end = (program_end+1)*sr/fps, rounded half-up. No program
  range (layout does not fit) -> SKIPPED.
- audio.loudness: tap `atrim,ebur128=metadata=1:peak=true` on the spec's
  `audio.loudness.program_track` (or one tap per `programs` entry, below). I/LRA from the last 100 ms record, true peak = running max
  (linear, 3 decimals -> 20*log10). Spec names `standard`; profile `standards` presets hold
  target_lufs / tolerance_lu / true_peak_max_dbtp (final: EBU R128 -23 +-1 / -1 dBTP,
  ATSC A/85 -24 +-2 / -2 dBTP); optional `lra_max_lu`. Every message says "BS.1770
  relative-gated ... not dialogue-gated". A PASS finding carries the measured values.
  Integrated-loudness finding = program range; true-peak finding = the first 100 ms block over
  the ceiling (frame range of that block). Unknown standard -> ERROR.
  Each loudness problem finding's evidence[0] carries `kind` (`integrated`, `true_peak`, `lra`)
  so a waiver of one does not cover the others (D17, src/cmr/CLAUDE.md "Waivers").
- Multi-stream programs (PBS-style masters: 8 mono streams, 1-6 = L R C LFE Ls Rs, 7-8 = stereo):
  spec `audio.loudness.programs: [{name, streams: [positions among audio streams, 0-based],
  channels: [L R C LFE Ls Rs M -]}]` (instead of `program_track`; both = config error).
  `channels` names every merged channel in order (default: 2 ch L R, 6 ch L R C LFE Ls Rs; one
  mono stream without channels = as program_track; other counts need `channels`). One tap per
  program: each stream `atrim`, `amerge=inputs=N` in order, `pan=<layout>|FL=c0|...` keeping the
  measured channels (L FL, R FR, C/M FC, Ls SL, Rs SR) - LFE and `-` are dropped BEFORE ebur128.
  Why dropped, measured (ffmpeg 6.1.1, 1 kHz -20 dBFS on one channel): ebur128 weights FL 1.0
  (-23.0 LUFS), BL/SL 1.41 (-21.5), LFE 0 (-70 = gated) when the layout names it (also a bare
  6-channel `amerge` output, read as 5.1), BUT its true peak still includes the LFE (-20 dBFS
  reported for an LFE-only signal). Each program gives its own finding(s); the finding stream =
  the program's first stream, evidence `program`, `streams`, `channels`, `measured_channels`.
  Tap names `r128` (first program) and `r128_<i>`. Engine: `Tap.inputs` (extra streams,
  `[in1]`.. in the chain; build_graph splits shared streams). Verified: `ffmpeg -h filter=amerge`
  (inputs), `-h filter=pan`, `-h filter=join` (channel_layout, map), mono MXF/MOV PCM streams
  probe as 1 ch `unknown` layout and amerge + pan works on them.
  Cross-check (tests/test_audio_multistream.py, mov and mxf): tone at -28.64 dBFS on L R C Ls
  Rs + 60 Hz at -1 dBFS on LFE: cmr 5.1 = -24.0 LUFS (analytic -24.0), true peak -28.6 dBTP;
  independent ffmpeg CLI `join` 5.0(side) + ebur128 summary: -24.0 / -28.6; same with LFE in a
  5.1(side) layout: -24.0 LUFS but peak -1.0 dBFS (what LFE exclusion avoids). Stereo mix
  streams 7-8 at -24 dBFS: -24.0 LUFS. `program_track: 6` (one mono stream) = -27.0 (single
  channel, unchanged behaviour).
- Declared intended silence: `allowed_silence: [{stream (position, 0-based), channels: [1-based]
  (omitted = all), start, end (timecode, inclusive; omitted = program start/end)}]`, from the spec
  (`audio.allowed_silence`, reaching the checks via param `audio: "${spec.audio}"` in final.yaml)
  and/or the profile param `allowed_silence` (both lists are used). A stream not in the file is
  ignored (logged). silence: declared channels are left out of the per-frame test, a frame
  with every channel declared is not judged; a raw run > max_run_frames that lost frames to the
  declaration gives ONE INFO `kind: declared_silence` (`declared_frames`). silent_channel:
  frames in a declared range are not looked at; a channel declared for the whole program that
  is silent -> one INFO per stream (`kind: declared_silence`, `channels`); a partly declared
  channel that is silent everywhere else still FAILs. Declared channels that carry sound are
  not reported (no "should be silent" check). The silence checks measure the program only, so
  a silent head needs no declaration.
- Programs (2026-10-26; tests `tests/unit/test_audio_program_silence.py`,
  `tests/test_audio_program_media.py`): spec `audio.programs: [{name, streams, channels}]` is
  THE place programs are declared (same entry format as above); `audio.loudness.programs` is
  the older spelling and still works; both present and different = config error (equal is
  fine); programs + `program_track` = config error. Shared code: `declared_programs` (reads
  param `audio` = `${spec.audio}` and param `loudness`), `resolve_programs` (-> per merged
  channel `(stream, channel, role)`). final.yaml passes `audio: "${spec.audio}"` to all four
  audio content checks. Why: a PBS-style master (8 mono streams, 5.1 + stereo) gave 1,178
  silence FAILs per mono stream (surrounds are silent for long stretches) and a loudness of one
  mono stream.
  - audio.silence with programs: a PROGRAM is silent in a frame when ALL its measured channels
    (LFE and `-` excluded) are < threshold at once; a run > max_run_frames = ONE finding per
    program, stream = the program's first stream, message `program 5.1 (L(1) R(2) C(3) Ls(5)
    Rs(6)): silence N frames ...` (role(file stream index); `role(s.c)` for multi-channel
    streams), evidence `program`, `streams`, `roles`, `label`. allowed_silence works per
    channel as before (declared channels leave the per-frame test; INFO `declared_silence`
    per program). Streams in NO program are still judged per stream, and a file without
    programs is judged per stream exactly as before (backward compatible).
  - audio.silent_channel with programs: per channel by role, `-` never, LFE only with profile
    `include_lfe: true` (final: false); whole-program silence only (partial silence of a
    surround is not reported); finding stream = the channel's stream, evidence `channel`
    (1-based in the stream, waiver locator as before) + `role` + `program`; message `program
    5.1 channel Rs (stream 6): silent for the whole program ...`. A stream in two programs is
    judged once. Declared whole-program INFO carries `roles`.
  - audio.clipping: unchanged per stream; with programs the message and evidence (`roles`,
    e.g. `5.1 C`) name program and role. A program declaration that does not resolve (stream
    missing, bad channels) only logs a WARN there; silence/silent_channel SKIP / ERROR like
    loudness.
  - audio.loudness: one finding set per program (as before). Without programs and with more
    than one audio stream: one extra finding `kind: undeclared_programs` at
    `undeclared_programs_severity` (info|warn, final: warn) "N audio streams but no programs
    declared: loudness measured only audio stream T ...; declare audio.programs". An explicit
    `program_track` in the spec counts as declaring the program (user decision, 2026-10-26): the
    notice fires only when NEITHER `audio.programs` / `audio.loudness.programs` NOR
    `program_track` is given and the file has more than one audio stream.
  - audio.silence two tiers (user decision; tests `tests/unit/test_audio_program_silence.py`,
    `tests/test_audio_program_media.py`): optional param `warn_run_frames` (integer >= 0). When
    set and < `max_run_frames`: a silent run > warn and <= max is a WARN ("please listen",
    evidence[0] `tier: warn`, severity min(WARN, profile severity)); a run > max keeps the
    profile `severity` (default FAIL, `tier: limit`). Messages name the tier and both limits.
    Absent or >= max = the single tier as before (no `tier` key). Works per program and per stream;
    the declared_silence INFO now triggers above the lower limit. `broadcast_master` sets
    warn 6 / max 30 (FAIL from 31 frames, about 1 s); `release` keeps its single 6-frame limit.
  - Real master (PBS-style, 58 min 1080i 29.97, 8 mono PCM streams, Rocky machine, ffmpeg
    5.1.10, audio-only wrapper of broadcast_master: max_run_frames 6): per-stream before =
    1,178 silence FAILs; per program now 24 (5.1: 11, stereo: 13; program-wide dips of 7-55
    frames, mostly at the same frames in both programs), no silent channel, no clipping.
    Loudness 5.1 I -23.6 LUFS / LRA 5.8 / TP -1.993 dBTP (FAIL vs -2 by 0.007 dB), stereo
    -23.6 / 5.9 / -2.8. Wall 5:32 (ffmpeg pass ~5 min, decode of the video included), max RSS
    1.7 GB (records of three identical astats taps x 8 streams x 104k frames).
  - Limit: ebur128 prints the running true peak LINEAR with 3 decimals, so near -2 dBTP one step
    is ~0.011 dB (0.794 = -2.004, 0.795 = -1.993); a reading within one step of the ceiling is
    at the resolution limit (UNVERIFIED which side the true value is on).
- audio.silence / silent_channel / clipping: tap per audio stream
  `atrim,asetnsamples=n=floor(sr/fps):p=0,astats=metadata=1:reset=1:measure_perchannel=Peak_level:measure_overall=none`
  -> per-frame per-channel sample peak. Frame of chunk k = floor((start + k*n)*fps/sr) from
  the chunk INDEX (not pts_time, see engine issue below). Frames without audio count as silent.
  silence: frame silent when all channels < `threshold_dbfs`; run > `max_run_frames` is a
  problem (final: -60 dBFS, 12 frames). silent_channel: channel peak < `threshold_dbfs` over the
  whole program (final: -60). clipping: runs of frames with any channel >= `clip_dbfs`
  (final: -0.1, severity fail; severity is the profile `severity`).
- Changes outside the module (noted per ownership rule): 4 entries in profiles/final.yaml;
  tests/media/make_media.py base tone level `volume=-17dB` -> `-1.94dB` so the `good` case is
  -23 LUFS (old docstring "tone at -20 dBFS" was wrong: sine source is 1/8 = -18.06 dBFS, `-ac 2`
  mono->stereo adds -3.01 dB, so the old tone was -38.07 LUFS and would fail loudness).
- Verified (ffmpeg 6.1.1, `ffmpeg -h filter=X` for ebur128, astats, asetnsamples, atrim,
  silencedetect, sine, pan + runs): ebur128 metadata keys lavfi.r128.{M,S,I,LRA,true_peak,
  true_peaks_chN,...}; I per record = summary I; true_peak is a running max in LINEAR units;
  ebur128 re-chunks to 100 ms (pts step 4800 at 48 kHz); astats reset=1 = per-frame values,
  -inf on digital silence; atrim keeps original pts; silencedetect `mono=1` gives
  lavfi.silence_start.<ch> (verified, not used: seconds at run edges only).
- Cross-check: generated tones at -10 / -30 / -24.3 LUFS (analytic: stereo 1 kHz sine at P dBFS
  peak = P LUFS) measure -10.0 / -30.0 / -24.3; independent ffmpeg ebur128 summary on a raw lavfi
  sine (no atrim/engine) gives the same to 0.1 LU. Hot head+tail (+2 dBFS): full-file I = +0.4
  LUFS, program-range I = -23.0 (exclusion works). Planted gap frames 40-47 reported 40-47
  (01:00:00:16-01:00:00:23); silencedetect independently: 1.66833-2.00202 s = frames 40..47.
- Engine-interface issues found:
  1. FIXED in engine (exact pts x time base, regression tests in tests/unit/test_engine.py).
     ametadata/metadata print pts_time with 6 significant digits ("3600.04" for 3600.0417 s;
     verified). `MetaRecord.frame` (floor/round of pts_time*fps) can be off by one frame beyond
     ~1000 s. The integer `pts:` field is exact but not kept in MetaRecord. Audio checks here
     avoid it via chunk index; other checks (black_runs, blanking) rely on MetaRecord.frame:
     engine owner should parse `pts:` + stream time base.
  2. No tap sharing: silence, silent_channel and clipping each run an identical astats tap
     (3x asplit per stream). Cheap, but a shared-tap mechanism would remove it.
  3. FIXED: an unknown loudness standard raises `engine.ConfigError` -> ERROR
     "configuration error: standard ... has no preset" (test
     `test_loudness_unknown_standard_is_configuration_error`).
- Not verified / limits: non-48k-multiple rates (sr/fps not an integer, e.g. 29.97) attribute
  chunks with < 1 frame spill (unit-tested arithmetic only, no 29.97 media); 7.1 / Lss-Rss
  layouts (no channel names for them yet); real multi-stream masters (synthetic only); mono program tracks are measured as single
  channel (no `dualmono`); integrated value compared unrounded (3 decimals) to target +- tolerance;
  true-peak location precision = 100 ms block; R128 file-based tolerance is often +-0.5 LU (set
  +-1 per request, profile-tunable); A/85 -2 dBTP ceiling is an assumption (request gave only
  -24 +-2).

## Quality modes (`quality: fast | high`)
### Design
- `quality` is a reserved per-check profile key (like `enabled`/`severity`), default `high`.
  `PROFILE_SCHEMA` only allows `fast` or `high`, so a typo is a config error when the profile
  loads (exit 3).
- `Check.qualities` (default `("high",)`) lists the modes a check implements. If a profile sets
  a mode the check does not implement (e.g. `decode.errors: {quality: fast}`), the engine
  reports an ERROR finding: `bad profile params: quality 'fast' not implemented (supports:
  high)`. That way no report can claim a fast pass for a check that has no fast mode.
- The engine takes `quality` out of the params before the check's `params_schema` validation
  (so no check schema has to list it) and passes it on as `ctx.quality`. taps(), detect()
  and evaluate() read it from `ctx`.
- Every finding a check returns, PASS included, gets `Finding.quality` (`fast`/`high`) in the
  JSON report. Engine-level errors such as an unknown check id leave it `null`. The console
  tags fast findings as `check_id [fast mode]:`. This is an additive optional field, so the
  report schema version stays `1.0`.
- `cmr profiles show` prints the effective `quality` of every check that has more than one
  mode (`high` when the profile does not set it).
- Checks with a fast mode: `video.blanking` and `video.black_runs`. Profiles: `review` sets
  `fast` for both, `release` sets `high` explicitly, `final` defaults to `high`.
  `internal_review.yaml` was removed; `review` replaces it.

### video.blanking
- high: unchanged (every frame).
- fast: each edge tap starts with `select='not(mod(n,S))'` (S = `fast_frame_step`, default 5,
  integer >= 1). Only file frames 0, S, 2S, ... are measured. `n` counts the frames entering
  the tap, i.e. the decoded frames from 0. The engine's split comes before the select, so the
  other checks still see every frame.
- What fast can miss: a defect lasting >= S consecutive frames always contains a multiple of
  S, so it is always seen at least once. A shorter defect is seen only if it covers one; a
  4-frame defect at S = 5 is missed when its start % 5 == 1 (1 phase out of 5). Tested in
  `tests/test_quality_modes.py`: five 5-frame defects at phases 0..4 are all caught; of five
  4-frame defects at phases 0..4, the phase-1 one (frames 81-84) is missed; high finds all
  of them frame-exact.
- Sampled runs: consecutive samples with the same label join into one run. The finding's
  frame range is the first..last SAMPLED frame. The evidence has `frame_step`,
  `frames_sampled`, `possible_frame_start/end` (sampled range widened by S-1 on each side,
  clipped at 0) and `boundary_uncertainty_frames` = S-1. Fast evidence has no `frames` key,
  so there is no exact run length (no false precision). The message reads "seen on N sampled
  frame(s) (fast: 1 in S frames; true start/end up to S-1 frame(s) outside the range shown)".
  `min_run_frames` is compared with the largest possible extent, so a sampled hit is never
  dropped as too short. Two defects separated by fewer than S clean frames can merge into one
  sampled run.
- `persistent` uses sample counts (run samples >= persistent_fraction x labelled samples).

### video.black_runs
- high: `format=yuv420p,signalstats` (was `format=yuv444p,signalstats`). Per-frame YHIGH was
  identical on every frame tested (1,980 frames: stress clip 156, 1440-frame clip, br_long,
  br_dark, br_short, good). Only the chroma planes, which this check does not read, differ.
- fast: `scale=iw/4:ih/4:flags=area+accurate_rnd,format=yuv420p,signalstats`. That is 1/16
  of the pixels, but EVERY frame is still measured, so integer-frame run logic, head/tail
  exclusion and `max_black_run_frames` work exactly as in high. Frame sampling was not used:
  it saved only another 0.3 s per 1440 frames on top of the downscale and would make run
  lengths uncertain.
- Without `accurate_rnd`, area scaling from 10-bit added +1 to flat levels (black measured 17,
  Y=24 measured 25, so a frame at the threshold flipped to "not black"). With it, flat levels
  match high exactly.
- Stress clip (1080p ProRes HQ, 12-frame segments; YHIGH in high / fast; * = black decision
  at <= 24 differs):

  | segment | high | fast | note |
  |---|---|---|---|
  | black | 16 | 16 | |
  | black + noise 10 | 21 | 18 | |
  | black + noise 30 | 32 | 22* | grain averaged: fast calls it black (extra candidate) |
  | Y=20 + noise 10 | 25 | 22* | same |
  | flat Y 22/24/25/26 | 22/24/25/26 | same | threshold behaviour identical |
  | black + 1% white logo | 16 | 16 | |
  | 1px white lines every 8 rows (12.5%) | 235 | 71 | both not black |
  | 1px white lines every 16 rows (6.25%) | 16 | 71* | high: black (90th pct ignores <10%); fast: not black |
  | dark picture / picture | 40 / 210 | 40 / 210 | |

  So fast leans towards more black-frame candidates on grainy near-black. It can miss a
  "black" frame only when thin bright detail covers less than 10% of it, and such a frame
  is visibly not black anyway. Real film grain was not tested.
- `nearest neighbor` downscale was rejected: it keeps the noise statistics but aliases thin
  lines (lines every 8 rows measured 16 = "black").

### Measured timings
16-CPU box, ffmpeg 6.1.1, 1920x1080 ProRes 422 HQ 10-bit, 1440 frames (testsrc2 content),
`-filter_complex_threads 8`, best of 2-3 runs.

Single tap (benchmark script, YHIGH printed):

| chain | s |
|---|---|
| decode only | 0.69 |
| `format=yuv444p,signalstats` (old black_runs) | 19.90 |
| `format=yuv420p,signalstats` (new high) | 8.91 |
| `signalstats` native 10-bit | 12.48 |
| `format=gray,signalstats` | 15.70 |
| `extractplanes=y,signalstats` | 17.29 |
| `scale=480:270:flags=area,format=yuv420p,signalstats` | 1.23 |
| same + `accurate_rnd` (new fast) | 1.21 |
| `select` 1/5 + yuv444p signalstats | 4.29 |
| `select` 1/5 + quarter-res area | 0.92 |
| half-res area | 2.53-3.29 |

Engine single pass (`engine.run_pass`, all four blanking taps + frame counter):

| taps | s |
|---|---|
| counter only (decode) | 0.70 |
| blanking high | 1.24 |
| blanking fast (1/5) | 0.99 |
| blanking high + black_runs old yuv444p | 20.85 |
| blanking high + black_runs high yuv420p | 9.73 |
| blanking fast + black_runs fast | 1.40 |

Whole `cmr check` (all checks incl. audio), same file:

| run | s |
|---|---|
| before: `final`, old black chain | 21.68 |
| after: `final` / `release` (high) | 10.44 / 10.41 |
| after: `review` (fast blanking + black_runs) | 2.05 |

Blanking fast saves only ~0.25 s per 1440 frames (~20% of the blanking pass, ~15 s per hour
of 24p). Decode (0.7 s) dominates and cannot be skipped: ProRes is intra-only and `select`
runs after decode. The mode ships anyway, as requested. The large saving is black_runs:
high is 2.1x faster with equivalent results, and fast is 16x faster than the old chain.
Real footage decodes slower than testsrc2, so the relative saving on real renders will be
smaller (UNVERIFIED, no real renders here).

### Verified ffmpeg facts (ffmpeg 6.1.1)
- `ffmpeg -h filter=select`: options `expr`/`e`, `outputs`. `select='not(mod(n,5))'`
  inside `-filter_complex` passes 288 of 1440 frames, and the sampled pts give file frames
  0, 5, 10, ... (engine frames, tested).
- `ffmpeg -h filter=scale`: `w`, `h`, `flags`. The swscale flag names `area`, `neighbor`,
  `fast_bilinear` and `accurate_rnd` come from `ffmpeg -h full` (`-sws_flags`). Odd output
  sizes (1998x1081 / 4) run without error.
- `ffmpeg -h filter=signalstats`: options `stat`, `out`, `c`/`color` only. There is no way
  to restrict it to luma or to switch off the chroma statistics.
- `ffmpeg -h filter=format|extractplanes|noise|lutyuv|geq|concat` were used to build the
  benchmark and stress media.

### Limitations / not verified
- Equivalence of the high chain was shown on synthetic media (testsrc2, flat levels, noise,
  lines), not on real renders. It relies on swscale producing the same luma for 10->8-bit
  conversion to yuv420p and to yuv444p.
- Fast black_runs on real film grain or noisy black: not tested. The direction is known
  (more candidates), the size is not.
- Blanking fast phase is tied to file frame 0, not to program start.

## Behaviour notes from the first real file (H.264/AAC mp4 from Adobe Media Encoder)
- Untagged sample aspect ratio is treated as square pixels, and an untagged display aspect is width/height x pixel aspect
  (`effective_aspects` in metadata.py); previously these failed as "got None".
- Bit depth is only compared for PCM audio (`bit_depth_comparable`); AAC/AC-3 report none.
- `timing.timecode_start` FAILs when a file has no embedded timecode (mp4 cannot carry a tmcd track). For such files turn
  the check off in the profile (`timing.timecode_start: {enabled: false}`) instead of changing the check.

## Known problems and limits
- Mono-per-channel streams are merged for loudness and judged together by silence /
  silent_channel when the spec declares `audio.programs`; audio.layout does not merge them; layouts other than mono/stereo/5.1/7.1 -> SKIPPED there.
- Only the first video stream is checked.
- Decode errors are not located to a frame (ffmpeg gives no frame number).
- UNVERIFIED: whether the ProRes decoder derives frame color_range from the bitstream or
  defaults it; H.264 A53 `closed_captions` detection (no A53 test media).
