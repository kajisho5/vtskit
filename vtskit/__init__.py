"""vtskit: DVD-Video titlesets in pure Python.

Step 1 of the plan in the README, a disc without menus:

* `vtskit.titleset`: a titleset written from its titles' program streams:
  the VOBs with their navigation packs filled, the IFO and its BUP.
* `vtskit.vmg`: the video manager (VIDEO_TS.IFO and its BUP) of a disc
  without menus.
* `vtskit.vm`: the navigation commands a program chain carries, encoded.
* `vtskit.nav`: the navigation packs of a VOB filled in (`vtskit.ps` reads
  the program stream for both).
* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.0.1"
__all__ = ["VtsError"]
