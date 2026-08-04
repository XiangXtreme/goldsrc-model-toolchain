# Environment And Release

## Supported Runtime

- Blender `5.2.x` LTS, Windows x64.
- Extension ID `goldsrc_model_toolchain`, release `1.4.1`, API version `1`.
- Official `ahujasid/blender-mcp` is external. Environment checks may inspect it but never install, overwrite, downgrade, or update it.
- Sven StudioMDL, Pillow `12.3.0`, and the SourceIO-derived GoldSrc reader are bundled in the Extension ZIP.

## Repository Boundary

Treat the repository and installed Extension as read-only during model production. Write Blend files, contracts, SMD, QC, BMP, MDL, reports, renders, caches, ZIPs, and extraction roots to an explicit external artifact directory. The path guard also rejects writes anywhere under a directory containing `SKILL.md`.

## Build And Install

```powershell
python scripts/check_environment.py
python scripts/build_extension.py --output <artifact-dir>/goldsrc_model_toolchain-1.4.1-windows-x64.zip
python scripts/bootstrap_environment.py --apply
```

`bootstrap_environment.py` builds and installs only this Extension. It may disable legacy GoldSrc add-ons but does not manage Blender MCP. Use `GOLDSRC_BLENDER` to select Blender and `GOLDSRC_SVEN_STUDIOMDL` only to override the bundled compiler intentionally.

For a clean regression, install the built ZIP into a temporary Blender 5.2 configuration, enable `bl_ext.user_default.goldsrc_model_toolchain`, then run `scripts/extension_smoke_test.py`.

## Public Release

Build `goldsrc_model_toolchain-1.4.1-windows-x64.zip`, emit its `.sha256`, audit extraction through `scripts/audit_release_archives.py`, and upload both files to GitHub Release `v1.4.1`. Release archives must contain no cache, local metadata, Blender MCP files, Source 1/2 assets, BSP, VTF/VMT, DMX/VTA, historical scenes, or runtime artifacts.
