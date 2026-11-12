# 1. A bitrate planner and session chapters

Status: accepted, 2026-11-12

## Context

Organisers who want a recording of a long event (a conference day, a set of
talks, 1 to 4 hours) on DVD ask first whether it fits on one disc, and how
good it will look. That answer is needed before anything is encoded, and the
disc should get a chapter for each session.

vtskit writes the disc from streams already encoded; it does not choose a
bitrate. The encoder (oncemore, ffmpeg) needs one figure: the average video
bitrate.

## Decision

`vtskit.plan` is arithmetic alone, standard library only, reading and
writing nothing:

* **Discs.** DVD-5 and DVD-9, each the smaller of the recordable discs of
  its kind (DVD-5: DVD+R, 2,295,104 sectors; DVD-9: DVD-R DL, 4,171,712
  sectors), so a plan fits on either. `disc="auto"` takes DVD-5 when it
  meets the floor there, else DVD-9.
* **What is taken off the disc.** A reserve (32 MiB: file system, IFOs and
  BUPs, menus), then a margin (2 % of the rest), as a variable-bitrate
  encoder lands near its average, not on it.
* **What is taken off the stream.** One navigation pack a VOBU at a VOBU
  every half second (32 kbit/s), 1.5 % for the pack and PES headers of the
  other packs, then the sound tracks (192 kbit/s AC-3 each unless given)
  and the subtitle streams.
* **Caps.** The video's average never goes above 9.8 Mbit/s, nor the whole
  stream above 10.08 Mbit/s, DVD-Video's limits.
* **Quality floor.** A stated lowest average video bitrate, 3.5 Mbit/s by
  default for full-size (720-wide) MPEG-2, a parameter. Below it the plan
  still gives the bitrate, marks `meets_floor` false, adds a warning, and
  reports the shortfall: the bytes the disc lacks at the floor, the minutes
  of programme beyond what it holds, and that length (`max_duration`). The
  caller decides: a second disc, a lower floor, or fewer minutes.
* **Chapters.** A chapter list, a line a session (`H:MM:SS name`, or
  `MM:SS`, `#` for comments), or (start, name) pairs, becomes one chapter a
  session. A first chapter at 0 is added when the list starts later. Starts
  must increase and fall before the end; a title holds 99 chapters. The
  times go to `vtskit.titleset.Title(chapters=...)` as they are, and
  vtskit places each at the nearest VOBU start as before, so a chapter lands
  within about half a second of its time; a session under a second long is
  warned about. DVD chapters have no names; the names stay in the plan for
  a menu or a printed list.

## Consequences

* The plan is an estimate built from stated figures, not a measurement of
  an encode. The reserve and the margin keep a real encode inside the disc;
  a caller that knows better (menus with long motion backgrounds, many
  subtitle streams) passes its own.
* A 3 h programme plans on DVD-9 at about 5.9 Mbit/s of video with one
  AC-3 track; on DVD-5 it is about 3.1 Mbit/s, under the floor, about 19
  minutes too long.
* The contract gains a tool, `vtskit/plan`, and a capability,
  `dvd.bitrate_plan` (additive; nothing that was there changes).
* Splitting a programme over two discs at a session boundary is not done
  here; the shortfall says when it is needed.
