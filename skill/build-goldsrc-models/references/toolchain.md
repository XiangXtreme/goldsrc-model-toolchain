# External GoldSrc Model Toolchain

## Release Boundary

The Blender Extension is maintained publicly at <https://github.com/XiangXtreme/goldsrc-model-toolchain>. This Skill does not contain Extension source, StudioMDL, Pillow, SourceIO code, pipeline runners, fixtures, or release tooling.

The pinned validated release is:

- Extension `goldsrc_model_toolchain` `1.3.3`
- API version `1`
- GitHub tag `v1.3.3`
- Blender `5.2.x`, Windows x64
- Asset and SHA-256 recorded in `scripts/toolchain-release.json`

The development source may include capabilities that are not present in the pinned archive until a public release is cut. For large-texture or SMD-budget work, require `capabilities()["features"]["large_texture_tiling"]` and `capabilities()["features"]["smd_budget_split"]`; do not infer support from the version string alone.

Run `python scripts/install_toolchain.py` to inspect the installed runtime. Add `--apply` only when the Extension is missing, older, or reports another API. A newer API-1 version is accepted with a compatibility warning and is never downgraded automatically.

The installer downloads only the fixed GitHub Release asset, checks its pinned SHA-256, and invokes Blender's Extension installer. It never writes Blender MCP files, Codex configuration, scene assets, or Skill files.

## Runtime Surface

Access the API through live official Blender MCP:

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
capabilities = api.capabilities()
```

Public stages:

```text
PREFLIGHT -> EXPORT -> COMPILE -> INSPECT -> ROUNDTRIP
```

Execute one stage through the runtime API:

```python
result = api.execute_stage(stage, contract_path, artifacts_dir)
```

Or use the background-only operator and write a report inside the artifact directory:

```python
bpy.ops.goldsrc_toolchain.execute_stage(
    stage=stage,
    contract_path=contract_path,
    artifacts_dir=artifacts_dir,
    report_path=report_name,
)
```

The API also provides SMD/MDL import, indexed texture conversion, MDL inspection, rigid-body world configuration, adaptive baking, event-chain evaluation, transform transfer/audits, and asset namespace helpers. Version `1.3.0` added:

```python
api.import_smd_animation(animation_smd, reference_smd=reference_smd)
api.import_smd_animation(animation_smd, target_armature=armature_name)
api.decompile_mdl(mdl_path, artifacts_dir)
api.tile_large_texture(smd_path, image_path, output_dir, width=2048, height=2048)
api.split_smd_for_goldsrc(smd_path, output_dir)
```

Animation import requires an explicit reference rest or target armature and reports five-point global matrix error. MDL decompilation writes reference/animation SMDs, exact indexed BMP data, QC, and `decompile_manifest.json`; it rejects external sequence groups rather than returning a silently incomplete result. The existing `validate_model_compatibility(candidate_mdl, baseline_mdl, role)` and `validate_player_portrait(path, remapped=False)` remain available. Call all methods through the runtime object; do not import `bl_ext.*` internals.

Version `1.3.1` compares source animation frames in SMD declaration order, measures compiled rotation with local matrices, validates loop seam endpoints before StudioMDL, reports preflight object dimensions/bounds, frames readback from the model's thinnest axis with scale-aware clipping, and rejects an animated readback whose previews all contain zero foreground pixels.

Version `1.3.2` routes file-backed images through Pillow median-cut quantization from the saved source file, uses explicit color-space and row-origin semantics for Blender buffers, reserves index 255 only for Masked textures, and records source/output color counts, mean and maximum channel error, plus direct-versus-flipped orientation evidence in `EXPORT`.

Version `1.3.3` keeps the original five-point readback PNGs and adds one labeled `3x2` contact sheet plus JSON layout per Action. The layout records source hashes, sizes, labels, frame details, and image/caption rectangles. Runtime callers can create author/readback comparisons or physics-event sheets with:

```python
api.create_visual_contact_sheet(
    items, destination, columns=3, title="Readback or event overview"
)
```

Each item supplies `path`, `label`, and `detail`. Write outputs to the active artifact directory. Use equal-time labels for ordinary Action sampling and event labels for physical simulations. The compositor contains images without cropping and places text outside image areas, but its scaled result remains an overview rather than full-resolution visual evidence.

## Rendering Boundary

The Extension's `ROUNDTRIP` stage intentionally uses its own camera, world, lights, and scene render call to validate compiled MDL readback. It is not Blender Material Preview and must not be replaced with the MCP viewport screenshot path. Author-side Material Preview stills use Blender MCP's `execute_blender_code` plus `bpy.ops.render.opengl(...)`; MCP `get_viewport_screenshot` remains a current-user-viewport capture with overlays. These paths are linked by the workflow, but they have different evidence contracts.

The contract-driven boundary is deliberate: `EXPORT` resolves named contract objects, evaluates their dependency-graph geometry, reads the evaluated active UV and material slots, and converts an explicit image to indexed BMP. It does not read the current selection, bake arbitrary PBR node graphs, or decide whether a source UV is semantically correct for a baked image. The authoring workflow must freeze selection into the contract, create the GoldSrc UV/bake, and inspect the evaluated UV/material report.

For `large_textures`, the logical source image stays outside the MDL contract's physical texture table. EXPORT expands it into aligned `512x512` indexed BMP tiles, clips cross-tile triangles, writes local tile UVs, and records the result in `export_plan.json`. A source reference above the compiled `(bone, position)`, `(bone, normal)`, or triangle budget is split into complete SMD parts and compiled as multiple `$body` entries. Bodygroup choices are deliberately rejected when they require splitting. A text-only slicer is not part of the runtime path.

When `PREFLIGHT` returns `status: fail`, its requirement evidence is a failed diagnostic, not passing proof. A stage result with `status: pass` is required before pipeline evidence can establish a requirement.

API 1 does not author dual-source or four-source blend sequences. A compatibility report may warn about blend-count differences, but that warning is not evidence that the missing authoring path was reproduced.

## Contract Timing

For a basic asset, author first and write the smallest valid version 2 contract once real object, material, bone, and Action names are stable. For an enhanced asset, establish the intent requirements before authoring and complete structural bindings before `PREFLIGHT`.

Every contract and output path must be relative to an explicit artifact directory outside all Skill trees. Keep phase names and `sourceio_roundtrip` report semantics stable.

## Error Handling

Toolchain failures expose `phase`, `code`, `message`, and `details`. Fix the first owning layer:

- `PREFLIGHT`: Blender scene binding, weights, Action/range, geometry, materials.
- `EXPORT`: SMD/BMP/QC generation or postconditions.
- `COMPILE`: StudioMDL process or compiler-enforced limits.
- `INSPECT`: MDL binary structure or source-to-compiled animation mismatch.
- `ROUNDTRIP`: independent parser/reconstruction or visual/readback state.

Do not turn a stage failure into a scripted approximation of the requested model behavior.
