# GoldSrc Model Toolchain Workspace

Unified source workspace for the `build-goldsrc-models` Skill and its public Blender 5.2 Extension. The plugin and Skill remain separate runtime packages but share one compatibility contract, test suite, and local installation workflow.

The current source, local sync installation, and public compatibility baseline are `1.4.0 / API 1`. Reproducible public installation is pinned to GitHub Release `v1.4.0`.

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

Release `v1.4.0` productizes selected-static export as one high-level call while retaining the five-stage validation boundary. It adds compact persisted reports, isolated strict roundtrips, unified visual comparison, sparse 2K texture tiling, explicit triangle summaries, and source-evaluated to prepared to logical-SMD material-distribution auditing. It also retains labeled `3x2` contact sheets and decoded-pixel hashes for deterministic readback evidence. Multi-source blend sequence authoring remains an explicit limitation.

The release supports logical large textures and over-budget reference SMDs. A logical `2048x2048` atlas defines sixteen possible `512x512` tiles; EXPORT clips and retriangulates triangles crossing tile boundaries, remaps local UVs, and export-plan version 2 converts/compiles only geometry- or skin-referenced tiles. GoldSrc does not receive a `2048x2048` texture entry, and hidden anchor geometry is unnecessary. Reference SMDs are split by compiled `(bone, position)`, `(bone, normal)`, and triangle budgets into multiple `$body` entries. Bodygroup choices are rejected when they need splitting. Simple line slicers such as `smdcutpy.py` are not a substitute for this path.

For author-baked procedural/PBR colors, a contract can declare `texture_bake.uv_layer` and require the same UV layer to be both Blender's active and active-render layer. PREFLIGHT reports an undeclared mismatch and fails an explicit mismatch before EXPORT can produce a misleading texture mapping. Low-level EXPORT still does not bake node graphs or infer material intent. The high-level static API can execute an explicitly selected `color_only` or `unlit_color` bake on copied materials, preserving supported Alpha graphs separately and returning `needs_decision` for unsupported closures or node groups.

## Install

Download `goldsrc_model_toolchain-1.4.0-windows-x64.zip` and its checksum from the [v1.4.0 release](https://github.com/XiangXtreme/goldsrc-model-toolchain/releases/tag/v1.4.0), verify SHA-256, then install the ZIP as a Blender Extension in the `user_default` repository.

Requirements:

- Blender `5.2.x` LTS on Windows x64
- CPython 3.13 as bundled with Blender 5.2
- Official Blender MCP only when an external agent needs live Blender control

## Runtime API

Release `1.4.0` provides one compact product entry for an ordinary selected static mesh while keeping API version 1 and the old three-argument `execute_stage()` full-result default:

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
result = api.export_selected_static(
    artifacts_dir=artifacts_dir,
    model_name="branched_cave_2k.mdl",
    request="Export the selected object as MDL with a 2K texture and no baked lighting.",
    texture_size=2048,
    uv_strategy="smart_project",
    origin_strategy="source_origin",
    bake_mode="unlit_color",
    assurance="strict",
    preserve_author_session=True,
    visual_compare=True,
    delivery_dir=delivery_dir,
)
```

The call internally performs read-only analysis, non-destructive preparation, and all five validation stages. It returns only delivery facts, including explicit author, crossed-tile, and post-tile triangle counts; full evidence is persisted under `reports/`, including `static_export.json` and `pipeline.json`. Missing UV, origin, bake, or transparency semantics return `needs_decision` before Scene mutation. The prepared material keeps its logical PNG; logical BMP tokens and generated physical tile BMPs remain separate compiler concerns.

The layered `analyze_selected_static()`, `prepare_static_export()`, `create_static_contract_from_scene()`, and `execute_pipeline()` methods remain available for diagnosis, recovery, and already prepared assets. They are not the default route for a routine selected-object delivery.

The lower-level API remains available:

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

## Repository Layout

- `plugin/goldsrc_model_toolchain/` is the only Blender Extension payload. Blender Extensions use Blender's Python API, so runtime `.py` modules such as `blender/static_export.py` are expected source files, not generated scripts. The static module is asset-agnostic and contains no cave- or fixture-specific behavior.
- `skill/build-goldsrc-models/` is the only Codex Skill source.
- `scripts/` contains workspace validation, build/install commands, compatibility CLIs, and regression fixtures. It is never copied into the Extension ZIP or imported by the installed runtime. See `scripts/README.md`.
- `workspace-manifest.json` binds the current `1.4.0 / API 1` components to the public `v1.4.0` compatibility pin.

## Development Commands

```powershell
python -m unittest discover -s scripts/tests -v
python scripts/audit_repository.py
python scripts/build_extension.py --output <artifact-dir>/goldsrc_model_toolchain-1.4.0-windows-x64.zip
```

Generated assets and test artifacts must remain outside this repository.

## Licensing And Provenance

Project code is GPL-2.0-or-later. SourceIO-derived code retains its MIT notice, Pillow retains HPND terms, and Blender Source Tools-derived SMD behavior retains GPL attribution. The bundled `studiomdl.exe` is the recorded Sven Co-op SDK modelling tool snapshot; component notices and hashes are under `plugin/goldsrc_model_toolchain/licenses/` and `tool-manifest.json`.
