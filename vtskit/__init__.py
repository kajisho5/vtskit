"""vtskit: DVD-Video titlesets in pure Python.

The first part of the titleset writer (see the plan in the README):

* `vtskit.nav`: the navigation packs of a VOB filled in (`vtskit.ps` reads
  the program stream for it).
* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.0.1"
__all__ = ["VtsError"]
