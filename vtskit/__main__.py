"""`python -m vtskit contract [--json]`: the machine-readable contract. vtskit is a
Python library; this is its only command."""

import argparse
import json
import sys

from . import __version__
from .contract import contract


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m vtskit", description="DVD-Video titlesets "
                                "(a Python library); this prints its contract.")
    p.add_argument("--version", action="version", version=f"vtskit {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("contract", help="print the machine-readable contract (JSON)")
    s.add_argument("--json", action="store_true", help="accepted; the output is always JSON")
    p.parse_args(argv)
    print(json.dumps(contract(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
