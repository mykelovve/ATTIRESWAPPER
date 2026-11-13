# Handoff from Claude — v0.5.0 released (2026-11-13)

The owner asked Claude to resume work, fix the clothing auto-fit, and publish it so the in-app updater delivers it. That is done. This file says what shipped, how it was built and checked, and what is left.

## Released
- [v0.5.0](https://github.com/mykelovve/ATTIRESWAPPER/releases/tag/v0.5.0) is the latest stable release.
  - Tag commit: `c43930636878559881ae1e0b293df0c2fdd8a8e9`.
  - `Mykelovve_Attire_Swapper_v0.5_Portable.zip`: 81,696,655 bytes, SHA-256 `8693cbd1c48306f26ac990e84537fda5164e7a212109a9babbc673db09777fe3`.
  - EXE SHA-256 `b69dde45917df2359fd9ccfacaaa7ebefdbaf28ce9fec6e78e5cf5dc7c5439a9`.
- Published through the existing **Publish portable release** workflow. The public download, GitHub asset digest and `SHA256SUMS.txt` agree, and the temporary `release-input/v0.5.0` branch was deleted.
- `Source/` on the tag matches the build. `main` after the tag adds only docs, `tools/` and `validation/v0.5.0.json`.

## What changed in v0.5 (Source)
- **`fit_helpers.py`:**
  - No vertex travels more than `max_displacement`. Vertices that would need more are tapered, or left alone if they are 2× the limit deep, along with the surface samples touching them.
  - Each garment is repaired partially instead of being rejected, and reports `fully_resolved`, `unresolved_inside_vertices`, `tapered_vertices` and `deep_interior_vertices`.
  - Interior items (≥80% inside, median depth beyond the limit) are left unchanged.
  - `strict=True` keeps the old all-or-nothing behaviour.
- **`garment_fit.py`:** shape-keyed garments are fitted, with every key offset like Basis. Cap handling is unchanged.
- **`swap_worker.py`:** fit statuses are now `applied`, `applied_partial` (with a log warning) or `interior_item_unchanged`.
- **`attire_swapper_gui.py`:** `APP_VERSION = "0.5"` only.
- **`tests/blender_fit_test.py`:** a synthetic regression test that needs no game files.

## How the EXE was built (repeatable without the private build machine)
`tools/repackage_exe.py` rebuilds a release EXE from the previous sealed release EXE plus git source. `tools/pyi_archive.py` reads and writes the PyInstaller 6 onefile archive and PYZ. Python 3.14 is required, to match the bundled `python314.dll`.

- It replaces only the `Source/*.py` modules that changed, both as bundled `assets\helpers\*.py` and as compiled PYZ modules.
- Every other entry (runtime, renderer, WWE2K_MDL add-on, `gmf_gui` entry script) stays byte-identical.
- It verifies every entry after writing.
- The method reproduces v0.4's compiled code objects exactly from v0.4 source.

Command used:
`py -3.14 -I tools/repackage_exe.py <repo> c439306... <v0.4 exe> Mykelovve_Attire_Swapper_v0.5.exe v0.4.0`

For v0.6, use the v0.5 EXE as the base and `v0.5.0` as the previous tag.

The EXE entry script `gmf_gui` (not in `Source/`) handles `--apply-update` (it calls `app_updater.apply_update`) and the Tk self-tests.

## Checks run (native Windows 11; details in `validation/v0.5.0.json`)
- **Build integrity:** all 1,191 archive entries verified.
- **`--self-test`:** passed with version 0.5 and 19 valid PNGs.
- **Full swaps** in Blender 4.4 using the add-on and worker extracted from the v0.5 EXE:
  - Swerve (same character) and Bailey (different donor pack) both succeeded.
  - Base models are byte-identical, and source folders have unchanged files and sizes.
  - Results are in the owner's `Documents/Mykelovve Attire Swaps/*-v05` folders.
- **Previews:** v0.5 previews of both results are valid, with no missing pictures.
- **Real in-app update v0.4 → v0.5:** the v0.4 installer helper replaced a running v0.4 copy, kept a verified backup, relaunched v0.5, and passed the health check with no rollback.
- **Live update check:** v0.4 sees v0.5.0, downloads and verifies it, and gets the exact tested EXE. v0.5 sees no update.
- **Regression test:** `tests/blender_fit_test.py` passed 12/12 on Blender 4.4 and 5.2.

## Visual finding (owner's Swerve screenshot)
In the same viewer, the **original unswapped Swerve mod** shows a bare belly, thigh gaps and a shin hole, exactly like the owner's v0.4 swap. That skin comes from the source mod, not from the swap.

The v0.5 swap covers the belly and shin, and most of the hips. Two upper-thigh patches and two small hem patches remain. These are the 134 pants and 51 top vertices 2–13 cm inside the body, which the 2 cm limit cannot reach.

The viewer states it reads hidden skin the way the game does. The result has not been checked in game.

## What is left (priority order)
1. **In-game check** of the `-v05` Swerve and Bailey swaps in WWE 2K26.
2. **Remaining deep penetration on tight garments.**
   - Option A: a per-garment larger limit with smooth falloff.
   - Option B: when base and donor are the same character, keep the mod's authored offset rather than forcing clearance.
   - Option C: write HPL hide-masks for body polygons fully covered by donor clothing. The exporter runs with `export_hpl=False`, and the worker strips `HPL_` groups from the base reference.
3. **Cross-character swaps:** the export rig is a copy of the base attire rig, so donor-only bones (coat tails, accessories) are missing. `validate_export_bundle` and `_copy_attachments` will stop the job. Not yet reproduced.
4. **Blender version:** `discover_blender()` picks the newest install (5.2 on the owner's PC). Prefer 4.3/4.4 and warn about untested versions.
5. **GUI:**
   - No Cancel button for a running swap.
   - The status keeps saying "draft created" after later failures.
   - Staging and temp folders accumulate.
6. **Speedball screenshots** (robe green in game but black in the preview; hair through the headband): preview shading and hair fit. Not addressed.
7. **Owner's PC:** a leftover hidden folder `.Swerve Strickland '26 - Promo Attire_AttireSwap_20261112-200410-fixedfit.staging-75439ead08` in `Documents/Mykelovve Attire Swaps`, from an interrupted test run, is safe to delete.

## Release policy
Unchanged; see `docs/CLAUDE_HANDOFF.md`. Ship every update with matching `Source/`, a tag on that source commit, the portable ZIP in GitHub Releases, an agreeing `SHA256SUMS.txt`, and a verified public download.

No game files, model assets or personal reports are included in this repository.
