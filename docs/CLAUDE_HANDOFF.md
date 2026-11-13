# Claude handoff — Attire Swapper v0.4

> **Superseded 2026-11-13:** the owner resumed work and v0.5.4 is now released. Read `docs/HANDOFF_TO_CHATGPT.md` for the current state; the release policy below still applies.

## Current instruction
Further model repairs and new feature work are paused at the owner's request. Complete release/documentation bookkeeping only. Resume other work only when the owner asks.

## Verified release
- Repository: https://github.com/mykelovve/ATTIRESWAPPER
- Stable release: [v0.4.0](https://github.com/mykelovve/ATTIRESWAPPER/releases/tag/v0.4.0)
- [Portable Windows ZIP](https://github.com/mykelovve/ATTIRESWAPPER/releases/download/v0.4.0/Mykelovve_Attire_Swapper_v0.4_Portable.zip)
- ZIP size: 81,887,047 bytes
- SHA256: `68d1e9a4732e4aa05fe3f276d0b5811adfef2c383608363e26bc9c672c631cac`
- Matching source/tag commit: `f6f12b5ddbec0db15a0fa739aa0324c6be11601d`

The public ZIP download, GitHub asset digest and SHA256SUMS.txt agree. Source/ contains the nine application modules matching the sealed portable build. Subsequent documentation commits do not change the v0.4.0 tag or binary.

Users on v0.3 or earlier must manually extract and run v0.4 once. Future Update app clicks check GitHub, download and verify the update, then install/restart inside the app.

## Implemented application behavior
- Nested character-folder and supported MCD/MDL47 discovery, complete base characters and attire-only donors.
- Original Formatter UI/theme/sidebar and interactive viewer controls retained.
- In-app GitHub updater with pinned repository/hosts, version checks, SHA256 validation, bounded EXE-only ZIP staging, helper readiness, Windows image-lock retry, verified backup, atomic replacement and startup-health rollback.
- Preview-only unique local material/diffuse DDS recovery when renamed folders break stored paths. Valid bindings stay authoritative; ambiguous matches remain errors. This fallback does not rewrite game files.

## Validation and limits
The frozen build passed 22 updater cases, public GitHub download/staging, actual helper install/relaunch/health checks, and both Tk Update button callbacks with zero browser launches. Native MCD/MDL previews each embedded 19 valid PNGs with source-matching body/head images. Original renderer/theme resources were preserved.

Windows execution checks ran under Wine. See validation/v0.4.0.json for precise scope. Automatic swap outputs remain drafts; skeletal previews do not validate every game animation or live cloth/hair simulation.

## Permanent release policy
Every Swapper application update must go to this repository with:
1. Matching Source/ files.
2. A version tag pointing to that source commit.
3. The portable ZIP in GitHub Releases.
4. SHA256SUMS.txt agreeing with the package and GitHub asset digest.
5. Verification of the complete actual public download before calling it released.

Keep the filename convention `Mykelovve_Attire_Swapper_vVERSION_Portable.zip`. The updater reads the latest stable release from this repository.

The Publish portable release workflow validates a prebuilt sealed package, source binding and uploaded digests. Temporary release-input branches are cleaned after verification; binaries are not added to main history.

## Build prerequisites and private assets
Source/ is the application layer. Rebuilding also requires the original Formatter runtime, native SDK and model add-on resources, which are not duplicated in this public repository.

Model/game files, personal reports and private research checkpoints stay outside this repository. No model-specific instructions or private assets are included in this public handoff. Ask the owner for the required inputs if future authorized work depends on them.
