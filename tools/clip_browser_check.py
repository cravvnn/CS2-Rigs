"""Draw the clip browser into images without a window, to look at its layout and previews.

    blender --background --factory-startup --python tools/clip_browser_check.py -- [out_dir] [data_dir]

The images go to out_dir, by default a clip_browser_check folder in the system temp folder.
data_dir holds clips.json (a list of clip paths), clip_status.json and clip_previews/. Without it,
the clips come from the installed game and the status and previews from the add-on cache.
A modal operator cannot run in the background, so this draws the same Browser the operator
draws, offscreen, at 1080p and 4K sizes, and checks that the operator registers and cleans up.
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import bpy
import gpu
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cs2_rigs  # noqa: E402
from cs2_rigs import clip_browser  # noqa: E402
from cs2_rigs.core import clipbrowse as cb  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out_dir = Path(args[0]) if args else Path(tempfile.gettempdir()) / "clip_browser_check"
data_dir = Path(args[1]) if len(args) > 1 else None
out_dir.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
cs2_rigs.register()
try:
    gpu.init()  # background runs start without a GPU context
except (AttributeError, RuntimeError):
    pass

if data_dir is not None:
    paths = json.loads((data_dir / "clips.json").read_text(encoding="utf-8"))
    status_file, preview_dir = data_dir / "clip_status.json", data_dir / "clip_previews"
else:
    current = cs2_rigs.session.get()
    paths = current.catalog()["clips"]
    status_file = clip_browser._cache_file(current.cache, "clip_status_file", "clip_status.json")
    preview_dir = clip_browser._cache_file(current.cache, "clip_preview_dir", "clip_previews")
clips = [cb.Clip(path) for path in paths]
status = cb.read_status(status_file)
previewed = [clip for clip in clips if cb.preview_file(preview_dir, clip.path).is_file()]
print(f"CHECK {len(clips)} clips, status {'read' if status is not None else 'missing'}, {len(previewed)} previews")


def browser(**options) -> clip_browser.Browser:
    made = clip_browser.Browser(clips=clips, status=status, preview_dir=preview_dir, **options)
    made.started = 0.0
    return made


def render(name: str, made: clip_browser.Browser, width: int, height: int, scale: float, now: float = 0.0,
           covered=(0, 0, 0, 0)) -> Path:
    offscreen = gpu.types.GPUOffScreen(width, height)
    with offscreen.bind():
        framebuffer = gpu.state.active_framebuffer_get()
        framebuffer.clear(color=(0.22, 0.22, 0.22, 1.0))  # Blender's viewport grey, behind the shade
        with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
            gpu.matrix.load_identity()
            gpu.matrix.load_projection_matrix(
                Matrix(((2 / width, 0, 0, -1), (0, 2 / height, 0, -1), (0, 0, 1, 0), (0, 0, 0, 1)))
            )
            clip_browser.draw_browser(clip_browser.Painter(), made, width, height, scale, now, covered)
        pixels = framebuffer.read_color(0, 0, width, height, 4, 0, "UBYTE")
    offscreen.free()
    pixels.dimensions = width * height * 4
    image = bpy.data.images.new(name, width, height, alpha=False)
    image.pixels.foreach_set([value / 255 for value in pixels])
    path = out_dir / f"{name}.png"
    image.filepath_raw = str(path)
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)
    print("CHECK wrote", path)
    return path


def hover(made: clip_browser.Browser, clip_path: str) -> None:
    """Point the list at a clip the way the mouse would."""
    for index, clip in enumerate(made.visible):
        if clip.path == clip_path:
            made.focus, made.hover = index, ("list", index)
            made.show(clip)
            made.started = 0.0
            return
    raise SystemExit(f"CHECK {clip_path} is not listed")


# Holding an AK-47 with Only Clips for the Held Weapon on: opens on rifle clips, the AK's own first.
held = browser(family="rifle", weapon="ak47", agent="ctm_sas", open_family=True)
assert held.tree[held.node].key == "world/rifle" and held.first_folder == "rifle_ak", held.first_folder
assert held.visible[0].folder == "rifle_ak"
reload_clip = "animation/anims/world/rifle/rifle_ak/reload_ak"
hover(held, reload_clip)
preview = held.preview(held.shown)
if preview is not None:
    for step, seconds in enumerate((0.0, preview.duration * 0.35, preview.duration * 0.7)):
        render(f"1080p_rifle_reload_{step}", held, 1600, 900, 1.0, seconds)
else:
    render("1080p_rifle_reload_nopreview", held, 1600, 900, 1.0)

# Typed search over every clip, the run cycle in the preview.
search = browser(agent="ctm_sas")
for letter in "run_n":
    search.query += letter
    search.refilter()
hover(search, "animation/anims/world/rifle/_default_rifle/run_n_rifle")
render("1080p_search_run", search, 1600, 900, 1.0, 0.3)
render("4k_search_run", search, 3200, 1800, 2.0, 0.3)

# A menu pose, at 4K and in a small viewport.
menu = browser(agent="ctm_sas")
menu.node = next(i for i, node in enumerate(menu.tree) if node.key == "ui_anims/main_menu")
menu.refilter()
hover(menu, "animation/anims/ui_anims/main_menu/ct/ct_main_menu_elite_shrug")
render("4k_menu_pose", menu, 3200, 1800, 2.0, 4.0)
render("small_menu_pose", menu, 1000, 620, 1.0, 4.0)

# The dimmed group, and a clip with no preview: the status and the hint still show.
dimmed = browser()
dimmed.node = next(i for i, node in enumerate(dimmed.tree) if node.key == "viewmodel")
dimmed.refilter()
hover(dimmed, dimmed.visible[3].path)
dimmed.message = f"{dimmed.shown.name} cannot be loaded: {dimmed.shown.reason.lower()}."
render("1080p_not_for_agents", dimmed, 1600, 900, 1.0)
empty = clip_browser.Browser(clips=clips, status=None, preview_dir=out_dir / "no_previews", can_build=True)
hover(empty, empty.visible[5].path)
empty.hover = ("button", -1)
render("1080p_no_status_no_previews", empty, 1600, 900, 1.0)

# With the sidebar open over the 3D View (Region Overlap), the browser moves left of it.
render("1080p_sidebar_open", held, 1600, 900, 1.0, 1.0, covered=(0, 0, 360, 26))

# The modal operator's event handling, fed stand-in events (a modal cannot run in the background).
Operator = clip_browser.CS2RIG_OT_browse_clips
stub = SimpleNamespace(_searched=Operator._searched)
stub._load = lambda context, made, clip: Operator._load(stub, context, made, clip)
lay = cb.layout(1600, 900, 1.0)


def event(kind, value="PRESS", text="", ctrl=False):
    return SimpleNamespace(type=kind, value=value, unicode=text, ctrl=ctrl, alt=False, oskey=False)


def send(made, kind, target=("panel", -1), **options):
    return Operator._handle(stub, bpy.context, event(kind, **options), made, lay, target)


live = browser(family="rifle", weapon="ak47", open_family=True)
for letter in "reload":
    assert send(live, letter.upper(), text=letter) == {"RUNNING_MODAL"}
assert live.query == "reload" and all("reload" in clip.label for clip in live.visible)
assert live.shown == live.visible[0] and live.visible[0].folder == "rifle_ak"
send(live, "BACK_SPACE")
assert live.query == "reloa"
send(live, "G", text="g", ctrl=True)  # shortcuts do not type
assert live.query == "reloa"
send(live, "BACK_SPACE", ctrl=True)
assert live.query == "" and len(live.visible) == 414
send(live, "WHEELDOWNMOUSE", target=("list", 3))
assert live.list_scroll == 3 * lay.row
for _ in range(500):
    send(live, "WHEELDOWNMOUSE", target=("list", 3))
assert live.list_scroll == cb.max_scroll(len(live.visible), lay.row, lay.list)
send(live, "DOWN_ARROW")
send(live, "DOWN_ARROW")
assert live.focus == 2 and live.shown == live.visible[2]  # a search starts on its first match
send(live, "MOUSEMOVE", value="NOTHING", target=("list", 5))
assert live.focus == 5 and live.shown == live.visible[5]
viewmodel = next(i for i, node in enumerate(live.tree) if node.key == "viewmodel")
send(live, "LEFTMOUSE", target=("tree", viewmodel))
assert live.node == viewmodel and live.list_scroll == 0 and not live.visible[0].usable
assert send(live, "LEFTMOUSE", target=("list", 0)) == {"RUNNING_MODAL"}
assert "cannot be loaded" in live.message
send(live, "LEFTMOUSE", target=("tree", 0))
assert send(live, "LEFTMOUSE", target=("list", 0)) == {"RUNNING_MODAL"}  # no agent in this scene
assert "Select an imported agent" in live.message
assert send(live, "ESC") == {"CANCELLED"}
assert send(live, "RIGHTMOUSE") == {"CANCELLED"}
assert send(live, "LEFTMOUSE", target=("outside", -1)) == {"CANCELLED"}
print("CHECK events OK")

# The operator registers, and closing removes its draw handler even with nothing open.
assert hasattr(bpy.types, "CS2RIG_OT_browse_clips")
assert clip_browser._on_load_pre in bpy.app.handlers.load_pre
handler = bpy.types.SpaceView3D.draw_handler_add(clip_browser._draw_callback, (), "WINDOW", "POST_PIXEL")
clip_browser._open.update(browser=held, handler=handler, area=0)
clip_browser._on_load_pre(None)
assert not clip_browser._open
try:
    bpy.types.SpaceView3D.draw_handler_remove(handler, "WINDOW")
    raise SystemExit("CHECK the draw handler was left behind")
except ValueError:
    pass
clip_browser.close_browser()
print("CHECK build previews operator:", "present" if clip_browser._can_build_previews() else "absent, hint shown")
cs2_rigs.unregister()
assert clip_browser._on_load_pre not in bpy.app.handlers.load_pre
print("CHECK OK")
