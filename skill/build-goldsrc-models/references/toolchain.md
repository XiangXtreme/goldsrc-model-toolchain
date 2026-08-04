# External GoldSrc Model Toolchain

## Release Boundary

The Blender Extension is maintained publicly at <https://github.com/XiangXtreme/goldsrc-model-toolchain>. This Skill does not contain Extension source, StudioMDL, Pillow, SourceIO code, pipeline runners, fixtures, or release tooling.

The pinned validated release is:

- Extension `goldsrc_model_toolchain` `1.4.0`
- API version `1`
- GitHub tag `v1.4.0`
- Blender `5.2.x`, Windows x64
- Asset and SHA-256 recorded in `scripts/toolchain-release.json`

The `v1.4.0` archive contains the selected-static quick path and its validation capabilities. For large-texture or SMD-budget work, still require `capabilities()["features"]["large_texture_tiling"]` and `capabilities()["features"]["smd_budget_split"]`; use the sparse-atlas fast path only when `large_texture_tiling.sparse_compiled_tiles` is true. Consolidated static preflight additionally checks `preflight_material_texture_token`, `evaluated_material_mapping_audit`, and `export_time_triangulation`; decoded render comparison checks `roundtrip_decoded_pixel_hash`. Capability checks remain authoritative even when the version is compatible.

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

Version `1.4.0` adds export-plan version 2 sparse atlas selection, preflight/export material-token parity, audited evaluated-to-prepared material remapping, export-time evaluated-mesh triangulation warnings, decoded RGBA hashes, compact persisted reports, static analysis/preparation, isolated readback, strict pipeline orchestration, and unified visual comparison. `preview_hashes` remains the PNG artifact hash list for compatibility; `preview_pixel_hashes` and each preview's `pixel_sha256` are authoritative for image equality. Contact-sheet layouts add `source_pixel_sha256` per cell.

High-level static API:

```python
result = api.export_selected_static(
    artifacts_dir=artifacts_dir,
    model_name="prop.mdl",
    request=exact_user_request,
    texture_size=2048,
    uv_strategy="smart_project",
    origin_strategy="source_origin",
    bake_mode="unlit_color",
    assurance="strict",
    preserve_author_session=True, visual_compare=True,
    delivery_dir=delivery_dir,
)
```

`export_selected_static()` is the ordinary product route. It performs analysis, explicit preparation, and the strict pipeline internally, always asks for missing artistic strategy instead of choosing it, and returns a compact delivery result. The authoritative orchestration report is `reports/static_export.json`; stage evidence remains in the canonical reports.

The layered `analyze_selected_static()`, `prepare_static_export()`, `create_static_contract_from_scene()`, and `execute_pipeline()` methods remain available for advanced diagnosis and recovery. Analysis is read-only and returns a session-scoped fingerprint. Preparation returns `needs_decision` without Scene mutation when an artistic strategy is missing, rejects stale fingerprints, and freezes successful evaluated geometry into an independent marked Collection.

`execute_stage(stage, contract_path, artifacts_dir)` retains its API-1 full-result default. Optional `detail_level="summary"` returns a compact structure, `report_path` selects a path inside the artifact root, and `preserve_author_session=True` isolates ROUNDTRIP. Every call persists the full canonical report under `reports/`.

`execute_pipeline()` runs the five stages once in order and stops at the first failure. `assurance="strict"` is the only route that adds a second isolated ROUNDTRIP; it compares stable structure, weighted-vertex audit, decoded preview hashes, and contact-sheet pixel hashes rather than Blend bytes. It writes `pipeline.json`, `visual_compare.json`, and the repeated readback report while returning only compact facts by default.

## Rendering Boundary

The Extension's isolated `ROUNDTRIP` starts the same Blender binary with `--background --factory-startup --addons <current-package>`. It never opens or saves another Blend in the author process. Its ordinary evidence renderer remains independent from Blender Material Preview. Unified static comparison additionally renders only contract-owned author geometry and decoded readback geometry with one 512px orthographic camera, transparent background, Emission material, nearest sampling, and StudioMDL's author-side `+90 degree Z` root mapping. Author-side look-development stills can still use Blender MCP plus `bpy.ops.render.opengl(...)`; `get_viewport_screenshot` remains a current-user-viewport capture with overlays.

The contract-driven boundary is deliberate: `EXPORT` resolves named contract objects, evaluates dependency-graph geometry, reads the evaluated active UV and material slots, and converts an explicit image to indexed BMP. The high-level preparation API can mechanically freeze one previously analyzed selection and execute an explicitly chosen bake, but it never chooses UV, origin, material meaning, or transparency mode. Unsupported closure or node-group semantics return a decision request instead of silently degrading.

For `large_textures`, the prepared material keeps the logical PNG and its logical `.bmp` token stays outside the MDL's physical texture table. EXPORT computes the possible aligned `512x512` tile set, clips cross-tile triangles, writes local tile UVs, converts only geometry/skin-referenced tile BMPs, and records `declared`, `compiled`, and `omitted_unused_large_tiles` in export-plan version 2. Physical tile BMPs never become author materials. COMPILE, INSPECT, and ROUNDTRIP apply that effective texture contract before checking artifacts. A source reference above the compiled `(bone, position)`, `(bone, normal)`, or triangle budget is split into complete SMD parts and compiled as multiple `$body` entries. Bodygroup choices are deliberately rejected when they require splitting. Hidden anchor geometry and text-only slicers are not part of the runtime path.

When `PREFLIGHT` returns `status: fail`, its requirement evidence is a failed diagnostic, not passing proof. A stage result with `status: pass` is required before pipeline evidence can establish a requirement.

API 1 does not author dual-source or four-source blend sequences. A compatibility report may warn about blend-count differences, but that warning is not evidence that the missing authoring path was reproduced.

## Contract Timing

For a basic static asset, use one `export_selected_static()` call with explicit strategies. Do not decompose it into manual stages, re-query unchanged scene bindings, or repeat a passing stage. For an enhanced asset, establish intent requirements before authoring and complete structural bindings before `PREFLIGHT`.

Every contract and output path must be relative to an explicit artifact directory outside all Skill trees. Keep phase names and `sourceio_roundtrip` report semantics stable.

## Error Handling

Toolchain failures expose `phase`, `code`, `message`, and `details`. Fix the first owning layer:

- `PREFLIGHT`: Blender scene binding, weights, Action/range, geometry, materials.
- `EXPORT`: SMD/BMP/QC generation or postconditions.
- `COMPILE`: StudioMDL process or compiler-enforced limits.
- `INSPECT`: MDL binary structure or source-to-compiled animation mismatch.
- `ROUNDTRIP`: independent parser/reconstruction or visual/readback state.

Do not turn a stage failure into a scripted approximation of the requested model behavior.
