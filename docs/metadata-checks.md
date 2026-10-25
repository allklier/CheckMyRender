# Metadata checks

Check ids (profiles/final.yaml): container.format, video.codec (codec + profile via value map),
video.resolution (w/h, pixel aspect, display aspect), video.pixel_format, video.fps (r_frame_rate
and avg_frame_rate, exact rational), video.scan, video.color (unknown -> unknown_severity),
audio.format (codec/sample rate/bit depth per stream), audio.layout (stream count + channels per
spec track), filename (explicit filename beats naming_pattern), timing.frame_count (program
frames = file - head - tail), timing.layout (layout fits; first program frame TC ==
program_start_timecode), timing.timecode_start (embedded TC + DF/NDF vs spec, derived if no
file_start_timecode), captions.presence (embedded_608/708 only), decode.errors.
Profiles: `final`, `review` and `release` (both extend final).
