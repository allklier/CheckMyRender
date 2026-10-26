# CheckMyRender - roadmap and next-version list

One list, two sections. **Roadmap** = new capabilities. **Next version** = known gaps and refinements of
what exists. Decision numbers (Dn) are in CLAUDE.md. Priority: P1 soon, P2 next, P3 later.

## Next version (known gaps in what exists)
- P1 Blanking: per-event summary (one defect currently shows as 2-3 findings: edge/class/stage-1 splits).
- P1 Blanking: stage-1 visibility of 1 px low-contrast dark lines next to dark picture (separate from context).
- P1 Engine: `finding.verifier` is overwritten when an ML verifier is registered; make it a chain.
- P2 Blanking: planted masks inside a fade from black at an already black edge read as `transition` (known gap).
- P2 Loudness: finer true-peak measurement (ebur128 prints linear with 3 decimals; ~0.011 dB steps near -2 dBTP).
- P2 Probe: fall back to the MOV `colr` atom / frame tags when ffprobe 5.x returns unknown colour (only matters for `--use-system-ffmpeg`).
- P2 DNxHD/MXF seek precision (blanking context evidence differs by one frame across ffmpeg builds); strict compare ignores evidence internals.
- P2 Metadata findings cannot be waived (D17): metadata waivers; waivers for content that moved in time; reviewer identities.
- P2 Stills policy (D19), HTML/JUnit reports.
- P3 Migration to ffmpeg 9.1 LTS when released: re-verify baselines and waivers.
- P3 Caption sidecars and caption quality; wrapped slate values; best-fit slate template auto-detection.
- P3 launchd LaunchAgent end to end, `xattr` quarantine text, Miniforge bootstrap on Linux (UNVERIFIED).
- P3 Silence events on the long PBS master (01:21:59;10 and the last 36 frames): listening review postponed by the user.

## Roadmap (new capabilities)
- P1 ML verifier for blanking and freeze frames (D9), trained from reviewer labels (first labels: suite/stills/pbs_blanking/labels.yaml, private).
- P1 Dialogue-gated loudness (meter choice OPEN, D15).
- P2 Freeze frames (two-stage with ML), duplicate/dropped frames, timecode track continuity.
- P2 Basic legal levels / full gamut, visual colour-space sanity (log-looking footage), banding.
- P2 A/V sync offset, 2-pop detection, head/tail content checks (D7), bars/tone level.
- P3 Watermarks (accidental), lip-sync ML, phase/polarity, DC offset, field order/cadence, pillarbox allowance, dual-track detection, checksum sidecar, act-break black.
- P3 Image sequences (D5), batch/watch-folder, stems and 8-channel layouts, slate vs reference sheet extensions.
- Deferred/out: PSE/flash.
