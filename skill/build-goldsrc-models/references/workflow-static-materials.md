# Static Models And Materials

## Decide The Representation

Use this route for props, environment objects, one or more visible materials, transparent/additive/chrome surfaces, foliage planes, and high-to-low diffuse bakes. A static GoldSrc model still needs one root bone and an idle sequence.

Choose dimensions, pivot, axes, silhouette, material boundaries, and target profile before detailing. Test orientation with a non-symmetric shape; a cube cannot expose mirrored axes or a rotated origin.

## Build The Mesh

1. Model at explicit dimensions and apply object scale and rotation.
2. Keep purposeful hard edges and flat facets. Triangulate only after the final topology/modifier result is known.
3. Add one root armature. Give every exported vertex exactly one `1.0` influence, including static geometry.
4. Evaluate modifiers before checking vertex and triangle budgets.
5. Duplicate reverse-wound triangles where a plane must be visible from both sides; material flags do not create back faces.

For a selected-object delivery, freeze the active object's exact name into the contract and keep the export object separate from unrelated scene meshes. When Geometry Nodes or modifiers are present, inspect the evaluated dependency-graph mesh and use its final UV layer and material slots as the export truth. Do not validate only the pre-modifier mesh.

## Create UVs And Materials

1. Unwrap around visible seams, scale islands consistently, and inspect painted/checker distortion.
2. Assign a real image to every exported material. Match the SMD material token to the final `.bmp` filename.
3. Keep each physical MDL texture at multiples of 16 and no larger than 512 for the conservative Half-Life/Counter-Strike profile. For a source atlas larger than 512, declare a logical `large_textures` record and let the Extension generate aligned `512x512` tiles; a `2048x2048` author image is sixteen MDL textures, not one oversized MDL texture.
4. Save file-backed source images before export. Let the Extension read their encoded pixels from disk; use explicit linear RGB and bottom-left row semantics only for generated or dirty Blender buffers.
5. Convert to uncompressed 8-bit indexed BMP with 256 palette entries. Reserve blue index 255 only for Masked textures; non-Masked textures may use all 256 indices.
6. Reload or repoint the Blender material to the final BMP so author preview, exported token, embedded MDL texture, and readback use the same asset.
7. Preview Chrome, fullbright, additive, masked, or flatshade intent in Blender, then verify actual MDL texture flags after compilation.

Keep source PBR/procedural UVs separate from the GoldSrc UV. The GoldSrc UV must be active on the evaluated export mesh when SMD triangles are written and must also be the UV layer with `active_render=True` before `bpy.ops.object.bake`. Declare `texture_bake.uv_layer` with `require_active_render: true` so PREFLIGHT can reject a raw/evaluated mismatch. Bake the visible color into a file-backed image, convert it to the final indexed BMP, and repoint the export material to that BMP before the author/readback comparison. The Extension reports evaluated UV and material facts, but it does not infer a correct bake or semantic correspondence between a node graph and a texture.

For a strict no-baked-shadow request, first run a `DIFFUSE`/`COLOR`-only bake as a diagnostic. If it is still dark, the darkness is part of the material graph rather than direct/indirect scene lighting. Make a temporary copy of the source material, replace its Principled/Diffuse closures with Emission nodes driven by Base Color at Strength `1.0`, preserve shader mix factors, and bake `EMIT` to a separate file-backed image. Keep alpha/masked behavior as a separate explicit output decision because an Emission closure does not carry a Principled alpha socket by itself. Never replace the saved author material with the temporary emission graph.

For a logical atlas, keep the GoldSrc UV across the full `0..1` image. EXPORT crops bottom-origin `512` tiles, clips triangles that cross tile edges, triangulates the clipped polygons, and remaps each tile's UVs locally. Check that every generated tile is indexed, that the SMD token includes the generated `.bmp` name, and that the compiled MDL contains only `512x512` texture records. Reject more than 64 tiles for one MDL.

## Preview Special Texture Modes

| Mode | GoldSrc rule | Blender 5.2 author preview |
|---|---|---|
| Chrome | The legacy `CHROME_` name-set form implies Chrome+Flatshade; explicit `chrome` mode is a flag-set form and needs no prefix. Half-Life/CS requires `64x64`; Sven 5.18+ does not. | Feed Normal/Reflection-style coordinates into the intended image and an Emission-like surface to approximate view-dependent placement. Do not evaluate Chrome from ordinary UVs. |
| Additive | Black contributes no light and bright pixels contribute more; compile flag `0x0020`. | Mix Transparent and Emission nodes. Blender 5.2 has no native `blend_method = ADDITIVE`, so this is an intent preview only. |
| Masked | Palette index 255 must be blue `(0, 0, 255)` and becomes transparent; compile flag `0x0040`. | Derive alpha from the source mask and use the still-supported legacy `Material.blend_method = 'CLIP'` plus `alpha_threshold` for exact clip preview. Blender 5.2 documents `surface_render_method` as the preferred general transparency API, but it does not replace GoldSrc's indexed Masked flag. Inspect blue fringes at cutout edges. |
| Flatshade | Compiler flag `0x0001`; lighting ignores the intended smooth-normal response. | Use flat face shading and inspect actual split/loop normals rather than changing only the material. |
| Fullbright | Sven/Xash3D-only flag `0x0004`; reject it for `half-life-cs`. | Use Emission so the preview is independent of scene lighting, then verify the MDL flag under a Sven contract. |

Keep the final judgment in the compiled MDL. A Blender node graph does not set GoldSrc flags. The Extension emits non-Masked `$texrendermode` declarations first and Masked declarations last, guaranteeing Additive entries precede Masked entries. Prefer Chrome textures no larger than `256x256` for Sven unless the target and readback evidence justify a higher legal size.

## Bake High-To-Low Color

1. UV unwrap the low model and make its destination image node active.
2. Select high then low, leaving low active; enable selected-to-active.
3. Use Cycles for `bpy.ops.object.bake` in Blender 5.2. Disable direct and indirect passes when the target is albedo.
4. If the target must contain no material lighting/shadow contribution, use the temporary Emission closure workflow above and `type='EMIT'`; `DIFFUSE`/`COLOR`-only is not a guarantee that AO or other color-graph darkening is absent.
5. Save the source image, convert to indexed BMP, reload that BMP, and compare color range and visible brightness.

## Inspect Before Export

- Render front, side, and three-quarter views with readable lighting.
- Inspect silhouette, UV seams, material boundaries, transparent edges, back faces, pivot, and ground contact.
- Check final evaluated geometry, single weights, bounds, texture dimensions, palette usage, and material tokens.
- Read [pitfalls.md](pitfalls.md) sections `tex-indexed`, `tex-colorspace`, `tex-stale-image`, `tex-bake-unlit`, `tex-export-fidelity`, `uv-compensation`, and `smd-material-token` when results differ from the author view.

## Export And Read Back

Create the minimal contract only after the exact export object, evaluated geometry, root bone, GoldSrc UV, material image, and idle Action names are stable. Execute all five Extension stages, inspect evaluated UV/material facts and each texture's conversion method, color counts, MAE, maximum channel error, and orientation evidence, then inspect `export_plan.json` for cross-tile triangle counts and per-part compiled budgets before comparing the independent readback to the author render from the same view/frame. Compilation alone does not validate UV placement, UV tiling, brightness, flags, or orientation.
