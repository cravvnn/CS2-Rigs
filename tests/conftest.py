"""Tests cover the Blender-free `core` package, imported without the add-on's bpy code."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cs2_rigs"))
