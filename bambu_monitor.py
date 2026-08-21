#!/usr/bin/env python3
"""Compatibility launcher for existing Docker and command-line installations."""

from bambu_monitor.app import main


if __name__ == "__main__":
    raise SystemExit(main())
