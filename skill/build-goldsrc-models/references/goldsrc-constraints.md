# GoldSrc Model Constraints

## SMD And Geometry

- Export GoldSrc SMD version 1.
- Apply object scale and rotation before export. EXPORT triangulates the evaluated mesh deterministically; author or apply triangulation only when a specific diagonal is part of the intended topology.
- Use exactly one bone influence per exported vertex. A static mesh still needs a root bone and an idle sequence.
- Confirm axes, origin, scale, and bounds with a non-symmetric model.
- Preserve explicit custom normals when the asset depends on flat, foliage, or controlled lighting behavior.
- Calculate `$bbox` and `$cbox` from exported SMD bounds multiplied by `$scale`; do not accept universal zero bounds.
- Keep the exported skeleton at or below 128 bones. For Half-Life/Counter-Strike, Valve's `studio.h` limits each compiled submodel to 2,048 stored vertices, 2,048 stored normals, and 20,000 triangles; count SMD entries by unique `(bone, position)` and `(bone, normal)` rather than raw triangle lines. Evaluate final modifier output before export. EXPORT may preserve triangle order and split an over-budget reference into multiple always-present `$body` entries. For Sven-only contracts, report overflow beyond these legacy values as compatibility risk and let the selected Sven compiler enforce its extended ceiling.

## Blender 5.2 API Notes

- `MeshLoop` has no `polygon_index` property; `MeshLoopTriangle` does. Iterate `mesh.polygons`, then each `polygon.loop_indices`, when assigning or checking corner normals.
- Use the public Python RNA method `mesh.normals_split_custom_set(...)` with loop-order data after building the complete loop-normal array. The internal helper `mesh_set_custom_normals` is C++ only, not a Blender Python method. Check the method on a mesh instance, not on `bpy.types.Mesh` itself.
- GoldSrc single-weight repair must remove or zero competing vertex-group membership, not merely normalize multiple groups.

## Textures And Materials

- Use uncompressed 8-bit indexed BMP with a 256-entry palette.
- Record used-index frequencies, visible color count, and palette-weighted luminance from the final BMP. Single-color, all-black, or fully transparent results require visual review but are not automatically artistic failures.
- Keep each physical MDL texture width and height at multiples of 16 and no larger than 512. A logical atlas may exceed 512 only when it is divisible into no more than 64 possible `512x512` tiles; the complete MDL still has one shared 64-texture budget across all atlases and ordinary textures. Export-plan version 2 embeds only geometry/skin-referenced tiles and omits the rest. Logical atlas tokens, ordinary texture names, and generated tile names must not collide. The atlas is never embedded as one oversized texture.
- The SMD material token must match the compiled texture filename, including `.bmp` where expected.
- For masked textures, reserve palette index 255 and use blue `(0, 0, 255)` for the transparent entry.
- Tested MDL v10 texture flags are flatshade `0x0001`, chrome `0x0002`, fullbright `0x0004`, additive `0x0020`, and masked `0x0040`.
- For Half-Life/Counter-Strike, effective Chrome textures are exactly `64x64` and Fullbright is invalid. The legacy `CHROME_` filename form implies Chrome+Flatshade; an explicit Chrome flag does not require that prefix. Sven 5.18+ removes the classic Chrome-size restriction; prefer at most `256x256` unless the target requires more.
- Emit Additive `$texrendermode` entries before Masked entries. Blender 5.2 has no native Additive material blend mode, so preview it with Transparent/Emission nodes and validate the compiled MDL flag.
- Double-sided foliage requires duplicated triangles with opposite winding; material flags do not create reverse geometry.

## Animation And Model Structure

- Include at least one sequence.
- Confirm sequence frame count, FPS, loop state, activity, events, and root motion from exported SMD/QC/MDL rather than Blender playback alone.
- Validate hitboxes, attachments, mouth/controller declarations, and player color-remap texture semantics where applicable.
- Bodygroup validation must count each bodypart and its `studio`/`blank` choices.
- Skin-family validation must inspect the MDL skin table, not only the imported material list.
- A baseline-compatible player preserves the exact sequence count/order and FPS, does not exceed baseline frame counts, keeps baseline bones as an unchanged ordered prefix, and appends new bones only below baseline leaves. NPC additions go after the baseline sequence prefix.

For sampled rigid-body sequences, preserve duration with `export_fps = source_fps / sample_step`. Compiler animation-data limits are compiler/output constraints, not a license to silently shorten the requested event chain; report the budget estimate and fail when the selected compiler rejects the sequence.

## Target Limits

Use Sven StudioMDL as the default modern compiler, while treating Half-Life/Counter-Strike behavior as the output limit set. UV accuracy, UV tiling, and bone/attachment preservation can improve without changing the MDL v10 format. Higher Sven texture/chrome limits and other Sven-only engine features are not automatically compatible with Half-Life or Counter-Strike. Never use compiler success alone to erase a target-engine limitation.

Write `$bbox` and `$cbox` explicitly. Parse vectors at MDL header offsets and compare them with QC because modern Sven compiler versions have a reported cbox rotation/misalignment bug. The local advanced fixtures produced identical explicit bounds with the Sven and Half-Life SDK compilers.
