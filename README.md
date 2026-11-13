# Mykelovve Attire Swapper

Windows attire swapping and a textured 3D model viewer for WWE 2K26.

## Download

Get the Windows portable ZIP from [Releases](../../releases/latest), extract it, and run the included EXE. Python is included. Viewing models does not require Blender.

Current release: **v0.4 Preview**. Install this ZIP once if you have v0.3 or earlier; those versions cannot install the new updater themselves.

## Updates

Both **Update app** buttons now check this repository, download and verify the latest stable portable ZIP, then install and restart inside the app. Keep the EXE in a writable folder. The installer waits for running model work, preserves preferences and model folders, and keeps a previous-EXE backup with rollback if startup fails. No website or separate Python is needed.

## Choose your models

1. Select a complete character as **Base model**. It must contain `BaseModel` and an attire; its body, head, hair and beard are retained.
2. Select the clothing source as **Attire donor**. A full character or an attire-only Promo pack can be used.
3. Choose a fresh output folder and a compatible Windows `blender.exe`, then create and inspect the draft.

You can select an extracted pack, a `Characters` folder, a character root, or a `BaseModel`/attire subfolder. Nested wrapper folders are detected. If several characters or model files exist, select the intended one explicitly.

Keep `.mdl` and `.mcd` filenames intact. v0.3 recognizes both formats and validates the actual model header. Creating swaps targets WWE 2K26 model version47; port older-game files with Model Formatter first. Blender4.3.2 was tested.

## Viewer

Drag to rotate, right-drag to pan, and scroll to zoom. Ring, Entrance, Standing, Arms up, Flex and Open mouth use the original Formatter viewer engine. Base, donor and result previews are available from the sidebar.

## v0.4 preview fixes

When a renamed character folder breaks stored material paths, the viewer resolves unique local material and color DDS files. This restores body and head images for the tested packs while preserving the source game files. Missing or ambiguous images are reported. Preview shading can still differ from the game.

The frozen application passed both Update button callbacks without opening a browser, 22 updater transaction and rejection cases, an actual public release download, and installer/relaunch/health checks. MCD and MDL previews each embedded 19 PNGs; body and head images matched their source DDS files. The original 503 renderer/theme modules and the v0.3 model loader and swap worker are preserved. See `validation/v0.4.0.json` for evidence and testing limits. Windows runtime checks ran under Wine; native Windows visual testing was not repeated.

## Retained v0.3 loading fixes

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
