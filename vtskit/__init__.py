"""vtskit: DVD-Video titlesets in pure Python.

Nothing is written yet (see the plan in the README). What is here:

* `vtskit.ifo`: the chapters of a disc and where they start, read from its
  IFO files.
"""

from .errors import VtsError

__version__ = "0.0.1"
__all__ = ["VtsError"]
