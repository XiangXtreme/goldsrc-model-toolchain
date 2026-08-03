---
name: build-goldsrc-models
description: Create, repair, and deliver GoldSrc MDL v10 models in Blender 5.2 with the live official Blender MCP. Use for Half-Life, Counter-Strike, or Sven Co-op props, indexed textures, logical large-texture atlases, compiled SMD budget splitting, skeletal animation, NPC/player models, bodygroups and skins, animation remapping, physics-to-bone effects, SMD/QC compilation, MDL readback, imported-model orientation and axis diagnosis, author-side Blender Material Preview viewport stills, or troubleshooting visible and structural model defects. Choose an authoring workflow and known pitfall guidance first; use the external public goldsrc_model_toolchain Extension for deterministic export and validation. Keep maps, BSP, runtime spawning, and game scripts out of scope.
---

# Build GoldSrc Models

Create the requested asset in Blender `5.2.x` on Windows x64 through the live official `ahujasid/blender-mcp` session. Let the model's visible behavior determine topology, rigging, materials, and simulation strategy before thinking about pipeline mechanics.

## Understand The Asset

- Preserve the user's wording and separate observable requirements from implementation choices. Do not invent numeric artistic thresholds.
- Inspect only the active thread workspace, this canonical Skill, and user-named paths. Do not search sibling Codex projects or use old scenes as reference answers unless the user requests reuse.
- Select one authoring guide before editing:
  - Static props, UVs, materials, indexed textures, and high-to-low bakes: [references/workflow-static-materials.md](references/workflow-static-materials.md).
  - Skeletal animation, remapping, NPC/player structure, bodygroups, skins, controllers, attachments, and hitboxes: [references/workflow-animation-characters.md](references/workflow-animation-characters.md).
  - Baked flame/smoke/embers, fake highlights, intermediate-bone deformation, detail overlays, and skin-state effects: [references/workflow-advanced-fx.md](references/workflow-advanced-fx.md).
  - Rigid bodies, fracture, ropes/chains, cloth approximations, collision chains, and physics-to-bone transfer: [references/workflow-physics-baking.md](references/workflow-physics-baking.md).
  - Imported-model diagnosis, UV/weight/normal repair, compile failures, and readback mismatches: [references/workflow-import-repair.md](references/workflow-import-repair.md).
- Search [references/pitfalls.md](references/pitfalls.md) for the relevant topic before committing to an implementation. Use [references/goldsrc-constraints.md](references/goldsrc-constraints.md) for actual format limits.

## Author And Inspect

