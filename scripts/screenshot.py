"""Generate the README screenshot without a display.

Runs the real MainWindow under Qt's "offscreen" platform, loads the sample,
selects an element and grabs the window to ``images/cihuang-gui.png``.

Usage::

    python scripts/screenshot.py [input.svg] [output.png]
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from cihuang.gui import MainWindow

    root = Path(__file__).resolve().parent.parent
    svg = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "examples" / "sample.svg"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "images" / "cihuang-gui.png"

    app = QApplication([])
    window = MainWindow()
    window.resize(1180, 780)
    window.show()
    window.load(str(svg))
    app.processEvents()
    window.canvas.fit()
    window.canvas.select(1)
    app.processEvents()

    out.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(out))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
