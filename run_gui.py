#!/usr/bin/env python3
"""Launch the PCBKo GUI."""
import sys
from pathlib import Path

# Ensure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from src.qt_gui import main
except ModuleNotFoundError as exc:
    if exc.name == "PySide6":
        print("PySide6 bulunamadı, Tkinter tabanlı arayüze dönülüyor...")
        from src.gui import main
    else:
        raise

if __name__ == "__main__":
    main()
