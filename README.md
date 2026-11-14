# Mykelovve Attire Swapper

Windows attire swapping and a textured 3D model viewer for WWE 2K26.

## Download

Get the Windows portable ZIP from [Releases](../../releases/latest), extract it, and run the included EXE. Python is included. Viewing models does not require Blender.

Current release: **v0.5.6 Preview**. v0.4 and later install it from **Update app**. Install this ZIP once by hand if you have v0.3 or earlier; those versions cannot install updates themselves.

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

## v0.5.6 donor attachments on missing bones

- A swap no longer stops with "Donor attachments use missing base bones". Donor attachment points tied to bones the base does not have (usually the donor's own hair, for example `Hair_b04_06`) are left out and listed in the log; nothing is rebound to another bone. Everything in v0.5.5 is unchanged.

## v0.5.5 mask edges and swap controls

Masks now require coverage across every incident body triangle, using vertex, edge and interior probes. Authored masks are checked against the fitted clothing too, and old assignments are cleared first. This addresses holes outside narrow straps and uncovered head regions. Donor native-index masks require byte-identical BaseModel files; matching vertex counts alone is insufficient.

Transparent or unverified-opacity faces, mouthguards and fit-identified interior items do not hide skin. Claude's far-side limb guard and final visibility check remain in place. Coverage is sampled in the rest pose; recreate the swap after updating and inspect it in game.

**Cancel swap** stops the owned worker, including the automatic checker, while retaining logs, unfinished output and prior completed swaps. Status reflects the current action's actual success or failure. Blender discovery prefers4.4/4.3 over5.x. Publishing now requires sealed-runtime, original-theme UI and workflow checks on a Windows runner before stable release.

## v0.5.4 Check model, no donor beards

**Check model** (sidebar): **Check base**, **Check donor**, **Check result** and **Check any model** examine a model in Blender and list findings in plain words: hair and beard meshes, hair materials missing their texture links, the `.ycl` physics file and its cages, skin showing through the clothing, and hide-mask holes. A finished swap is checked automatically, and the check also confirms the base body and hair were not changed.

- A donor's beard (`M_Combinations` and other facial-hair meshes) is no longer added on top of the base face.
- A donor physics cage that the outgoing `.ycl` does not drive (it would show as a black band, for example around an arm) is left out.
- Every body hide-mask, including a donor's own, is double-checked so it never opens a hole; fabric on the far side of a thin limb no longer counts as covering skin.

## v0.5.3 fixes

- Bases whose attire rig has no hair bones (for example Carlito-slot ports) no longer stop with "Base attire rig has no J_Hair subtree". Their hair meshes are still kept untouched.
- A rounding error that could stop a swap with "a fitted vertex exceeded the travel limit" is fixed.
- Body hide-masks also cover skin that pokes out through clothing sitting deep inside the body, and close pinholes. The face is never masked this way.
- Every hidden skin point is double-checked from 49 viewing directions. If any direction can see it from outside (collar edges, wrap fringes, garment openings), it stays visible, so masks never open holes. Skin poking out through fabric stays hidden, because the fabric behind it shows instead.

## v0.5.2 no skin through clothing

Swaps now include body hide-masks (HPL), so the body no longer shows through the new clothing. When the donor pack's body matches the base, the donor attire's own masks are used; otherwise the masks are generated from what the donor clothing covers, separately for ring and entrance gear, and trimmed back one vertex at garment edges so no holes open. Only the new attire carries the masks: BaseModel files and the base hair are still shipped untouched.

## v0.5.1 base hair kept exactly

The base model's hair now ships completely untouched. Its geometry and textures were already kept; v0.5.1 also ships the base attire's own hair material files byte for byte. Earlier versions regenerated them without their flow and root (`*_flow`, `*_rai`) texture links, which made hair (for example the back of a mullet) missing or see-through in game. The base attire's `.ycl` hair physics file is carried over unchanged when the base attire has one; otherwise no physics file is added and the log says so.

## v0.5 clothing fit

Automatic fitting no longer rejects whole garments. No vertex moves more than the garment's limit (2 cm; 3.7 cm for jackets and tights). Vertices that would need more are tapered or left in place, and the rest of the garment is still repaired. The swap log names any garment that still has geometry inside the body, with its deepest remaining point, so you know what to inspect.

- Mouthguards and other items that sit inside the body by design are recognized and left unchanged.
- Garments with shape keys are fitted; every key receives the same offset, so key shapes are preserved.
- The body, head, hair and source files are still never changed.

On the tested Swerve swap, vertices inside the body dropped from 1064 to 134 (pants), 584 to 51 (top) and 44 to 2 (shoes). Some deep penetration can remain; swaps are still drafts to inspect in the viewer and in game. `tests/blender_fit_test.py` checks the fit without game files. See `validation/v0.5.0.json`.

## Retained v0.4 preview fixes

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

## Release policy

Every Swapper update is published here with its matching `Source/` files, a version tag pointing to that source commit, a downloadable portable ZIP in GitHub Releases, and `SHA256SUMS.txt`. A build is released only after the public download and its complete SHA256 hash are verified. The in-app updater reads the latest stable GitHub Release from this repository.

The manual **Publish portable release** workflow checks the sealed ZIP, its source hashes and the uploaded asset digests before publishing. Its temporary `release-input/<tag>` branch carries the prebuilt package without adding binaries to the main source history, and is removed after download verification. Original model/game assets and personal swap reports are not published in this source tree.

The original Formatter theme and renderer are retained. 3D viewer attribution: **WWE2K_MDL / PWM Mods**. See [third-party notices](THIRD_PARTY_NOTICES.md).
