from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from .gui import ScxGuiWindow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI frontend for the installed scx package")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Construct the UI and exit immediately. Useful for headless checks.",
    )
    args = parser.parse_args(argv)

    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    app = QApplication(sys.argv if argv is None else [sys.argv[0], *argv])
    window = ScxGuiWindow(auto_refresh=not args.smoke_test)

    if args.smoke_test:
        QTimer.singleShot(0, app.quit)
    else:
        window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
