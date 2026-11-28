<div align="center">

<img src="docs/hero.gif" alt="CS2 Rigs in Blender: SAS agent with an AK-47 | Case Hardened and Driver Gloves | Black Tie on Mirage, with the CS2 Rigs panel and Clip Browser">

# CS2 Rigs

**Counter-Strike 2 agents, weapons, finishes and maps in Blender, from your own game install.**

[![Download](https://img.shields.io/badge/Download-Releases-2ea44f?logo=github&logoColor=white)](https://github.com/cravvnn/CS2-Rigs/releases)
[![Sponsor](https://img.shields.io/badge/Sponsor-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/cravvnn)
[![Report an issue](https://img.shields.io/badge/Report%20an%20issue-6e7781?logo=github&logoColor=white)](https://github.com/cravvnn/CS2-Rigs/issues/new/choose)

![Platform](https://img.shields.io/badge/platform-Windows%20x64-0078D4)
![Blender](https://img.shields.io/badge/Blender-5.3-E87D0D?logo=blender&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-2ea44f)
[![Latest release](https://img.shields.io/github/v/release/cravvnn/CS2-Rigs?include_prereleases&label=latest%20release&color=orange)](https://github.com/cravvnn/CS2-Rigs/releases)

</div>

Import any agent with a posable rig, put a weapon in their hands with the finish
you want, play the game's own animation clips and stand them in a real map. Everything
is converted from the game files on your disk, offline.

**Current release: 0.4.0-alpha · Windows x64 · Blender 5.3.**

> **Alpha.** Expect rough edges and report what breaks. CS2 Rigs only reads the
> game files on disk. It never opens, injects into or talks to the running game.
> Not affiliated with or endorsed by Valve.

[Quick start](#quick-start) · [Features](#features) · [Compatibility](#compatibility) ·
[Known issues](#known-issues) · [Support](#support) · [Credits](#credits)

## Quick start

1. Download `CS2_Rigs_<version>_Windows_x64.zip` from [Releases](https://github.com/cravvnn/CS2-Rigs/releases). Don't unzip it.
2. Drag the zip into Blender, or use **Edit > Preferences > Get Extensions > Install from Disk**.
3. In the 3D View press **N** and open the **CS2 Rigs** tab. Press **Download Converter** (65 MB, once). Blender's
   online access must be allowed under **Preferences > System**.
4. Press **Read Game Files**. The first time it reads the game's catalogue and finishes, which takes about half a minute.
   After that, opening a saved file fills the lists and matches them to the agent in it.
5. Choose an agent and press **Import Agent**, then **Hold Weapon**, **Apply Finish**, **Load Clip** and **Add Pose Controls**.

The tab is split into **Agent**, **Weapon**, **Stickers**, **Gloves**, **Animation**, **Pose Controls** (or **POV Camera** for first-person rigs), **Map**, **Studio** and **Tools**.
Collapsed section headers show what is in them, such as the held weapon and its finish, and the top of the tab says
what to do next. A failed step stays at the top with **Open Log Folder** until the next step works.

The game is found through Steam. If it is somewhere unusual, set **Game Folder** in the add-on preferences.

| First time | About |
| --- | --- |
| Download Converter | 65 MB, once |
| Read Game Files | about 30 s, then instant |
| Import an agent, hold a weapon or load a clip | a few seconds each, then from the cache |
| Apply a finish | about 10 s, then from the cache |
| Add a map | 3 to 5 minutes and about 1 GB, then seconds |

## Features

- **Agents & first-person arms.** All third-person agents with their materials and skeleton, plus first-person viewmodel arms with agent sleeves, custom gloves and a calibrated Source 2 POV camera.
- **Weapons.** Every weapon and knife, either held by an agent/first-person rig (automatically posed with its idle clip) or imported standalone without an agent.
- **Finishes & stickers.** Weapon, knife and glove finishes built the way the game builds them, plus live UV-layered stickers across 11,918 kits with scrape wear, foil/holo shaders and custom offset/scale/rotation.
- **Animation clips.** Every third-person and viewmodel game clip, filtered to the held weapon, with a visual Clip Browser.
- **Pose controls.** IK/FK arms and legs, foot roll, toe, twist bones, and weapon and hand follow switches.
- **Maps & lighting.** Game maps with their real HDR `.exr` skybox, calibrated sun and interior lights, and baked indoor cubemap probes — plus a one-click **Studio** kit with 3-point lighting, turntable and a standalone 21-sky CS2 HDRI picker.

### Agents and clips

Press **Read Game Files**, choose an agent and press **Import Agent**. With the agent selected,
search for a clip and press **Load Clip**.

**Hold Weapon** puts the chosen weapon on the agent's weapon bone, so it follows
the clip. A clip made for the held weapon also moves its parts: slides, bolts,
magazines and knife blades. The clip list then shows only clips made for that kind of weapon: the
weapon's own clips first (a karambit's own draw), then the default ones it does
not replace, then the shared deaths, flinches and jumps. A rifle clip leaves a
knife floating where the rifle would be. Untick **Only clips for AK-47** (the
checkbox names the held weapon) to see every clip, including the first-person, chicken and egg clips, which are
made for other skeletons and do not play on an agent.

Some clips only hold the difference from another pose and the game plays them on
top of one. For those the game's own full version is loaded instead.

**Build Clip Thumbnails** in **Tools** loads every agent clip once in the background, checks it
and saves a small stick-figure preview of it. It takes about ten minutes, and
**Cancel** stops it.

**Browse Clips** opens every clip in the game by category, including the menu
poses, with a search field. Point at a clip to see its skeleton play; click it to
load it. Clips made for the first-person arms, the chicken and the egg are listed
greyed out, since they do not fit an agent. Press Esc or right-click to close it.

Importing an agent sets a scene still on Blender's default 24 fps to 30 fps, the
rate the game clips are authored at.

![SAS agent running with an AK-47](docs/agent-with-weapon.png)

### Finishes

With a weapon held, choose a **Finish** in the **Weapon** section, set **Weapon Wear** and **Weapon Seed**, and press
**Apply Finish**. The finish is built from the weapon's own textures, the
finish's pattern and colours and the game's wear and grime textures, so it wears
like it does in the game. It is made in the background the first time, so Blender
stays usable; a finish already in the cache is put on at once. The result is kept
in the cache and packed into the .blend. The line under the button says which
finish is on the weapon, with its wear and seed.

- **Weapon Wear** runs from 0 (factory new) to 1 (battle-scarred) and is kept inside
  the range the finish allows.
- **Weapon Seed** moves the pattern, wear and grime around using the same random
  sequence as the game, so pattern seeds (such as Case Hardened or Fade) match.
- Older finishes are laid out for the weapon's previous model, so applying one
  switches the weapon to that body.

1,384 of the game's 1,479 weapon and knife finishes can be applied; see
[Known issues](#known-issues) for render-time effects that are not reproduced.

![AK-47 with the Bloodsport finish](docs/finish.png)

![AK-47 Jungle Spray, battle-scarred](docs/finish-worn.png)

### Gloves

With an agent selected, open the **Gloves** section, choose a glove finish (for example Sport Gloves |
Vice or Driver Gloves | King Snake), set **Glove Wear** and **Glove Seed** and press **Apply
Gloves**. The agent's default gloves are hidden and the glove model goes on its
skeleton, so the hands follow every clip. **Remove Gloves** brings the defaults back.
The finish is built once per hand, so left and right differ the way they do in the game.
The line under the button says what the hands are wearing, and **Cancel** stops a finish being made.

The finish is built from the game's `csgo_customglove` shader inputs for both
generations of glove finishes, including detail and damage normals, wear and
left/right pattern flipping. All 94 glove finishes can be applied.

### Pose controls

**Add Pose Controls** adds, in Pose Mode:

- hand and foot IK controls with elbow and knee poles. Each limb has a 0 to 1
  switch: at 0 the game clip drives it, at 1 the control does. The **IK** and
  **FK** buttons switch a limb without moving it.
- a foot roll wheel behind each heel (turn it one way to rock back on the heel,
  the other to lift the heel off the ball of the foot) and a toe control.
- **Weapon Follows Right Hand** and **Left Hand Follows Weapon**, for posing the
  weapon by hand instead of from a clip.
- twist bones that follow the wrist and shoulder. The game turns these itself and
  its clips do not animate them, so without the controls forearms pinch when the
  hand turns.

Only the controls that currently drive a limb are shown, and the **IK** or **FK**
button for the current state is drawn pressed. Outside Pose Mode a **Switch to
Pose Mode** button appears. **Remove Pose Controls** takes
them off again and leaves the agent as imported.

![IK controls on an agent](docs/ik-controls.png)

### Maps

Choose a map and press **Add Map**. The first time, the map is converted and built
into its own .blend in the cache, which takes a few minutes and about 1 GB per
map; Blender stays usable meanwhile, and the **Cancel** next to the progress stops it.
After that it is added in seconds. With **Link to Map File** ticked the scene links to that
file and stays small; unticked, the map is copied into the scene. Built maps stay
in the cache across game updates; a map built from older game files still loads,
and its row offers **Rebuild** when newer game files would make a better one. A
map whose file is gone shows as a missing row; **X** takes it out.

Maps come with the game's real HDR `.exr` skybox, calibrated sun and interior lights, and baked indoor cubemap HDR probes (switchable per map via the sky/cubemap buttons and **Cubemap Probe** list). Names ending in `_vanity` are the small scenes
the game shows behind its menus.

![Dust II from above](docs/map-dust2.png)

### Studio and CS2 HDRIs

Open **Studio** to light any scene in one click:

- **CS2 HDRI:** pick from 21 in-game CS2 skies (`Dust II`, `Mirage`, `Inferno`, `Nuke`, `Ancient`, `Anubis`, `Vertigo`, `Rain Night`, etc.) without building a map, with live **HDRI Strength** and **HDRI Rotation** sliders that also drive loaded map skies and cubemap probes.
- **Add Studio:** adds 3-point area lighting (`Studio`, `Dark` or `Rim`), a toggleable 360° seamless backdrop, a 65 mm framed camera (**Frame Subject**), and a smooth **Turntable** orbit that leaves agent animation intact. Studio preserves any active CS2 HDRI or map sky.

### First-person arms and POV camera

In **Agent**, press **Import First-Person** to bring in the game's viewmodel arms with the chosen agent's sleeves and gloves plus a calibrated Source 2 POV camera. **Hold Weapon**, **Apply Finish**, **Apply Gloves**, **Stickers** and **Animation** (all 678 `viewmodel/` clips) work the same way as on third-person agents. Use the **POV Camera** section to switch between `Classic (68°)`, `Desktop (60°)` and custom FOV/offset settings, or press **Look Through POV Camera**.

### Stickers

With any weapon selected (held, first-person or standalone), open **Stickers**, pick a slot (1–5), search across all 11,918 weapon stickers and press **Apply Sticker**. Stickers are layered live in the shader on native weapon UVs so **Scrape**, **Offset X/Y**, **Scale** and **Rotation** update immediately and survive weapon finish swaps and save/reopen.

### Cache

Converted files live in `%LOCALAPPDATA%\cs2-rigs\cache`, in one folder per game
version and converter build. A game update starts a new folder; press **Read Game Files**
after one. Built maps are kept in `%LOCALAPPDATA%\cs2-rigs\maps` instead, so a
game update does not strand them or make them rebuild. The add-on preferences have
buttons to clear old folders or everything. Clearing old folders moves their built
maps to the maps folder first, and the open file follows them; clearing everything
deletes the maps too. A map whose file is gone shows in the Map section: choose that
map and press **Add Map** to link it to a new build, or press **X** to take it out. Converting everything takes about 6 GB.

### Updates

When Blender starts with online access allowed, CS2 Rigs asks GitHub once whether
a newer release is out; nothing else is sent. The top of the tab then shows
**Update Available!** or **CS2-Rigs is Updated**. Nothing is installed until you
press **Update Available!** and confirm; restart Blender afterwards. With online
access off, no check is made and the tab shows **Check for Updates** instead.

### Log

Each step is written to `cs2-rigs.log`, next to the cache by default. **Cache
Folder** and **Log Folder** in the preferences move them anywhere else, and **Open
Log Folder** in **Tools** shows the log. The same button sits under the last error
at the top of the tab.

## Compatibility

- Blender 5.3 or newer, Windows x64.
- Counter-Strike 2 installed through Steam. A game update can change the game
  files before the converter catches up; if imports start failing after one, report it.
- Source2Viewer-CLI 21.1 is pinned and checked against its published SHA-256 when
  downloaded. To use your own copy, set its path in the add-on preferences or the
  `CS2RIG_S2V_CLI` environment variable.

**Sharing .blend files.** Applied finishes are packed into the .blend, and a map
that is copied in (Link to Map File unticked) is stored in it. Sending such a file to someone
passes on Valve's assets with it; share renders, or files with linked maps and no finish.

## Known issues

This is an alpha. Not done yet:

- **Glove finishes:** the anisotropic gloss of a few newer silk finishes is not reproduced,
  and Blender's cloth sheen is a little softer than the game's.
- **Render-time effects** such as pearlescence, glitter and iridescence (and glitter/lenticular sticker effects).
- **Dual Berettas:** the left pistol does not follow the clip.
- **Weapon parts in clips** move only in clips made for the held weapon: a butterfly
  knife spins open in its own draw and an AK-47's magazine drops in its own reload, but
  a clip made for another weapon leaves the parts still. The parts have no controls.
- **Maps** have no baked lightmaps and cannot be cropped to an area yet.
- **Pose controls:** no torso or hip controls; twist bone weights are estimates; first-person rigs use game clips and FK rather than custom IK.
- **Long paths:** Blender cannot open files whose path is over 259 characters. With a
  long Windows user name or a deep **Cache Folder** a conversion fails before it starts,
  with an error saying how long the file's path would be; choose a short Cache Folder
  such as `C:\cs2-rigs`.

## Support

Contact **@Cravvnn on Discord**, or [report an issue](https://github.com/cravvnn/CS2-Rigs/issues/new/choose).
Include the CS2 Rigs version, what you chose and pressed, and `cs2-rigs.log`
(**Edit > Preferences > Add-ons > CS2 Rigs > Open Log Folder**).

The log writes your user folder as `~`, but it lists the agents, weapons and maps
you used; look it over before posting it publicly.

## Development

`cs2_rigs/core` has no Blender imports and is covered by `tests/` (which need
numpy for the compositor tests):

    .venv\Scripts\python -m pytest -q tests

`tools/smoke_blender.py` runs the whole path in a real Blender and renders a frame:

    blender --background --factory-startup --python tools/smoke_blender.py -- <out_dir>

`tools/rig_check.py`, `tools/weapon_check.py`, `tools/skin_check.py` and
`tools/map_check.py` run the same way and check the controls, weapon attachment,
finishes and maps. `tools/reopen_check.py` saves and reopens a file with a finish on.
`tools/glove_check.py` puts glove finishes on a CT and a T agent and renders each hand, and
`tools/glove_sweep.py` reads every glove finish and reports which apply.
`tools/panel_check.py` draws every section of the panel without a window, in each
state it can be in, to catch a wrong icon, property or operator name; with
`-- <out_dir>` it also writes `panel_states.json` for the HTML mock. `tools/sweep.py` imports every
agent and holds every weapon, and `tools/finish_sweep.py` reads every finish
of every weapon and applies one to each; both write a report of what fails.

Build the add-on zip with Blender:

    blender --command extension build --source-dir cs2_rigs --output-dir dist

## Credits

- Inspired by [deadlockrigs](https://github.com/Sirsyorrz/deadlockrigs).
- [Source 2 Viewer](https://github.com/ValveResourceFormat/ValveResourceFormat) (MIT), whose command-line converter reads the game files.
- [Valve](https://www.valvesoftware.com/), for Counter-Strike 2 and Source 2. Game assets are not included and remain Valve's.

## Licence

MIT. See [LICENSE](LICENSE).
