# examples - sample spec sheets

A spec sheet is the *data* for one job (title, fps, duration, timecode, audio layout, head/tail layout).
Rules live in profiles (../profiles/CLAUDE.md). Decisions: ../CLAUDE.md (D3, D4, D7, D20).

- `spec_hd_broadcast.yaml` - long-form HD broadcast (use with `release`). Values are illustrative.
- `spec_review.yaml` - 2-minute review cut (use with `review`).
- `spec_spot_30s.yaml` - 30 s ad spot, exact frame count (720 = 30 s of timecode at 23.976, still a placeholder; use the real spec).
- fps is an exact rational string (`"24000/1001"`), never a float. `head_layout` is a strictly
  sequential list of video segments from file frame 0 (slate, black, bars_tone, two_pop, program).
  `program_start_timecode` normally 01:00:00:00; `file_start_timecode` optional (derived).
- An explicit `job.filename` wins over `job.naming_pattern`; the pattern uses `{title_code}` (filename-safe).
- `spot_xr/` - :60 Extreme Reach spot with a job file, deliverables CSV and slate template (D27;
  see spot_xr/CLAUDE.md). A job file (`cmr check --job`) is the job-level part of the spec.
