# GoldSrc Model Toolchain

Public Blender 5.2 Extension and deterministic host tools for creating and validating GoldSrc MDL v10 models on Windows x64.

The Extension provides GoldSrc SMD/QC import and export, indexed BMP conversion, Sven StudioMDL compilation, independent MDL v10 inspection and readback, rigid-body-to-bone baking helpers, and a background-only five-stage API. It registers no panel or menu. The official [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) remains an external dependency and is never bundled or modified.

Version `1.2.0` adds contract-driven `$renamebone`, Half-Life/Sven special-texture rules, player/NPC baseline inspection, and player portrait validation. Multi-source blend sequence authoring remains an explicit limitation.

## Install

Download `goldsrc_model_toolchain-1.2.0-windows-x64.zip` and its checksum from the [v1.2.0 release](https://github.com/XiangXtreme/goldsrc-model-toolchain/releases/tag/v1.2.0), verify SHA-256, then install the ZIP as a Blender Extension in the `user_default` repository.

Requirements:

- Blender `5.2.x` LTS on Windows x64
- CPython 3.13 as bundled with Blender 5.2
- Official Blender MCP only when an external agent needs live Blender control

## Runtime API

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
api.capabilities()
api.execute_stage("PREFLIGHT", contract_path, artifacts_dir)
```

The background operator is:

```python
bpy.ops.goldsrc_toolchain.execute_stage(
    stage="EXPORT",
    contract_path=contract_path,
    artifacts_dir=artifacts_dir,
    report_path="export_report.json",
)
```

Supported stages are `PREFLIGHT`, `EXPORT`, `COMPILE`, `INSPECT`, and `ROUNDTRIP`. Contract version 2 and existing report phase names remain stable.

## Development

```powershell
python -m unittest discover -s scripts/tests -v
python scripts/audit_repository.py
python scripts/build_extension.py --output <artifact-dir>/goldsrc_model_toolchain-1.2.0-windows-x64.zip
python scripts/audit_release_archives.py <artifact-dir>/goldsrc_model_toolchain-1.2.0-windows-x64.zip
```

Generated assets and test artifacts must remain outside this repository.

## Licensing And Provenance

Project code is GPL-2.0-or-later. SourceIO-derived code retains its MIT notice, Pillow retains HPND terms, and Blender Source Tools-derived SMD behavior retains GPL attribution. The bundled `studiomdl.exe` is the recorded Sven Co-op SDK modelling tool snapshot; component notices and hashes are under `extension/goldsrc_model_toolchain/licenses/` and `tool-manifest.json`.
