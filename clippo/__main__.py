"""Entry point: python -m clippo"""

import sys


def main() -> int:
    from .app import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
