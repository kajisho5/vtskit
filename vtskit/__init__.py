"""vtskit: DVD-Video titlesets in pure Python.

The first piece of the writer (see the plan in the README):

* `vtskit.vob`: a title's program stream with its navigation packs filled
  in, and its cells (`vtskit.nav` is the packets' layout, `vtskit.ps` reads
  the stream).
* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.0.1"
__all__ = ["VtsError"]
