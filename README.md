# Mykelovve Attire Swapper

Windows attire swapping and a textured 3D model viewer for WWE 2K26.

## Download

Get the Windows portable ZIP from [Releases](../../releases/latest), extract it, and run the included EXE. Python is included. Viewing models does not require Blender.

Current release: **v0.3 Preview**.

## Choose your models

1. Select a complete character as **Base model**. It must contain `BaseModel` and an attire; its body, head, hair and beard are retained.
2. Select the clothing source as **Attire donor**. A full character or an attire-only Promo pack can be used.
3. Choose a fresh output folder and a compatible Windows `blender.exe`, then create and inspect the draft.

You can select an extracted pack, a `Characters` folder, a character root, or a `BaseModel`/attire subfolder. Nested wrapper folders are detected. If several characters or model files exist, select the intended one explicitly.

Keep `.mdl` and `.mcd` filenames intact. v0.3 recognizes both formats and validates the actual model header. Creating swaps targets WWE 2K26 model version47; port older-game files with Model Formatter first. Blender4.3.2 was tested.

## Viewer

Drag to rotate, right-drag to pan, and scroll to zoom. Ring, Entrance, Standing, Arms up, Flex and Open mouth use the original Formatter viewer engine. Base, donor and result previews are available from the sidebar.

## v0.3 loading fixes

- MCD and bare MDL recognition, including case-insensitive filenames.
- Nested pack and inner-folder discovery.
- Explicit choices for multiple characters or attire files.
- Attire-only donor support and a specific diagnostic when the selected base lacks its body files.
- A read-only preview adapter that preserves the source models and their texture paths.

The packaged runtime, Tk callbacks, a real MDL47 textured preview and a complete Blender worker job passed. The workflow preserved all185 source input files. Matching checks and file hashes are in `validation/v0.3.0.json` and the release assets.

Automatic swap results are drafts. Inspect the fit and test in game. Live cloth/hair physics and character-specific mesh or mask repairs are separate work; skeletal previews do not simulate YCL physics.

## Source and credits

`Source/` contains the application-layer source matching the portable build. Rebuilding also requires the original Formatter runtime and model add-on resources used by the package; those resources are not duplicated in this source tree.

The original Formatter theme and renderer are retained. 3D viewer attribution: **WWE2K_MDL / PWM Mods**. See [third-party notices](THIRD_PARTY_NOTICES.md).
