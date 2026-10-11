"""vtskit: DVD-Video titlesets in pure Python.

Steps 1 and 3 of the plan in the README, a disc with titles and menus:

* `vtskit.titleset`: a titleset written from its titles' program streams
  and its menus: the VOBs with their navigation packs filled, the IFO and
  its BUP (`vtskit.menu` describes a menu and its buttons, `vtskit.spu`
  draws the button overlay).
* `vtskit.vmg`: the video manager (VIDEO_TS.IFO, its BUP, the title
  menu's VOB).
* `vtskit.vm`: the navigation commands a program chain carries, encoded.
* `vtskit.nav`: the navigation packs of a VOB filled in (`vtskit.ps` reads
  the program stream for both).
* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.0.1"
__all__ = ["VtsError"]
