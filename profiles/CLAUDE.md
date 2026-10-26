# profiles - rules

A profile is the *rules*: which checks run, tolerances, severities, quality mode. Job facts come from a
spec sheet via `${spec.*}` (see ../examples/CLAUDE.md). Decisions: ../CLAUDE.md (D3, D4, D8, D14).

- `final.yaml` - base rules, all checks.
- `review.yaml` - extends final: fast blanking and black_runs (`quality: fast`), filename info-only,
  captions off, blanking and loudness are warnings, blanking `dark_edge_mode: info`, looser
  black-run and silence limits (24 frames).
- `release.yaml` - extends final: `quality: high` explicit, R128 tolerance +-0.5 LU, black-run and
  silence limit 6 frames, blanking and captions gate (FAIL).
- Format: `checks:` is a mapping keyed by check id; reserved keys `enabled` (default true),
  `severity` (default fail), `quality` (fast|high, default high), `max_findings` (integer >= 0:
  problem findings listed per check before the rest is summarised as one "N additional ...
  suppressed; worst: X" entry; 0 = all; default 10 from `cmr check --max-findings`, and an
  explicit `--max-findings` overrides this key); everything else is check params. Suppressed
  findings still count in the summary and exit code (../src/cmr/CLAUDE.md "Findings cap").
  Merge on `extends`: mappings deep-merge, lists/scalars are replaced by the child.
- `video.blanking` `dark_edge_mode: flag | info | hide` (final: flag): how `dark_edge` hits
  (dark picture at a frame edge, no sharp step; see ../src/cmr/checks/blanking/CLAUDE.md) are
  reported: flag = at the check's severity, info = INFO, hide = not listed, only a count + frame
  ranges in one PASS/INFO finding. Sharp `edge` defects and `matte` are not affected.
- Loudness numbers per standard live here (`audio.loudness.standards`), not in the spec.
- `waivers:` (top level, D17; tolerances for `cmr check --waivers`): `video_max_bits` (10),
  `audio_max_db` (2.0), `margin_frames` (5). Defaults live in `src/cmr/waivers.py` DEFAULTS and
  are repeated in final.yaml; see ../src/cmr/CLAUDE.md "Waivers".
