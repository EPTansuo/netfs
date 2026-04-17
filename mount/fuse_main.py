#!/usr/bin/env python3

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from netfs.cli import run


if __name__ == "__main__":
    sys.exit(run(["mount"] + sys.argv[1:]))
