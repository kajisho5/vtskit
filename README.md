<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

**Planned:** DVD-Video authoring in pure Python. vtskit will write VIDEO_TS
itself: the IFO files, the VOBs' navigation packs, menus and the DVD's
navigation commands. This repository holds the plan; there is no code yet.

日本語の説明は [README.ja.md](README.ja.md) にあります。

## Why

Open-source DVD authoring today means dvdauthor. It is old, and some things
it cannot do at all, multi-angle titles for one. ffmpeg writes DVD program
streams, but leaves their navigation packs empty. vtskit is meant to be a
complete, tested writer that tools can build on, as
[bdmvkit](https://github.com/kajisho5/bdmvkit) is for Blu-ray.

## Plan

It grows out of [oncemore](https://github.com/kajisho5/oncemore), step by
step, each step checked against the same playback tests. The full plan is in
[oncemore#75](https://github.com/kajisho5/oncemore/issues/75).

1. **A titleset writer, for ordinary titles first:** the VOBs (split at
   1 GiB), their navigation packs (PCI / DSI) and the VTS IFO with its BUP,
   compared with what dvdauthor writes for the same title. It includes a
   small encoder for the navigation commands oncemore's titles carry (pre,
   post and cell commands, given as numbers, not as a language), so it can
   replace dvdauthor's titlesets for real discs.
2. **Titles with several angles,** which dvdauthor cannot make: interleaved
   angle blocks, and the VMG's title table and titleset attributes kept in
   step with them.
3. **Menus, the VMG, and a compiler for DVD navigation commands.**
4. **oncemore without dvdauthor.**

Ordinary titles come first because they can be checked against dvdauthor
field by field, and an angle block cannot be checked before the titleset
around it is right.

**Input:** the MPEG-2 program stream oncemore's encoder makes (ffmpeg
`-f dvd`, 2048-byte packs, a navigation pack at every GOP with its PCI and
DSI left empty). vtskit fills in the navigation packs and keeps every pack
where it is.

**Clean implementation:** the DVD-Video specification itself is not public
(it was licensed under NDA by DVD FLLC). vtskit is written from public
descriptions of the format:

* [DVD-Video information](https://dvdnav.mplayerhq.hu/dvdinfo/) (IFO, PGC,
  PCI / DSI, the instruction set; its author worked without the
  specification);
* [Inside DVD-Video](https://en.wikibooks.org/wiki/Inside_DVD-Video)
  (Wikibooks, CC BY-SA).

Where they say nothing, the bytes dvdauthor writes are observed; its source
is not read, nor libdvdread's or libdvdnav's. libdvdnav is only used in the
tests, through its public API (the function declarations of `dvdnav.h`), to
play the discs vtskit writes.

## What is here now

Nothing is written yet. The first pieces are what the writer will be
checked with:

* `vtskit.ifo` reads where each chapter of each title starts, as a sector of
  the disc, from the IFO files.
* `tests/dvdnav_sim.py` plays a disc with libdvdnav through ctypes: titles,
  chapters, menus and buttons, sound and subtitle tracks, and angles.

The tests build a disc with ffmpeg and dvdauthor and check both against it
(`python -m unittest discover -s tests -t .`; a test is skipped when a tool
it needs is missing).

Both come from [oncemore](https://github.com/kajisho5/oncemore) (its
`layerbreak.py` and `tests/dvdnav_sim.py`, GPL-3.0 there) and are
relicensed here under LGPL-3.0-or-later by their author.

## Support

vtskit is free and stays free. If it saves you time, you can support its
development through [GitHub Sponsors](https://github.com/sponsors/kajisho5)
(the **Sponsor** button at the top of this repository). Bug reports help just
as much: [Issues](https://github.com/kajisho5/vtskit/issues).

The logo, colours and writing rules: [docs/DESIGN.md](docs/DESIGN.md).

## License

GNU Lesser General Public License, version 3 or later. See
[COPYING.LESSER](COPYING.LESSER) and [COPYING](COPYING).
