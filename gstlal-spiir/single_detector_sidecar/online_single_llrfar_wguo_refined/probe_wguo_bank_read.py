#!/usr/bin/env python3
"""Probe whether a WGuo SPIIR environment can read a split bank XML."""

from __future__ import annotations

import sys

from gstlal_spiir.pipemodules import spiir_utils


def main() -> int:
    for filename in sys.argv[1:]:
        print(f"{filename}\tmaxrate={spiir_utils.get_maxrate_from_xml(filename)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
