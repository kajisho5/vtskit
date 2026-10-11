# vtskit: notes for Claude Code

Read `README.md` first: it carries the plan, the input contract and the
sources. The full plan and its decisions are in
[oncemore#75](https://github.com/kajisho5/oncemore/issues/75) (the comments
hold the current decisions; the body is older).

## Clean implementation (never relax this)

* Write the IFO, VOB and NAV pack code from the public descriptions named in
  the README (dvdnav.mplayerhq.hu/dvdinfo, Wikibooks "Inside DVD-Video").
* Do not read the source of dvdauthor, libdvdread or libdvdnav, nor
  describe what it does from memory. Where the descriptions say nothing,
  observe the bytes dvdauthor writes for a test disc and say so in a comment.
* libdvdnav is a test tool only, called through the declarations of
  `dvdnav.h` (its public API). It is never imported by `vtskit/`.
* Do not guess a field's meaning. When a description is unclear, check it
  against dvdauthor's output, or leave the field alone and note it.

## Conventions

* Python 3.11+, standard library only, relative imports inside `vtskit/`
  (it is vendored verbatim into `oncemore/_vendor/vtskit`).
* One exception type: `VtsError` in `vtskit/errors.py`.
* Tests: `python -m unittest discover -s tests -t .`. A test that needs a
  tool skips without it; CI on Ubuntu installs every tool and fails on any
  skip, so every test must run there.
* Input is the program stream oncemore's encoder makes (ffmpeg `-f dvd`):
  2048-byte packs, one navigation pack per GOP with empty PCI and DSI. vtskit
  fills the packs and moves nothing.
* Every writer comes with a test that builds the same title with dvdauthor
  and compares, and a playback check through `tests/dvdnav_sim.py`.

## Tools in a cloud session

`.claude/hooks/session-start.sh` installs ffmpeg, dvdauthor and libdvdnav4
(apt). ffmpeg 7's `dvdvideo` demuxer needs a GPL build with libdvdnav and
is not available here; that check stays local.

## Writing

English in code and docs, Japanese in `README.ja.md`, kept in step with
`README.md`. Plain sentences, no marketing. See `docs/DESIGN.md`.
