# Handoff from Claude — bounded auto-fit (2026-11-13)

The owner asked Claude to resume work on the clothing auto-fit after the v0.4 handoff. This file says what was done, what was checked, and what is left.

## Where the work is
- Branch: `claude/auto-fit-bounded` (based on `main` at `4cc6b9d`, the v0.4.0 handoff commit).
- **Not merged into main and not released.** `main`'s `Source/` still matches the sealed v0.4.0 build, as the release policy requires. A v0.5 build must be produced from this branch using the private Formatter runtime, native SDK and model add-on, then released per the release policy in `docs/CLAUDE_HANDOFF.md`.
- Changed: `Source/fit_helpers.py`, `Source/garment_fit.py`, `Source/swap_worker.py`. Added: `tests/blender_fit_test.py`.
- The swap worker, folder discovery, GUI, updater and preview code are otherwise unchanged.

## The problem (from the owner's real v0.3/v0.4 swaps)
Auto-fit rejected 8 of 19 garments, including the large ones: Swerve pants, top and shoes, plus Bailey's kickpads, both kneepads and mouthguard. Rejected garments were exported unfitted, so body skin showed through the pants and top in the 3D preview. There were two causes:
1. **Runaway surface pass.** The edge/face correction pass pushed vertices that started outside the body 20–106 cm.
2. **All-or-nothing limit.** A few stray vertices (hidden caps or tucks 5–11 cm inside) vetoed the whole garment.

Garments with shape keys were also refused outright (Swerve `M_U_Acce`), and the mouthguard (inside the head by design) was treated as a failure.

## What changed
- **Bounded travel.** No vertex moves more than `max_displacement` (2 cm default; 3.7 cm for jackets and tights).
  - Vertices that need more are tapered (moved a shrinking amount).
  - Vertices deeper than 2× the limit are left alone, along with any surface samples touching them.
- **Partial fits.** The rest of each garment is repaired, and each garment reports `fully_resolved`, `unresolved_inside_vertices`, `tapered_vertices` and `deep_interior_vertices`. The worker status is `applied`, `applied_partial` (with a warning naming the garment and its deepest remaining penetration) or `interior_item_unchanged`. `strict=True` restores the old all-or-nothing behaviour.
- **Interior items.** A garment that is ≥80% inside the body, with median depth beyond the limit, is reported as an interior item and left unchanged (for example the mouthguard).
- **Shape keys.** Each fitted vertex's offset is applied to every shape key, so key shapes are unchanged relative to Basis.
- **Unchanged guarantees.** Proposals are still computed before writing, the body is still verified unchanged, and the cap handling in `garment_fit` is kept.

## Checks run
- **Real drafts (owner's machine, Blender 5.2).** The worker's `_fit` was run in memory on the four saved drafts in `Documents/Mykelovve Attire Swaps`. Nothing was saved and the body was verified unchanged. Vertices inside the body, before → after:
  - Swerve pants: 1064 → 134
  - Swerve top: 584 → 51
  - Swerve shoes: 44 → 2
  - Swerve `M_U_Acce` (shape keys): 24 → 0, with key drift 0
  - Bailey kickpads: 50 → 3
  - Bailey kneepads: 3 → 3 (only 9–10 vertices needed moving)
  - Mouthguard: now `interior_item_unchanged`
- **Regression test.** `tests/blender_fit_test.py` passes all 12 checks on Blender 4.4 and 5.2 and fails on the v0.4 code. Run it with:
  `blender --background --factory-startup --python tests/blender_fit_test.py -- Source`

## Not verified
- **Full worker export.** The complete Blender export (MCD write, materials, textures) was not rerun with the new code; only `_fit` was exercised. Run one full swap on Blender 4.3/4.4 before releasing.
- **In-game result.** Not tested in WWE 2K26.

## What is left (in priority order)
1. **Build, verify and release v0.5** from this branch per the release policy. Run one full swap first and inspect the result in the 3D viewer and in game.
2. **Remaining skin show-through.**
   - The Swerve pants and top still have 134 and 51 vertices inside, 2–13 cm deep: geometry a 2 cm limit cannot reach.
   - The attire's HPL (body hide-mask) blocks: the output MCD keeps the same number of `HPL!` blocks as the source, but whether their contents still hide the right body polygons was not checked. If the Formatter preview does not apply HPL at all, the preview will show skin that the game hides, so check in game before chasing this further.
   - When base and donor are the same character, the clothes were authored against that same body. Consider fitting only penetration that is new relative to the donor's own body, rather than absolute penetration.
3. **Cross-character swaps.** The export rig is a copy of the base attire rig, so bones that exist only in the donor (coat tails, accessories) are missing, and `validate_export_bundle` / `_copy_attachments` will stop the job. Not yet reproduced; both test swaps were same-character.
4. **Blender version.** `discover_blender()` picks the newest install. On the owner's machine that is 5.2, although the UI says 4.3/4.4 and 4.4 is installed. Prefer a tested version and warn otherwise.
5. **Missing installer entry.** The updater launches the EXE with `--apply-update`, but nothing in `Source/` handles that argument. The build record lists a `gmf_gui` entry script outside `Source/`; publish it or document it.
6. **GUI.**
   - No Cancel button for a running swap.
   - The status keeps saying "draft created" after later failures.
   - Staging and temp folders accumulate.
7. **Owner screenshots not addressed.** Speedball's robe shows green in game but black in the preview, and hair pokes through the headband. These are preview shading and hair fit; `START_HERE.txt` already lists them as not established fixes.

No game files, model assets or personal reports are included in this repository.