1. Establish scale, silhouette, origin, initial rest orientation, axes, visible materials, and the required motion or variants in Blender. For an image-defined flat asset, declare the upright direction, visible-front plane, and thickness axis from the source image, then inspect the first imported frame from orthographic front/top/side views before authoring animation. Do not assume an XY-plane import is an upright logo. For a requested world-axis rotation, resolve the axis through the bone rest basis and measure the evaluated world-space axis at a non-symmetric sample; never infer it from a pose-channel suffix. See [rotation-axis-space](references/workflow-animation-characters.md#rotation-axis-space) and [import-orientation](references/pitfalls.md#import-orientation).
   For a request to export the current selection, record the active mesh and selected mesh names first, then write those exact names into the contract. Selection is authoring intent, not a runtime export selector; do not let a later stale selection choose a different object.
   If the source object has Geometry Nodes or other modifiers, inspect the evaluated dependency-graph mesh before creating the contract. Confirm its evaluated triangle/vertex counts, active UV layer, active-render UV layer, UV bounds, non-degenerate UV triangle area, material slots, and final material tokens. UV bounds alone do not prove that a surface can carry a texture: stacked or planar-projection UV islands may collapse a triangle to a line. Create and activate an explicit GoldSrc UV layer, set that same layer's `active_render` flag before any Blender bake, declare `texture_bake: {"uv_layer": "GoldSrcUV", "require_active_render": true}`, and bake the visible color against that exact layer before export. Apply or duplicate the evaluated result into a named export object when the modifier depends on scene state that will not exist during later validation.
2. Use real image textures and inspect author renders early. For author stills that must match Blender's Material Preview, invoke Blender MCP's `execute_blender_code` and use a valid `VIEW_3D` context, then call `bpy.ops.render.opengl(write_still=True, view_context=True)` after setting `space.shading.type = 'MATERIAL'`, `use_scene_lights = False`, and `use_scene_world = False`. Do not use `get_viewport_screenshot` for this export: that official MCP tool intentionally captures the current user viewport, including its overlays. Use `bpy.context.temp_override(window=..., area=..., region=..., space_data=...)` when the caller is not already in that view. `view_context=True` reuses the current `RegionView3D`; it does not switch to camera view, so enter camera view explicitly when camera framing is intended. The operator writes at the scene render resolution (including percentage), not the viewport pixel size; with no valid `VIEW_3D` it falls back to the scene-camera path and therefore requires a scene camera. A Workbench scene does not provide the EEVEE studio-light Material Preview path: its `OB_MATERIAL` draw is handled by Workbench using scene display shading. Temporarily disable that area's overlays for the capture, save the previous `show_overlays` value, and restore it in a `finally` block before saving the Blend checkpoint. Keep this viewport path separate from final render validation: do not add Emission, alter asset material nodes, or add compensating lights just to make the author image visible. If a dark background is required, capture transparent pixels and composite the background outside the material. `ROUNDTRIP` must continue using the toolchain's independent deterministic renderer. A valid mesh, palette, or simulation report is not visual acceptance.
3. Keep all eventual render geometry present from frame 0. GoldSrc receives geometry and baked bone animation, not runtime physics or spawning.
   For PBR or procedural source materials, keep the author material for look development but create an explicit GoldSrc UV layer and bake the intended visible color into a file-backed image. Before `bpy.ops.object.bake`, set the layer as both `mesh.uv_layers.active` and the layer's `active_render` flag; Blender 5.2 baking can otherwise read a different UV from the one EXPORT writes. Declare the target in `texture_bake`, convert the image to the final indexed BMP, reload the BMP on the export material, and verify the evaluated mesh uses that UV/material pair. EXPORT does not bake node graphs or infer semantic UV mappings.
   If the requested result is explicitly free of baked lighting or shadows, distinguish two bake modes. `DIFFUSE` with `pass_filter={'COLOR'}` and direct/indirect disabled removes Cycles ray lighting, but it can still preserve darkening that is part of the material color graph, such as an AO image multiplied into Base Color. For a strict unlit atlas, duplicate the source material without changing the author material, replace its surface shader closures (Principled/Diffuse) with `Emission` driven by each closure's Base Color and Strength `1.0`, keep the intended color/mix factors, and bake `EMIT` on the same GoldSrc UV. Save the result before indexed conversion, then use an ordinary image-backed export material. Do not add Emission to the final author/export material merely to make a render visible, and do not claim a GoldSrc `fullbright` flag unless the target engine supports it and the contract requests it.
4. Bind the intended Action, set FPS and the inclusive scene playback range, set the Action range deliberately, restore the start frame, and save a playable Blend checkpoint. Spacebar playback must show the authored animation.
5. Inspect representative views or frames while changes are still cheap: silhouette, contact/pose extremes, texture brightness, seams, penetration, and the final state.

### Handle GoldSrc Size Limits

- Treat a source atlas larger than `512x512` as a logical author texture, not as one MDL texture. Declare it in `large_textures` with dimensions divisible by `512`; a `2048x2048` source becomes sixteen `512x512` indexed BMP entries in one MDL. Never put a `2048` texture directly in `textures` or claim that the MDL embeds one.
- Keep the logical atlas UVs in `0..1`. Let EXPORT clip and retriangulate every triangle crossing a tile boundary, assign the generated tile material token, and remap the clipped vertices to local tile UVs. Clipping must preserve each triangle's 3D area and must compare full corner attributes, not UV coordinates alone, because stacked UV islands can contain different positions/normals at one UV. Validate the tile crop orientation and the SMD material tokens against the final BMPs. One MDL supports at most 64 generated tiles; larger atlases require separate deliverables and are rejected by the current contract.
- Measure reference SMD budgets by compiled `(bone, position)` vertices, `(bone, normal)` entries, and triangles. If a source exceeds `2048`, `2048`, or `20000`, let EXPORT preserve triangle order while splitting it into multiple SMD parts and `$body` entries. Bodygroup choices are rejected when they need splitting. A four-line text slicer such as `smdcutpy.py` does not perform compiled deduplication, UV clipping, bone checks, or QC updates and is not a production path.

## Choose Assurance

Use the basic route for an ordinary static model or one simple Action with no bodygroups, skin families, controllers, attachments, hitboxes beyond routine defaults, imported repair, or baked physics. Finish authoring first, then create the smallest valid version 2 contract with the original request, its main literal deliverable requirement, and the real Blender object/Action names.

Use the enhanced route before authoring when the request involves repair, Geometry Nodes or other evaluated modifiers, PBR/procedural material baking, selected-object delivery, multiple Actions, bodygroups/skins, character metadata, physical simulation, non-rigid approximation, staged contacts, compiler/readback disagreement, or explicit evidence requirements. Trace every observable requirement and retain checkpoints plus phase evidence. Read [references/model-contract.md](references/model-contract.md) and [references/validation.md](references/validation.md) only for this route or when a basic build fails a gate.

Both routes must execute `PREFLIGHT`, `EXPORT`, `COMPILE`, `INSPECT`, and `ROUNDTRIP`, then compare author and MDL-readback views from the same action/frame and a declared view. Use `api.create_visual_contact_sheet(...)` to assemble an overview when useful, but preserve and inspect the original author and readback PNGs at full resolution. The difference is contract/evidence depth, not whether the final MDL is checked.

## Use The External Toolchain

Check `bpy.app.driver_namespace["goldsrc_model_toolchain"].capabilities()` through live MCP. Version `1.3.3` with `api_version = 1` is the pinned public baseline; require the capability keys relevant to the request, including `large_texture_tiling` and `smd_budget_split` for atlas/budget work. Allow a newer API-1 version with a compatibility warning and never auto-downgrade it. A development checkout may contain these capabilities before the next public release, so compare the actual capability report rather than trusting the version string alone. If the required runtime is missing or lacks the needed capability, run:

```powershell
python scripts/install_toolchain.py --apply
```

The installer downloads the pinned public GitHub Release, verifies SHA-256, and installs only the Extension. It never changes Blender MCP or Codex configuration. See [references/toolchain.md](references/toolchain.md) for API and installation details.

Use the runtime API or background operator; do not import namespaced Extension internals or use legacy Source Tools/SourceIO operators:

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
result = api.execute_stage(stage, contract_path, artifacts_dir)
```

Before `ROUNDTRIP`, save the author Blend checkpoint and restore temporary viewport shading, overlay, render, frame, and Action state. Release or isolate any Blender Bullet World before readback; never allow readback cleanup to delete the user's author scene. If a stage returns `status: fail`, every matching `requirement_evidence` entry must also be `status: fail`; do not treat a report with facts as proof.

For atlas exports, inspect `export_plan.json` before compilation. It is the source of truth for generated tile materials and split SMD parts; COMPILE consumes its relative paths and emits the parts as always-present `$body` entries. Do not manually add every tile to QC or bypass the plan with a text slicer.

Keep contracts, Blend files, SMD, QC, BMP, MDL, reports, renders, caches, ZIPs, and extraction directories outside every Skill tree.

## Deliver The Result

- Verify the requested model visually in the author scene and independent MDL readback. For animation, scan the labeled start/quarter/mid/three-quarter/end contact sheet first, then open suspicious source frames at full resolution. A contact sheet is an index, not a substitute for author and readback stills.
- Report `Blender equivalent reproduced` only after author/preflight/export evidence. Report `in-game validated` only after an actual named game/mod load.
- Classify failures as authoring, Blender lifecycle, export/material, compiler, MDL binary, readback, or delivery rather than repeatedly changing unrelated parameters.
- Copy only requested runtime files into the delivery directory. One self-contained MDL means exactly one `.mdl` and no convenience files.
