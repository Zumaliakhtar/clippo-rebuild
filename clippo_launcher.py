"""Frozen entry point for PyInstaller.

Why this file exists: packaging clippo/__main__.py directly makes it a
top-level script, which breaks the package's relative imports
("attempted relative import with no known parent package").
This launcher uses absolute imports so the `clippo` package stays intact.
"""
import sys

from clippo.app import run

if __name__ == "__main__":
    sys.exit(run())
