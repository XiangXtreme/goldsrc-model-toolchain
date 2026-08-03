# GoldSrc Model Toolchain Workspace

Unified source workspace for the `build-goldsrc-models` Skill and its public Blender 5.2 Extension. The plugin and Skill remain separate runtime packages but share one compatibility contract, test suite, and local installation workflow.

The public compatibility baseline is `v1.3.3`. The `main` branch may contain unreleased `1.4.0-dev` changes; use a tagged GitHub Release for reproducible installation and do not treat `main` as a release artifact.

## Workspace Layout

- `skill/build-goldsrc-models/`: Codex Skill source, references, and its release-pinned installer.
- `plugin/goldsrc_model_toolchain/`: Blender Extension source and bundled GoldSrc runtime components.
- `scripts/validate_workspace.py`: checks source layout and Skill/Extension compatibility.
- `scripts/sync_install.py`: installs the local Skill and builds/installs the local Blender Extension.

## Local Development

```powershell
python scripts/validate_workspace.py
python scripts/sync_install.py --all --dry-run
python scripts/sync_install.py --all
```

Use `--skill` or `--plugin` to deploy one component. Skill synchronization updates only files managed by this workspace. Plugin synchronization validates and builds the Extension ZIP before installing it into Blender's `user_default` repository.

The Extension provides GoldSrc SMD/QC import and export, indexed BMP conversion, Sven StudioMDL compilation, independent MDL v10 inspection and readback, rigid-body-to-bone baking helpers, and a background-only five-stage API. It registers no panel or menu. The official [ahujasid/blender-mcp](https://github.com/ahujasid/blender-mcp) remains an external dependency and is never bundled or modified.

Release `v1.3.3` adds labeled `3x2` contact sheets for five-point Action readback while preserving every original still and its hash. The same Blender-independent compositor is available through the runtime API for author/readback comparisons and physics-event evidence. The development branch also reports evaluated UV/material facts and propagates failed preflight evidence as `status: fail`. Multi-source blend sequence authoring remains an explicit limitation.

The current source also supports logical large textures and over-budget reference SMDs. A logical `2048x2048` atlas is exported as sixteen `512x512` indexed BMPs inside one MDL; EXPORT clips and retriangulates triangles crossing tile boundaries, then remaps each triangle to local tile UVs. GoldSrc does not receive a `2048x2048` texture entry. Reference SMDs are split by compiled `(bone, position)`, `(bone, normal)`, and triangle budgets into multiple `$body` entries. Bodygroup choices are rejected when they need splitting. Simple line slicers such as `smdcutpy.py` are not a substitute for this path.

For author-baked procedural/PBR colors, a contract can declare `texture_bake.uv_layer` and require the same UV layer to be both Blender's active and active-render layer. PREFLIGHT reports an undeclared mismatch and fails an explicit mismatch before EXPORT can produce a misleading texture mapping. The Extension does not bake node graphs or certify lighting provenance: use the authoring workflow's temporary Emission/`EMIT` bake when the source atlas must contain no material lighting or shadow contribution, then give EXPORT the saved image-backed result.

## Install

Download `goldsrc_model_toolchain-1.3.3-windows-x64.zip` and its checksum from the [v1.3.3 release](https://github.com/XiangXtreme/goldsrc-model-toolchain/releases/tag/v1.3.3), verify SHA-256, then install the ZIP as a Blender Extension in the `user_default` repository.

Requirements:

- Blender `5.2.x` LTS on Windows x64
- CPython 3.13 as bundled with Blender 5.2
- Official Blender MCP only when an external agent needs live Blender control

## Runtime API

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
api.capabilities()
api.execute_stage("PREFLIGHT", contract_path, artifacts_dir)
api.import_smd_animation(animation_smd, reference_smd=reference_smd)
api.decompile_mdl(mdl_path, artifacts_dir)
api.create_visual_contact_sheet(items, destination, columns=3, title="Readback")
api.tile_large_texture(smd_path, image_path, output_dir, width=2048, height=2048)
api.split_smd_for_goldsrc(smd_path, output_dir)
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
python scripts/build_extension.py --output <artifact-dir>/goldsrc_model_toolchain-1.3.3-windows-x64.zip
python scripts/audit_release_archives.py <artifact-dir>/goldsrc_model_toolchain-1.3.3-windows-x64.zip
```

Generated assets and test artifacts must remain outside this repository.

## Licensing And Provenance

Project code is GPL-2.0-or-later. SourceIO-derived code retains its MIT notice, Pillow retains HPND terms, and Blender Source Tools-derived SMD behavior retains GPL attribution. The bundled `studiomdl.exe` is the recorded Sven Co-op SDK modelling tool snapshot; component notices and hashes are under `plugin/goldsrc_model_toolchain/licenses/` and `tool-manifest.json`.
