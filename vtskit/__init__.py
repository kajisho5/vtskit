"""vtskit: DVD-Video titlesets in pure Python.

Steps 1 to 3 of the plan in the README, a disc with titles, menus,
subtitles and angles, and dvdauthor's command language:

* `vtskit.titleset`: a titleset written from its titles' program streams
  (several parts in a row, with angle blocks) and its menus: the VOBs with
  their navigation packs filled, the IFO and its BUP (`vtskit.menu`
  describes a menu and its buttons, `vtskit.spu` draws the button overlay).
* `vtskit.vmg`: the video manager (VIDEO_TS.IFO, its BUP, the title
  menu's VOB).
* `vtskit.subtitles`: subtitles added to a title's program stream, as
  spumux does.
* `vtskit.vm`: the navigation commands a program chain carries, encoded;
  `vtskit.lang` compiles dvdauthor's command language to them.
* `vtskit.nav`: the navigation packs of a VOB filled in (`vtskit.ps` reads
  the program stream for both).
* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.3.0"
__all__ = ["VtsError"]
