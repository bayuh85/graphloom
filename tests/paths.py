"""Paths shared by tests. Import this module, not conftest."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "harbor-shop"
