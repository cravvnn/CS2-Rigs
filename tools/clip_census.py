"""Read every animation clip's DATA block and write the census, without Blender.

    python tools/clip_census.py <census.json> [converter] [game_dir]

Streams one converter dump per clip category, so memory stays small.
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cs2_rigs"))
from core import catalog, clipdata, converter, download, steam  # noqa: E402
from core.cache import Cache, default_cache_root  # noqa: E402


def setup(cli_arg: str = "", game_arg: str = ""):
    install = steam.find_install(game_arg or os.environ.get("CS2RIG_GAME_DIR") or None)
    if install is None:
        raise SystemExit("Counter-Strike 2 was not found.")
    root = Path(os.environ.get("CS2RIG_CACHE_DIR") or default_cache_root())
    cli = converter.find_cli(cli_arg or None) or download.installed_cli(Cache(root, install.version()).tools_dir)
    if cli is None:
        raise SystemExit("No converter found; set CS2RIG_S2V_CLI.")
    conv = converter.Converter(cli)
    return install, conv, Cache(root, install.version(), conv.tag())


def main():
    out = Path(sys.argv[1])
    install, conv, cache = setup(*sys.argv[2:4])
    data = catalog.load_or_build_catalog(conv, install.main_package, cache.catalog_file)
    started = time.monotonic()
    census = clipdata.build_census(
        conv, install.main_package, data["clips"],
        progress=lambda n, total, folder: print(f"[{n}/{total}] {folder}", flush=True),
    )
    clipdata.write_json(out, census)
    clips = census["clips"]
    kinds = {}
    for info in clips.values():
        kinds[info["skeleton"]] = kinds.get(info["skeleton"], 0) + 1
    print("census:", len(clips), "clips in", round(time.monotonic() - started, 1), "s;", kinds)
    print("additive:", sum(i["additive"] for i in clips.values()), "with non-additive version:",
          sum(bool(i["non_additive"]) for i in clips.values()), "missing:", sum(bool(i.get("missing")) for i in clips.values()))


if __name__ == "__main__":
    main()
