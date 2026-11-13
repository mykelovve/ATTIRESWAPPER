# Handoff from Claude — v0.5.1 released (2026-11-13)

The owner asked Claude to resume work, fix the clothing auto-fit, fix missing hair, and publish both so the in-app updater delivers them. That is done. This file says what shipped, how it was built and checked, and what is left.

## Owner rule for porting (do not break)
The base model's hair stays **exactly** the same: geometry, textures and material files. Do not touch it. The only thing carried over for it is its physics: the base attire's `.ycl`, unchanged.

## Latest: v0.5.1 (base hair untouched)
- [v0.5.1](https://github.com/mykelovve/ATTIRESWAPPER/releases/tag/v0.5.1) is the latest stable release.
  - Tag commit: `e2fd482103eb39aaae9c5a6f2f2dfc55ed527a40`.
  - ZIP SHA-256 `c19f40949cb61e7aa3d400fe1816020409fcb228d02010ca5b2e3aafe5730edb`, 81,701,851 bytes.
  - EXE SHA-256 `ea4cac878a253287c1755589cceb320bb17a40f5f6c752431dae911acc244053`.
  - Built with `tools/repackage_exe.py` from the v0.5.0 EXE, using `v0.5.0` as the previous tag.
- **Bug fixed.** The exporter regenerated preserved hair `.jmtl` files without their `*_flow`/`*_rai` texture links, so hair went missing or see-through in game. That covers Speedball/Bailey's back hair (`M_Hair`, `hair_ALPHA`) and Swerve's hair. Present in v0.3–v0.5.
  - The viewer did not show the bug because it reads DDS files by name.
- **Fix** (`_restore_base_hair_materials` in `swap_worker.py`): for every base-hair material, ship the base attire's own `.jmtl` byte for byte. It is used only when its shader matches and every texture it references is shipped. The references stay valid because the output keeps the base character and attire folder names.
- **`.ycl`:** copied unchanged from the base attire when it exists. The base Bailey (United Empire) attire has none, so none is added and the log says so. When the donor's clothing differs from the base attire's, the copied `.ycl` also describes the base attire's clothing cages, so check clothing physics in game.
- **Checked on native Windows:**
  - Self-test reports 0.5.1.
  - Swerve and Bailey swaps: every hair `.jmtl` identical to the base's, hair geometry change 0.0, Swerve `.ycl` byte-identical.
  - The bundled worker equals the tested worker.
  - A real v0.5 → v0.5.1 in-app update passed its health check.
  - Live: v0.4 and v0.5 both see 0.5.1, and the public download equals the tested EXE.
  - The owner's test swaps are in `Documents/Mykelovve Attire Swaps/*-v051`.

## Speedball (Mike Bailey): use the GRANDSLAM base
- **Which base works.** The owner's working Speedball port, with hair and body fine in game, is `discovery\DISCOVERY CHARACTERS UPDATEDE 2\Characters\1169_Mike_Bailey_GRANDSLAM`.
  - Its `Attire` ships `Attire.ycl` (cages `S_Hair`, `S_Jacket`), `Attire.hpl` and `Attire.mtls`.
  - `discovery\Swervetest\...\1169_Mike_Bailey_United_Empire` has the same `baseModel.mcd` and identical hair meshes, but no `.ycl` or `.hpl`. It looks like an earlier swap output. Do not use it as a base.
- **Swap run with GRANDSLAM as base** (donor `mike_bailey'26 By ED\Alt_Attire`, v0.5.1 worker, Blender 4.4): succeeded.
  - Base hair untouched: 4 `.jmtl` files byte-identical, geometry change 0.0.
  - GRANDSLAM `Attire.ycl` copied unchanged.
  - Output: `Documents\Mykelovve Attire Swaps\1169_Mike_Bailey_GRANDSLAM_AttireSwap_20261112-215152-v051`.
- **Open risk.** The copied `.ycl` also holds physics for GRANDSLAM's own `S_Jacket` cage (382 vertices). The donor clothing has its own, different `S_Jacket` (152 vertices). If the jacket glitches in game, the `.ycl` needs only its `S_Hair` section kept, or the donor cage needs its own physics. That needs YCL format work; the add-on has no YCL writer.
  - The `.ycl` lists cage names (`S_*`), collider joints (`J_*_Col*`) and hair bone chains (`Hair_b..`).
- **HPL not carried over.** `Attire.hpl` (the body hide-mask) belongs to GRANDSLAM's clothing, so the swap does not copy it.

## Released (v0.5.0)
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
1. **In-game check** of the `-v051` Swerve and Bailey swaps in WWE 2K26: hair, including the back of Speedball's hair, plus hair and clothing physics.
   - If Speedball's back hair still misbehaves in game, the likely cause is physics. His base attire ships no `.ycl`, so ask the owner where his working hair physics comes from. Never alter the base hair to compensate.
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
