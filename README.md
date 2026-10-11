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

1. **A titleset writer:** VOB layout (including interleaved angle blocks),
   navigation packs (PCI / DSI) and the VTS IFO. It is used first for titles
   with several angles, which dvdauthor cannot make.
2. **Ordinary titles** through the same writer, compared with dvdauthor's
   output.
3. **Menus, the VMG, and a compiler for DVD navigation commands.**
4. **oncemore without dvdauthor.**

**Clean implementation:** vtskit is written from public descriptions of the
DVD-Video format, not from the source of dvdauthor, libdvdread or libdvdnav.
libdvdnav is only used in the tests, to play the discs vtskit writes.

## License

GNU Lesser General Public License, version 3 or later. See
[COPYING.LESSER](COPYING.LESSER) and [COPYING](COPYING).
