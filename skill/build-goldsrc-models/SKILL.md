---
name: build-goldsrc-models
description: Create, repair, and deliver GoldSrc MDL v10 models in Blender 5.2 with the live official Blender MCP. Use for Half-Life, Counter-Strike, or Sven Co-op props, indexed textures, logical large-texture atlases, compiled SMD budget splitting, skeletal animation, NPC/player models, bodygroups and skins, animation remapping, physics-to-bone effects, SMD/QC compilation, MDL readback, imported-model orientation and axis diagnosis, author-side Blender Material Preview viewport stills, or troubleshooting visible and structural model defects. Choose an authoring workflow and known pitfall guidance first; use the external public goldsrc_model_toolchain Extension for deterministic export and validation. Keep maps, BSP, runtime spawning, and game scripts out of scope.
---

# Build GoldSrc Models

Create the visible asset in Blender `5.2.x` through the live official `ahujasid/blender-mcp` session. Keep creative decisions with the agent: decide topology, silhouette, UV intent, origin, material meaning, transparency, animation, and target behavior from the request and inspected asset. Let the Extension perform freezing, baking execution, static rigging, contracts, compilation, validation, and evidence organization.

## Route The Work

- Use the static quick path below for one active or explicitly named mesh, including a deterministic evaluated modifier stack and an explicit color bake.
- Read [workflow-static-materials.md](references/workflow-static-materials.md) only for authoring, UV, material, high-to-low bake, or texture repair decisions beyond the quick path.
- Read [workflow-animation-characters.md](references/workflow-animation-characters.md) for skeletal animation, characters, bodygroups, skins, controllers, attachments, or hitboxes.
- Read [workflow-advanced-fx.md](references/workflow-advanced-fx.md) for baked visual effects and staged deformation.
- Read [workflow-physics-baking.md](references/workflow-physics-baking.md) for physics-to-bone work.
- Read [workflow-import-repair.md](references/workflow-import-repair.md) for imported assets or readback mismatch repair.
- Do not preload all references or all of [pitfalls.md](references/pitfalls.md). Load only the reference selected above or the section named by the first owning failure.

## Check Capabilities Once

Use one Blender MCP code call to obtain:

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
capabilities = api.capabilities()
```

Require `api_version == 1`. For the static quick path, require these feature flags:

```text
selected_static_export
stage_report_persistence
summary_stage_results
isolated_roundtrip
strict_static_pipeline
unified_static_visual_compare
evaluated_material_mapping_audit
```

Use actual capabilities, not the version string: the pinned public compatibility baseline is `1.4.0 / API 1`. If the runtime is missing or older, use `scripts/install_toolchain.py --apply`. If API 1 is present but any quick-path flag is absent, read [static-api1-fallback.md](references/static-api1-fallback.md) and use the manual API-1 route; do not improvise a partial high-level workflow.

## Export One Static Selection

Resolve every artistic strategy explicitly from the user's request and the asset, then use the product entry once. Map `2K` to `2048` and "no baked lighting" to `unlit_color`. Preserve an authored valid UV with `existing` plus its exact layer; use `smart_project` only when no usable authored UV exists and automatic unwrap is appropriate. For a plain selected prop, preserve its authored pivot with `source_origin` unless the requested placement calls for another origin. These remain agent decisions, not plugin defaults.

```python
result = api.export_selected_static(
    artifacts_dir=artifacts_dir,
    model_name="branched_cave_2k.mdl",
    request="the user's exact request",
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

If this returns `needs_decision`, resolve only the listed item and call the same product entry again; no Scene mutation occurred. Transparent semantics always require explicit `goldsrc_modes`. Unsupported material closure or node-group meaning also stops for a decision instead of degrading. For advanced diagnosis or recovery only, use the layered analysis, preparation, contract, and pipeline APIs documented in [toolchain.md](references/toolchain.md).

The product entry owns preparation and `PREFLIGHT -> EXPORT -> COMPILE -> INSPECT -> ROUNDTRIP`, stops at the first failure, persists full JSON reports, performs isolated readback, and returns only delivery facts. When modifiers change material slots, its evaluated-material audit must prove source face identity, prepared remapping, and logical SMD token counts before compilation. Never manually repeat a passing stage. The sole exception is the second isolated `ROUNDTRIP` performed internally by `assurance="strict"` to prove deterministic readback.

## Keep Texture Roles Distinct

- The prepared Blender material references the logical PNG used for authoring and baking.
- Its `goldsrc_texture_token` is the logical `.bmp` name written into SMD material records.
- For a logical texture above `512x512`, generated physical `512x512` indexed BMP tiles are compiler artifacts only. Never attach them to author materials.
- For a normal texture, EXPORT writes the indexed BMP compiler artifact but does not rebind it to the prepared material.
- A `2048x2048` logical atlas has sixteen possible tiles; sparse EXPORT embeds only geometry- or skin-referenced tiles after cross-tile clipping. Do not add hidden anchor triangles.

## Respond To The First Failure

- `static.*uv*`, `mesh.*uv*`, or `export.uv*`: read the UV section of [workflow-static-materials.md](references/workflow-static-materials.md), then search `UV` in [pitfalls.md](references/pitfalls.md).
- `static.*material*`, `static.*bake*`, `export.material*`, or `texture.*`: read the material/bake section of [workflow-static-materials.md](references/workflow-static-materials.md), then search the exact code in [pitfalls.md](references/pitfalls.md); for evaluated mapping failures load `evaluated-material-index`.
- `animation.*` or motion/axis mismatch: read [workflow-animation-characters.md](references/workflow-animation-characters.md).
- `physics.*` or rigid-body ownership: read [workflow-physics-baking.md](references/workflow-physics-baking.md).
- `compile.*`, `mdl.*`, `roundtrip.*`, or `visual.*`: read [validation.md](references/validation.md) and, for imported data, [workflow-import-repair.md](references/workflow-import-repair.md).
- Do not change unrelated art or rerun earlier passing stages while diagnosing a later owning stage.

## Deliver

Keep every contract, Blend, SMD, QC, BMP, MDL, report, render, cache, ZIP, and temporary directory outside the Skill tree. Let `delivery_dir` atomically receive only the requested MDL; do not overwrite an existing same-name file unless the user explicitly authorized it, and do not delete other delivery files.

Accept the result only when the strict pipeline, evaluated-material mapping audit, and canonical visual comparison pass. Report the returned MDL path, SHA-256, size, `author_triangles`, `crossed_tile_triangles`, `post_tile_triangles`, tile/texture facts, warnings, and report directory. Say `in-game validated` only after an actual named game or mod load.
