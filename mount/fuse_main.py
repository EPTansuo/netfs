#!/usr/bin/env python3

import sys

from netfs.cli import run


if __name__ == "__main__":
    sys.exit(run(["mount"] + sys.argv[1:]))
