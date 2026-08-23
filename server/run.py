#!/usr/bin/env python3
"""Entrypoint for the cafetaria server.

Usage:
    python run.py [--config /path/to/config.yml]

The configuration file is resolved from (in order):
  1. --config argument
  2. CAFETARIA_CONFIG environment variable
  3. ../config.yml relative to this file
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    parser = argparse.ArgumentParser(description="Cafetaria PWA server")
    parser.add_argument("--config", help="Path to config.yml", default=None)
    args = parser.parse_args()

    os.environ.setdefault("CAFETARIA_CONFIG", args.config) if args.config else None

    from cafetaria_app.main import main as run_main

    run_main()


if __name__ == "__main__":
    main()
