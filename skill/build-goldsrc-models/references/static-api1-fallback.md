# Static API-1 Fallback

Load this reference only when `capabilities()["api_version"] == 1` but the high-level static analysis, preparation, isolated roundtrip, strict pipeline, or unified visual comparison flags are absent.

## Manual Static Route

1. Record the exact active mesh name. Inspect its evaluated vertex/triangle counts, modifier result, UV layers, active-render UV, non-degenerate UV area, material slots/tokens, transforms, weights, armature, and idle Action in one Blender MCP code call.
2. Resolve UV, origin, color-bake, texture size, and transparency modes explicitly. Do not infer them from a missing UV or a complex material.
3. Duplicate the evaluated mesh and all required data into an independent export object. Leave the source object, modifiers, and materials unchanged.
4. For a strict unlit bake, duplicate supported materials, replace Principled/Diffuse closures with Strength-1 Emission while preserving color links and shader mixing, select the exact active-render GoldSrc UV, and run Cycles `EMIT` with a 16px margin. Stop on unsupported closure or node-group semantics.
5. Create one root bone, exactly one `1.0` weight per vertex, an Armature modifier, and a one-frame looping idle Action. Restore frame 0 and save an author checkpoint before readback.
6. Write the smallest valid v2 contract with exact object, Action, UV, logical texture, bounds, request, and requirement evidence declarations. Read [model-contract.md](model-contract.md) only when constructing this contract.
7. Execute each stage once and in order through the runtime API:

```python
for stage in ("PREFLIGHT", "EXPORT", "COMPILE", "INSPECT", "ROUNDTRIP"):
    report = api.execute_stage(stage, contract_path, artifacts_dir)
    if report["status"] not in {"pass", "pass_with_known_blockers"}:
        break
```

8. Save every full result as the canonical stage report when the older Extension does not persist it. Do not echo large per-pixel arrays into the conversation.
9. Before legacy `ROUNDTRIP`, save the author checkpoint and capture file, Scene, frame, Action, selection, active object, and viewport state. Restore the author file and all captured state afterward; never let readback cleanup destroy the only author scene.
10. Render contract-owned author geometry and readback from the same orthographic view after applying StudioMDL's root `+90 degree Z` mapping to the author side. Inspect both full-resolution images and a labeled comparison sheet.

Do not repeat any passing stage. Older API-1 fallback lacks the strict pipeline's internal repeated isolated `ROUNDTRIP`; perform a second readback only when explicit evidence requires idempotency, keep it in a separate evidence directory, and compare decoded pixels and structure rather than Blend bytes.

## Texture Boundary

Keep the logical PNG as the author image and use its logical `.bmp` token in SMD. Generated large-atlas tile BMPs belong only to EXPORT/COMPILE artifacts. For a normal texture, the indexed BMP is likewise a compiler artifact; do not silently replace the author's material image merely to make the fallback compile.
