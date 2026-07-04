"""Project path resolution.

The tool currently runs from a source checkout; data/ and locales/
live next to src/. Packaging as a wheel will need package-data here.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
LOCALE_DIR = ROOT / "locales"
