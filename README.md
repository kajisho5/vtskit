<p><img src="docs/images/logo/lockup-dark.svg" alt="vtskit" height="56"></p>

# vtskit

DVD-Video authoring in pure Python, in place of dvdauthor. vtskit writes
VIDEO_TS itself: the IFO files, the VOBs' navigation packs, menus,
subtitles, angle blocks and the DVD's navigation commands, which it also
compiles from dvdauthor's command language. Where dvdauthor writes the same
thing, vtskit's bytes are dvdauthor's (see
[What is here now](#what-is-here-now)); [oncemore](https://github.com/kajisho5/oncemore)
writes its discs with it since [oncemore#90](https://github.com/kajisho5/oncemore/pull/90)
(angles since [#91](https://github.com/kajisho5/oncemore/pull/91)).

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
DSI left empty), with subtitles added by spumux if there are any. vtskit
fills in the navigation packs; every other pack keeps its place, except
packs of padding alone (spumux leaves some), which are dropped as dvdauthor
drops them.

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

The four steps of the plan are in: discs with titles, menus, subtitles and
angles, dvdauthor's command language, what they are checked with, and
oncemore writing its discs with vtskit.

* `vtskit.titleset` writes a titleset: the title VOBs (one a title, cut
  into files under 1 GiB) with their navigation packs filled, and
  VTS_nn_0.IFO with its BUP: the streams' attributes, each title's chapters,
  a program chain a title (programs and cells one a chapter, its pre, post
  and cell commands given as bytes), the time maps, the cell and VOBU
  address tables. A chapter starts at the VOBU nearest its time, as
  dvdauthor places it. A title's stream is read through a memory map and
  copied out in blocks, its navigation packs kept aside, so a title of
  gigabytes is not held in memory. With menus (`vtskit.menu`: a Menu's stream, its
  Buttons with their rectangles, arrow-key neighbours and commands, its
  cells and which remote key it answers) it also writes the menu VOB, with
  the button overlay as a subpicture (`vtskit.spu`; invisible until a
  button is selected, or drawn in a normal colour all the time) and the
  highlight information in every navigation pack, and the menu tables of
  the IFO. A menu without a stream is a program chain of commands alone, as
  dvdauthor writes a `<pgc>` without a `<vob>` (a title menu that only
  jumps on, a titleset's root menu that only routes). Menus answer the
  remote keys they are marked for (root, audio, subpicture, angle, PTT);
  as dvdauthor arranges it, a domain whose menus name no root menu makes
  its first menu the root, and a titleset without a chapter menu gets one
  that links to the first menu. A title may name the program chains that
  follow, precede and contain it (`next_pgc`, `prev_pgc`, `up_pgc`): under
  libdvdnav a title whose post commands run out plays on into its next
  program chain; dvdauthor writes none of these. A title may be
  several parts in a row, streams and `Angles` blocks of 2 to 9 streams: an
  angle block is interleaved on the disc, its navigation packs point from
  each unit to every angle's next, and its cells are marked as an angle
  block.
* `vtskit.subtitles` adds subtitles to a title's program stream, as spumux
  does: each cue a picture of up to four colours placed on the screen and
  shown for a while, as subpicture packs a little before their time.
* `vtskit.lang` compiles dvdauthor's command language (`jump title 2;`,
  `if (g5 != 0) { button = g5; }`, `call menu entry root;`) for the domain
  a text runs in, to the same commands dvdauthor writes for it: a tool
  that already speaks the language, as oncemore does, keeps its text. A
  button of several commands runs them from the menu's post through a
  dispatcher, and a jump to a chapter of another titleset goes through pad
  program chains, as dvdauthor's "jumppads" do.
* `vtskit.vmg` writes the video manager: VIDEO_TS.IFO and its BUP, with
  the first-play commands, the table of titles and the titlesets'
  attributes, and VIDEO_TS.VOB with the title menu when there is one. With
  `vtskit.titleset` it writes a whole VIDEO_TS.
* `vtskit.vm` encodes the navigation commands (dvdinfo vmi.html): jumps,
  calls and links, the system and general registers, and `If` blocks
  assembled into Goto lines the way dvdauthor compiles its language (a Set
  followed by a Link folded into one command, as it does).
* `vtskit.nav` fills the navigation packs of a VOB: the PCI (times) and DSI
  (where the VOBU and its reference pictures end, where the VOBUs 0.5 s to
  120 s away start, where its sound and subtitles start). `vtskit.ps` reads
  the program stream for both.
* `vtskit.ifo` reads where each chapter of each title starts, as a sector of
  the disc, from the IFO files.
* `tests/dvdnav_sim.py` plays a disc with libdvdnav through ctypes: titles,
  chapters, menus and buttons, sound and subtitle tracks, and angles.

The tests give the same titles to dvdauthor and to vtskit, and the IFO and
the VOBs must be the same byte for byte: NTSC and PAL, 4:3 and 16:9,
chapters on and between VOBU starts, two titles in a titleset, AC-3, LPCM
and MPEG sound with languages, B-pictures, short GOPs, a 40-second title,
a subtitle track, two titlesets; every command form is the bytes dvdauthor
compiles for the same statement, and a disc described in its language
(menus of commands alone, buttons of several commands, cell commands, two
titlesets) comes out with the same IFOs; for menus, the highlight
information of every navigation pack and the IFOs' menu tables are
dvdauthor's (its buttons made with spumux), and a menu's overlay in a
normal colour is spumux's unit byte for byte; subtitles muxed by vtskit are
spumux's units, placed where spumux places them, and the title written
from that stream (4:3, and 16:9 with and without pan-scan or letterbox) is
dvdauthor's. A VIDEO_TS written by vtskit alone plays
its titles and chapters in order under libdvdnav, its commands jumping
between titles, its menus' buttons lead where they say, and its jumps into
another titleset's chapters land; a title with an angle block plays and
switches angles under libdvdnav, every pointer of its navigation packs
landing on the unit it names (dvdauthor writes no angles, so a set-top
player is the check still owed)
(`python -m unittest discover -s tests -t .`; a test is skipped when a tool
it needs is missing). oncemore's switch to vtskit (step 4) is merged:
[oncemore#90](https://github.com/kajisho5/oncemore/pull/90) and
[#91](https://github.com/kajisho5/oncemore/pull/91) keep a copy of `vtskit/`
as `oncemore/_vendor/vtskit`.

`vtskit.ifo` and `tests/dvdnav_sim.py` come from
[oncemore](https://github.com/kajisho5/oncemore) (its `layerbreak.py` and
`tests/dvdnav_sim.py`, GPL-3.0 there) and are relicensed here under
LGPL-3.0-or-later by their author.

## Support

vtskit is free and stays free. If it saves you time, you can support its
development through [GitHub Sponsors](https://github.com/sponsors/kajisho5)
(the **Sponsor** button at the top of this repository). Bug reports help just
as much: [Issues](https://github.com/kajisho5/vtskit/issues).

The logo, colours and writing rules: [docs/DESIGN.md](docs/DESIGN.md).

## License

GNU Lesser General Public License, version 3 or later. See
[COPYING.LESSER](COPYING.LESSER) and [COPYING](COPYING).
