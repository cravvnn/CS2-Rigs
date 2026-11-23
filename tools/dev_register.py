"""Load the add-on from this checkout for one Blender session, without installing it.

    blender [file.blend] --python tools/dev_register.py
"""

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402

cs2_rigs.register()


def _refresh():
    # Fill the agent, weapon and clip lists so the panel is usable straight away.
    try:
        bpy.ops.cs2rig.refresh()
    except RuntimeError as exc:
        print("cs2_rigs:", exc)
    return None


bpy.app.timers.register(_refresh, first_interval=0.5)
