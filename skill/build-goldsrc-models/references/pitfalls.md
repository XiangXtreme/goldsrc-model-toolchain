# GoldSrc Blender Pitfalls

Use this as a symptom-driven index. Each entry records a reproduced failure, not a universal artistic rule.

## Contents

- Geometry, UV, normals, and orientation: `uv-compensation`, `evaluated-uv`, `evaluated-material-index`, `single-weight`, `loop-normals`, `import-orientation`
- Textures and materials: `tex-indexed`, `tex-colorspace`, `tex-stale-image`, `tex-masked`, `tex-bake-engine`, `tex-bake-active-render-uv`, `tex-bake-unlit`, `smd-material-token`, `tex-export-fidelity`
- SMD, QC, and bounds: `smd-bounds`, `compiler-success`, `submodel-budget`, `large-texture-atlas`, `smd-budget-split`, `custom-build-preflight`, `bmp-errors`, `runtime-upload`, `remap-cache`, `obsolete-qc`
- Animation: `action-channelbags`, `bone-space`, `animation-bind-pose`, `preserve-target-animation`, `playback-start`, `loop-endpoint`, `frame-count-semantics`, `player-compatibility`, `npc-root-motion`
- Physics: `physics-lifecycle`, `contact-gating`, `collision-proxies`, `settlement`
- Author preview, session, and readback: `viewport-material-preview`, `namespace-collisions`, `readback-collisions`, `blank-readback`, `contact-sheet-overview`

## Geometry, UV, And Normals

<a id="evaluated-uv"></a>
### GEO-04 The Pre-Modifier UV Is Not Always The Export UV

- **Applies to:** Geometry Nodes, procedural modifiers, applied modifier copies, and any export that uses final evaluated geometry.
- **Visible symptom:** the mesh silhouette is correct but the MDL texture is shifted, stretched, or uses a different material region than the Blender author view.
- **Root cause:** the source mesh UV/material slots were inspected while the SMD exporter read the dependency-graph evaluated mesh; the evaluated mesh may have a different active UV, loop count, material index, or generated island layout.
- **Reliable approach:** inspect the evaluated mesh with `preserve_all_data_layers=True`, record its active UV name, per-loop UV bounds, non-degenerate UV triangle area, material slots, and material tokens, then create/activate a valid GoldSrc unwrap and bake/reload the explicit GoldSrc image against that UV before export.
- **Avoid:** assuming a modifier preserves UV semantics, making an unrelated UV layer active after the author still, or asking EXPORT to infer a PBR bake.
- **Verify:** compare evaluated UV/material facts, UV-degenerate counts, SMD triangle tokens, final BMP, and author/readback landmarks on the same visible surfaces.
- **Evidence:** Blender 5.2 dependency-graph export path and GoldSrc toolchain evaluated-surface preflight/export reports.

<a id="evaluated-material-index"></a>
### GEO-05 Evaluated Material Indices Must Be Remapped By Identity

- **Applies to:** Geometry Nodes or modifiers that append, replace, or reorder evaluated material slots.
- **Visible symptom:** geometry is complete but the MDL uses an unused raw material while the actual evaluated material bakes black or disappears.
- **Root cause:** Blender 5.2 `bpy.data.meshes.new_from_object(evaluated, ...)` can rebuild from original object data and return different polygon material indices even when the evaluated slot list looks correct.
- **Reliable approach:** capture the current evaluated topology, per-polygon material identity, and per-slot face/triangle counts first; require identical frozen topology; retain only used materials; apply an explicit old-index to compacted-index mapping; persist source, prepared, and logical SMD token counts in the contract.
- **Avoid:** trusting equal slot counts, matching prepared against its own MDL readback, or accepting a visual comparison that starts from the already-remapped object as the only material evidence.
- **Verify:** PREFLIGHT re-evaluates the selected source and prepared object independently; EXPORT requires every source-used material to retain its face/triangle distribution and every prepared logical token to receive the expected SMD triangles.
- **Evidence:** Blender 5.2 `mesh_create_eval_final()` path and the two-material Geometry Nodes regression fixture with raw slot 0 unused and evaluated slot 1 used.

<a id="uv-compensation"></a>
### GEO-01 UV 1.007 Compensation Is Not A Default

- **Applies to:** imported legacy UV drift or a demonstrated edge-sampling mismatch.
- **Visible symptom:** the texture is slightly displaced even though islands look proportionally correct.
- **Root cause:** a legacy tool/source convention, not a general Blender export requirement.
- **Reliable approach:** compare UVs and renders numerically before and after; apply `1.007` or a manual island move only to the proven source problem.
- **Avoid:** multiplying every model's UVs by `1.007` during export.
- **Verify:** author render and MDL readback place distinctive texture landmarks on the same vertices.
- **Evidence:** the303 `gold_mdl_repair`; validated Blender repair fixture.

<a id="single-weight"></a>
### GEO-02 Normalizing Does Not Produce GoldSrc Single Weights

- **Applies to:** every exported mesh, including static props.
- **Visible symptom:** rigid pieces stretch, animation collapses, or SMD validation reports multiple influences.
- **Root cause:** Blender keeps multiple positive vertex-group memberships after normalization.
- **Reliable approach:** select one owner bone, set it to `1.0`, and remove or zero all competing groups.
- **Avoid:** relying on Auto Normalize or visually small secondary weights.
- **Verify:** every exported vertex has exactly one positive `1.0` influence and the evaluated silhouette remains correct.
- **Evidence:** the303 repair coverage; toolchain contract and SMD regression tests.

<a id="loop-normals"></a>
### GEO-03 Blender 5.2 Custom Normals Use Loop Order

- **Applies to:** flat facets, foliage, controlled highlights, and repaired imported normals.
- **Visible symptom:** normals attach to the wrong corners or a script fails on `MeshLoop.polygon_index`.
- **Root cause:** Blender 5.2 loops do not expose `polygon_index`.
- **Reliable approach:** iterate polygons and each `polygon.loop_indices`, build the complete loop-normal array, then call the public RNA method `mesh.normals_split_custom_set(...)`. The C++ helper is named `mesh_set_custom_normals`; it is not a Python API. Test the method on a mesh instance rather than on `bpy.types.Mesh`.
- **Avoid:** deriving polygon ownership from a nonexistent loop property.
- **Verify:** inspect face shading in author and readback views with nonuniform lighting.
- **Evidence:** Blender 5.2 local API regression and the303 normal-repair reproduction.

<a id="import-orientation"></a>
### IMPORT-01 Establish The First Imported Orientation Before Authoring Motion

- **Applies to:** image-defined flat logos and props imported from MDL, and any later animation specified in world-axis terms.
- **Visible symptom:** the source image is upright but the first imported model lies in the XY plane; a requested world-Z spin then moves through the face plane or appears to use the wrong direction/axis.
- **Root cause:** the frame-0 authoring plane, visible-front normal, upright vector, and thickness axis were not recorded before animation. Viewer and StudioMDL coordinate conventions can change presentation, while a pose-channel suffix does not name a world axis.
- **Reliable approach:** declare upright/front/thickness directions from the source image, inspect the first import in orthographic front/top/side views, and correct the rest mesh or armature orientation before creating keys or remapping animation. Apply the intended object/armature transforms, then resolve a requested world axis through the measured rest basis. For an upright logo spinning around world Z, use an XZ or YZ rest silhouette with thickness along the remaining axis, then verify one export/readback orientation audit. Treat raw MDL header bounds as compiled-coordinate evidence only; StudioMDL's root convention can make those axes look swapped, so accept final orientation from independent readback bounds and sampled images.
- **Avoid:** assuming an XY-plane import is upright, choosing `rotation_euler.z` from the request text before measuring the rest basis, correcting orientation after keys exist, or compensating twice for the StudioMDL root conversion.
- **Verify:** frame-0 source and readback front silhouettes agree, the vertical extent is along intended world Z, the thickness normal is the expected axis, a non-symmetric quarter sample reports the requested evaluated world rotation axis with the expected invariant span, and raw MDL header axes are not used as the only orientation test.
- **Evidence:** Blender 5.2 rest-basis/StudioMDL coordinate audit and full-rotation flat-logo fixture.

## Textures And Materials

<a id="tex-indexed"></a>
### TEX-01 A Legal Indexed BMP Can Still Be Visually Wrong

- **Applies to:** all compiled GoldSrc textures.
- **Visible symptom:** the MDL is dark, nearly blank, flat-colored, or unlike the Blender material despite a valid BMP header.
- **Root cause:** low-color palette quantization, duplicate color-space conversion, stale source pixels, row inversion, or judging whole-image brightness instead of the model region.
- **Reliable approach:** inspect used-index frequencies, visible color count, palette-weighted luminance, source/BMP pixel ranges, and a foreground/model-region render.
- **Avoid:** accepting a texture because it is 8-bit indexed, or rejecting an intentional dark palette by one global threshold.
- **Verify:** author render, final BMP, compiled embedded texture, and readback render agree perceptually.
- **Evidence:** indexed texture and visual-evidence unit regressions; repeated brightness failures in live builds.

<a id="tex-colorspace"></a>
### TEX-02 Set Color Space Before Writing Generated Pixels

- **Applies to:** procedurally generated Blender images and Blender-buffer fallback conversion.
- **Visible symptom:** RGB becomes black or empty while alpha remains populated; converted BMP is unexpectedly dark.
- **Root cause:** changing color space after a large `image.pixels` write can clear Blender 5.2 RGB data; a saved file is already display-encoded while generated Blender buffers require explicit interpretation.
- **Reliable approach:** set color space first and write generated pixels second. Read clean file-backed images from the saved source without another gamma transform; describe generated/dirty buffers explicitly as linear RGB with their row origin before indexed output.
- **Avoid:** changing image color space after filling pixels, applying linear-to-sRGB twice to a saved file, or leaving buffer color space implicit.
- **Verify:** inspect source pixel min/max before conversion and compare known middle gray after BMP output.
- **Evidence:** Blender 5.2 generated-image reproduction and linear-to-sRGB unit regression.

<a id="tex-stale-image"></a>
### TEX-03 Same-Path Image Datablocks Can Be Stale

- **Applies to:** long-lived MCP sessions and repeated conversion attempts.
- **Visible symptom:** rerunning conversion uses old pixels even though the file changed on disk.
- **Root cause:** `bpy.data.images` reuses an existing same-path datablock from another scene or failed attempt.
- **Reliable approach:** load the just-written source with `check_existing=False`, convert it, then remove temporary unused data.
- **Avoid:** assuming a matching filepath means the image buffer is current.
- **Verify:** compare disk timestamp/hash, loaded pixel range, and resulting palette variation.
- **Evidence:** stale same-path Blender fallback regression.

<a id="tex-masked"></a>
### TEX-04 Masked Transparency Depends On Palette Index 255

- **Applies to:** masked/cutout textures.
- **Visible symptom:** transparent regions render blue or opaque, or visible colors disappear.
- **Root cause:** GoldSrc masked mode uses palette index 255, not arbitrary alpha semantics.
- **Reliable approach:** reserve index 255 as `(0, 0, 255)`, map transparent pixels to it, and keep visible pixels in indices 0-254. Let non-Masked textures use all 256 entries.
- **Avoid:** preserving RGBA alpha without constructing the required indexed palette.
- **Verify:** inspect used indices, palette entry 255, texture flag `masked`, and readback edges.
- **Evidence:** indexed BMP masked-palette regression and special-surface fixture.

<a id="tex-bake-engine"></a>
### TEX-05 Blender 5.2 Texture Baking Requires Cycles

- **Applies to:** high-to-low diffuse/albedo baking.
- **Visible symptom:** `bpy.ops.object.bake` fails or produces no useful target while EEVEE is active.
- **Root cause:** Blender 5.2 does not support this bake operation through EEVEE.
- **Reliable approach:** switch to Cycles, make the low target image node active, select high then low, and disable direct/indirect passes for albedo.
- **Avoid:** debugging cages and UVs before confirming the render engine and active target.
- **Verify:** inspect baked source image before indexed conversion and compare projection landmarks.
- **Evidence:** the303 research equivalent and Blender 5.2 bake reproduction.

<a id="tex-bake-active-render-uv"></a>
### TEX-08 Bake UV And Export UV Must Be The Same Layer

- **Applies to:** procedural/PBR color bakes that feed an explicit GoldSrc UV and any Blender 5.2 mesh with multiple UV layers.
- **Visible symptom:** the source object and frozen GoldSrc object share the same geometry, but the baked texture places terrain/material regions on the wrong surfaces.
- **Root cause:** `bpy.ops.object.bake` can use the UV layer marked `active_render`, while EXPORT writes the evaluated `uv_layers.active` layer. Changing only `active_index` or only a shader `ShaderNodeUVMap` does not synchronize those contracts.
- **Reliable approach:** set the intended layer as `mesh.uv_layers.active`, set exactly that layer's `active_render=True` and every other layer's flag to `False`, declare `texture_bake.uv_layer` with `require_active_render=true`, bake, reload the file-backed image, and run PREFLIGHT before EXPORT.
- **Avoid:** assuming the layer visible in the UV Editor is the bake layer, relying on `automap`/`UVMap` as a stale active-render default, or accepting an atlas because it is non-empty.
- **Verify:** PREFLIGHT reports raw and evaluated active/active-render names; a declared mismatch fails before SMD generation. Compare distinctive author/material landmarks and the independent MDL readback after re-baking.
- **Evidence:** Blender 5.2 TerrainBase reproduction; the toolchain `texture_bake_uv_guard` and regression tests.

<a id="tex-bake-unlit"></a>
### TEX-09 A Color-Only Bake Can Still Look Shadowed

- **Applies to:** procedural/PBR atlases requested without baked lighting, ambient shadows, or material shading.
- **Visible symptom:** `DIFFUSE` with `COLOR` only and direct/indirect disabled succeeds, but the exported atlas still contains dark creases or broad shadow-like regions.
- **Root cause:** Cycles removed ray lighting, but the material's color graph still contains AO, dark albedo, color ramps, or shader-closure mixing. Those values are material color, not a scene-light pass.
- **Reliable approach:** use the color-only result as a diagnostic, then duplicate the source material and replace Principled/Diffuse closures with Strength-1 Emission driven by Base Color. Bake `EMIT` to a separate file-backed image on the same GoldSrc UV, preserve alpha deliberately, and keep the temporary graph out of the final author/export material.
- **Avoid:** adding Emission to the production material, removing all normal detail when only baked lighting was requested, or setting a GoldSrc `fullbright` flag for a Half-Life/Counter-Strike contract.
- **Verify:** compare the color-only and Emission atlases by pixel error and visible landmarks, inspect the final indexed BMP, then run the independent MDL readback. Readback lighting is a separate validator and must not be mistaken for baked texture shadow.
- **Evidence:** Blender 5.2 Cycles `bake_setup_pass` selects `PASS_DIFFUSE_COLOR` when only color is requested, while the live TerrainBase reproduction required a temporary Emission bake to remove the remaining material darkening.

<a id="smd-material-token"></a>
### TEX-06 SMD Material Token Must Match The Compiled BMP

- **Applies to:** every textured reference SMD.
- **Visible symptom:** StudioMDL reports a missing texture, embeds an unintended texture, or readback has the wrong material.
- **Root cause:** token, filename, and contract texture name differ, often by extension or stale Blender material naming.
- **Reliable approach:** use the actual final `.bmp` filename as the SMD token and contract texture name.
- **Avoid:** exporting Blender material labels or source PNG names as GoldSrc tokens.
- **Verify:** parse SMD triangle tokens, QC texture references, and MDL embedded texture names.
- **Evidence:** SMD material-token contract regressions.

<a id="tex-export-fidelity"></a>
### TEX-07 EXPORT Must Preserve Texture Fidelity And Orientation

- **Applies to:** file-backed, generated, packed, or dirty Blender images exported to indexed BMP.
- **Visible symptom:** smooth gradients collapse to a handful of bands, white becomes yellow or blue, the image is upside down, or the MDL differs from the author texture before lighting is involved.
- **Root cause:** EXPORT bypassed the high-quality quantizer, reserved index 255 for a non-Masked texture, applied gamma to already encoded file pixels, or treated Blender's bottom-left buffer rows as top-left.
- **Reliable approach:** use the saved file through Pillow median-cut when available. For generated or dirty buffers, declare input color space and row origin explicitly; reserve 255 only for Masked output.
- **Avoid:** calling a fixed 3-3-2 fallback from the normal export path, clamping non-Masked index 255 to 254, or inferring orientation from a symmetric texture.
- **Verify:** require `EXPORT` to report conversion method, source/output color counts, mean and maximum channel error, and direct-versus-flipped error; compare BMP and MDL index/palette bytes, then inspect independent readback.
- **Evidence:** Toolchain `1.3.2` file/RGBA conversion regressions and Blender 5.2 five-stage gradient fixture.

## SMD, QC, And Bounds

<a id="smd-bounds"></a>
### SMD-01 Zero Or Unscaled Bounds Hide Real Geometry

- **Applies to:** all models and especially animated/root-motion sequences.
- **Visible symptom:** culling, collision bounds, or inspection vectors do not contain the exported model.
- **Root cause:** universal zero bounds or bounds measured before applying contract scale.
- **Reliable approach:** calculate `$bbox` and `$cbox` from exported SMD bounds multiplied by `$scale`, then parse the compiled MDL header vectors.
- **Avoid:** trusting compiler defaults or copying one model's bounds.
- **Verify:** QC and MDL vectors match and contain representative animation extrema.
- **Evidence:** compiler comparison fixtures and documented Sven cbox caveat.

<a id="compiler-success"></a>
### SMD-02 Compiler Success Is Not Model Acceptance

- **Applies to:** every delivery.
- **Visible symptom:** the compiler returns success but animation, bodygroups, skins, flags, brightness, or bounds are wrong.
- **Root cause:** StudioMDL validates only part of the intended contract.
- **Reliable approach:** inspect the MDL binary independently and reconstruct it through the SourceIO-derived reader, then compare visual frames.
- **Avoid:** treating a produced `.mdl` as proof of correctness.
- **Verify:** all five Extension stages pass and author/readback views agree.
- **Evidence:** five-stage fixtures and the303 coverage matrix.

<a id="submodel-budget"></a>
### SMD-03 The 2048 Limits Apply To Each Compiled Submodel In Both Profiles

- **Applies to:** imported component transplants, merged viewmodels, bodygroups, and dense props compiled by the bundled StudioMDL.
- **Visible symptom:** StudioMDL reports too many vertices or normals after meshes that compiled separately are merged; switching the contract to Sven does not fix it.
- **Root cause:** each compiled `mstudiomodel_t` retains fixed 2048-vertex and 2048-normal arrays for both supported profiles. The 20000-triangle ceiling is separate.
- **Reliable approach:** inspect each reference SMD independently and split along natural component, material, or bone boundaries into multiple always-present `$body` entries.
- **Avoid:** assuming Sven removes the fixed arrays, counting Blender loop corners as the compiled total, or mechanically cutting triangles without regard to seams and ownership.
- **Verify:** report compiled `(bone, position)` vertices, `(bone, normal)` normals, and triangles per source SMD before StudioMDL; inspect each compiled bodypart afterward.
- **Evidence:** bundled compiler failure/recompile regression, HL SDK `MAXSTUDIOVERTS`, and the303 per-SMD guidance.

<a id="large-texture-atlas"></a>
### SMD-04 A 2K Author Atlas Is A Tile Set, Not A 2K MDL Texture

- **Applies to:** BLBH-style lightmap/terrain atlases and any author image larger than `512x512`.
- **Visible symptom:** StudioMDL rejects a texture, the model shows one repeated quadrant, or a triangle at a tile edge samples the wrong material.
- **Root cause:** GoldSrc texture records use the conservative `512x512` ceiling; an atlas was declared as one texture or a crossing triangle was assigned to one tile without geometric clipping.
- **Reliable approach:** declare the logical atlas dimensions, keep the full `0..1` UV mapping, clip/retriangulate crossing triangles, crop only the referenced `512x512` indexed tiles, and remap each generated triangle to local tile UVs. Preserve each input triangle's 3D area and compare full vertex attributes when removing clipped-corner duplicates; equal UVs are not enough because stacked islands can have different spatial vertices. Keep each compiled material token and BMP filename identical.
- **Avoid:** embedding a `2048x2048` BMP directly, merely scaling UVs, adding hidden anchor triangles to retain empty tiles, manually listing every possible tile in QC, or relying on a four-line SMD slicer. More than 64 possible tiles requires multiple MDLs and is outside the current one-contract path.
- **Verify:** inspect `export_plan.json` version 2 `declared`, `compiled`, and `omitted_unused_large_tiles`, area-preservation evidence, compiled tile BMP dimensions/palettes, SMD tile tokens and local UV bounds, compiled MDL texture count/dimensions, and readback landmarks at tile boundaries. The MDL texture count must match `compiled`, not the atlas's full tile grid.
- **Evidence:** local BLBH source audit, Toolchain atlas clipping tests, indexed tile orientation regression, and MDL v10 inspection.

<a id="smd-budget-split"></a>
### SMD-05 An Over-Budget Reference Needs Triangle-Preserving Body Parts

- **Applies to:** reference SMDs exceeding compiled vertex, normal, or triangle budgets.
- **Visible symptom:** a source SMD parses correctly but StudioMDL fails after compilation, or a raw line slice compiles with missing material/UV/skeleton context.
- **Root cause:** raw triangle count does not equal compiled position/normal arrays, and line slicing does not update QC body declarations or validate bones/weights.
- **Reliable approach:** count unique `(bone, position)`, `(bone, normal)`, and triangles while traversing triangles; start a new part before any budget would be exceeded; write complete nodes/skeleton/material context for every part; compile parts as always-present `$body` entries.
- **Avoid:** counting only text lines, splitting bodygroup choices silently, or using `smdcutpy.py` as the production exporter.
- **Verify:** every generated part passes SMD validation and its budget report, `export_plan.json` lists relative part paths, QC contains the matching `$body` entries, and INSPECT reports the expected bodyparts.
- **Evidence:** gchimp `maybe_split_smd` comparison, Toolchain budget-split unit tests, and compiled MDL bodypart inspection.

<a id="custom-build-preflight"></a>
### SMD-06 Custom Build Scripts Do Not Bypass Export Rules

- **Applies to:** one-off transplant, conversion, or repair scripts that write SMD/QC and invoke StudioMDL directly.
- **Visible symptom:** a custom script reaches the compiler quickly but fails on geometry budget, skeleton mismatch, wrong material token, or readback animation.
- **Root cause:** the script skipped the deterministic checks normally performed by the contract-driven export stages.
- **Reliable approach:** run SMD structure, per-submodel budget, bone hierarchy, single-weight, material-token, indexed-BMP, compile, inspect, and independent readback checks on custom outputs.
- **Avoid:** treating bespoke code or a successful subprocess exit as an alternate acceptance path.
- **Verify:** compare the same structural and visual evidence required by the five public stages.
- **Evidence:** knife component transplant regression and compiler/readback fixtures.

<a id="bmp-errors"></a>
### QC-01 StudioMDL BMP Errors Identify Distinct Encodings

- **Applies to:** StudioMDL failures `-3000`, `-4`, and `-5` while loading a texture.
- **Visible symptom:** compilation stops before model output and reports one of those numeric texture errors.
- **Root cause:** `-3000` is an OS/2/short DIB header, `-4` is a non-8-bit indexed BMP, and `-5` is an RLE-compressed BMP.
- **Reliable approach:** rewrite the final texture as Windows BMP with a 40-byte-or-larger DIB, 8-bit indexed pixels, 256 palette entries, and no compression.
- **Avoid:** changing QC paths or geometry before inspecting the BMP header; the OS/2 code is `-3000`, not `-3`.
- **Verify:** run the indexed-BMP validator on the exact compiled file and compile again from a clean downstream stage.
- **Evidence:** the303 `gold_mdl_fix`; OS/2, bit-depth, compression, and palette regression checks.

<a id="runtime-upload"></a>
### QC-02 Runtime Texture Upload Has An Eight-Pixel Divisibility Check

- **Applies to:** `GL_Upload16:s&3` fatal errors when a compiled model is loaded.
- **Visible symptom:** StudioMDL may produce an MDL, but the engine terminates while uploading its texture.
- **Root cause:** the runtime rejects dimensions that are not divisible by 8; the conservative authoring path is stricter and uses multiples of 16.
- **Reliable approach:** resize both axes to legal multiples of 16 and update every skin-family slot consistently.
- **Avoid:** treating compiler success as proof that runtime texture dimensions are legal.
- **Verify:** inspect embedded MDL texture dimensions and load the model in the named target game/mod before claiming in-game validation.
- **Evidence:** the303 `gold_mdl_fix`; contract and MDL texture-dimension inspection.

<a id="remap-cache"></a>
### QC-03 REMAP And External Textures Can Trigger Cache_TryAlloc

- **Applies to:** player/remap textures combined with external texture/submodel arrangements.
- **Visible symptom:** the game terminates with `Cache_TryAlloc` while loading or switching the model.
- **Root cause:** a known legacy interaction between REMAP data and `$externaltextures`-style model layout.
- **Reliable approach:** compile a self-contained MDL with embedded textures and validate remap behavior against its actual target-game baseline.
- **Avoid:** retaining `$externaltextures` to reduce file size or assuming the crash is a palette-brightness problem.
- **Verify:** inspect that the delivery contains one embedded-texture MDL, then test remap loading in the target game.
- **Evidence:** the303 `gold_mdl_fix`; self-contained delivery and embedded-texture inspections.

<a id="obsolete-qc"></a>
### QC-04 Obsolete QC Directives Can Crash The Runtime

- **Applies to:** inherited or decompiled QC containing `$sequencegroupsize` or `$externaltextures`.
- **Visible symptom:** the model compiles but the game crashes with little or no useful diagnostic.
- **Root cause:** obsolete external sequence/texture layouts are not reliably supported by the intended modern self-contained pipeline.
- **Reliable approach:** remove those directives, keep sequences embedded unless an explicit external-sequence limitation is proven, and embed textures in the MDL.
- **Avoid:** copying legacy QC wholesale because StudioMDL accepts its syntax.
- **Verify:** inspect sequence groups and embedded textures in the final MDL and audit the delivery whitelist.
- **Evidence:** the303 `gold_mdl_fix`; external-group limitation and release regressions.

## Animation

<a id="action-channelbags"></a>
### ANIM-01 Blender 5.2 Actions May Have No Legacy `action.fcurves`

- **Applies to:** motion ownership audits, export, remapping, and playback checks.
- **Visible symptom:** a script reports no keys even though playback moves, or fails when reading `action.fcurves`.
- **Root cause:** Blender 5.2 stores curves in layers, strips, channelbags, and slots.
- **Reliable approach:** inspect channelbags as well as legacy direct curves and confirm the armature's bound Action/slot.
- **Avoid:** interpreting a missing direct `fcurves` attribute as an empty Action.
- **Verify:** enumerate keyed data paths and sample evaluated poses across the frame range.
- **Evidence:** Blender 5.2 Action compatibility regressions.

<a id="bone-space"></a>
### ANIM-02 World Delta Is Not Pose-Bone Local Transform

- **Applies to:** rigid-body transfer, parented bones, rotated rest bones, and retargeting.
- **Visible symptom:** pieces orbit or rotate on the wrong axis; a requested Z-axis spin appears in the ZY or XZ plane; motion matches only when rest bones happen to align with world axes.
- **Root cause:** pose-channel suffixes name bone-local axes. Blender edit-bone local `+Y` is head-to-tail, while local `X/Z` depend on roll. Assigning a world delta or choosing `rotation_euler.z` by name ignores armature, parent, and rest spaces. StudioMDL's separate root `+90 degree Z` convention can then obscure the original mistake without causing it.
- **Reliable approach:** follow [rotation-axis-space](workflow-animation-characters.md#rotation-axis-space): transform the requested world axis through the measured rest basis, or convert a complete target pose matrix with `bone.convert_local_to_pose(..., invert=True)`. Let StudioMDL apply its one root conversion; do not swap child channels manually.
- **Avoid:** assuming local Z equals world Z, copying a channel choice between bones with different directions/roll, compensating twice for StudioMDL, or validating against the same local channel values just assigned.
- **Verify:** at a non-symmetric quarter sample, derive the evaluated world rotation axis from `Rq @ R0.inverted()`, check that the model span parallel to that axis remains invariant, then repeat on independent MDL readback. Compare weighted vertices and SMD/MDL matrices; start/end closure alone proves no axis.
- **Evidence:** Blender 5.2 bone-basis API; Toolchain SMD exporter and StudioMDL root-convention audit; XiangXtreme 128-frame regression where local Z produced compiled X rotation and local Y correctly produced compiled Z rotation.

<a id="animation-bind-pose"></a>
### ANIM-03 Animation SMD Nodes Do Not Define A Bind Pose

- **Applies to:** imported animation SMDs, viewmodel transplants, retargeting, and MDL Action reconstruction.
- **Visible symptom:** node names and hierarchy match, but binding the Action to the reference armature makes children explode, orbit, or translate far from the model.
- **Root cause:** the animation SMD's first frame is animation data, not a reliable rest skeleton. Building an armature from it changes the basis used by every local channel.
- **Reliable approach:** provide a reference SMD or explicit target armature, construct rest bones directly from its global rest matrices, and convert every animation pose through target rest/parent pose space.
- **Avoid:** creating rest bones from animation frame 0 or using node-table equality as portability proof.
- **Verify:** use a non-axis-aligned three-level hierarchy whose first animation frame differs from rest; compare five-point global bone matrices and evaluated weighted vertices.
- **Evidence:** Blender 5.2 `convert_local_to_pose` regression and real 38-bone viewmodel readback.

<a id="preserve-target-animation"></a>
### ANIM-04 Unedited Target Motion Should Stay In Its Trusted Animation SMD

- **Applies to:** replacing a weapon, hand, garment, or prop while preserving the target model's actions.
- **Visible symptom:** the visible replacement is correct, but previously good target animations gain small drift or structural differences after an unnecessary Blender round trip.
- **Root cause:** trusted animation channels were imported and regenerated even though only reference geometry changed.
- **Reliable approach:** keep target animation SMDs and sequence metadata unchanged; modify only reference SMD/body/QC/texture surfaces. Bind and rebake only animations the request actually changes.
- **Avoid:** round-tripping every sequence through Blender for convenience.
- **Verify:** compare target and final sequence order, FPS, frame counts, local channels or compiled global matrices, events, and linear movement.
- **Evidence:** viewmodel transplant regression and MDL decompile/recompile comparison.

<a id="playback-start"></a>
### ANIM-05 A Valid Action Can Still Produce No Spacebar Playback

- **Applies to:** every animated Blend checkpoint and MDL readback Blend.
- **Visible symptom:** pressing space appears static even though an Action exists.
- **Root cause:** Action or slot is unbound, scene range misses keys, current frame is at the end, or the file was saved away from sequence start.
- **Reliable approach:** bind Action and slot, set scene start/end and FPS, frame-set the Action start, update the view layer, then save.
- **Avoid:** checking only that `bpy.data.actions` is nonempty.
- **Verify:** reopen the Blend and sample five playback frames before delivery.
- **Evidence:** Blender preflight playback regression and readback fixture.

<a id="loop-endpoint"></a>
### ANIM-06 Loops Need A Duplicate Endpoint And Observable Intermediate Motion

- **Applies to:** continuous spins, cyclic mechanisms, locomotion loops, and any loop whose start/end orientation is equivalent.
- **Visible symptom:** the loop snaps at the seam, a full rotation becomes static or reverses, or INSPECT reports a rotation mismatch even though the viewport endpoints look equivalent.
- **Root cause:** StudioMDL treats the loop's final compiled pose as the seam back to its first pose; quaternion shortest-path interpolation can erase a rotation described only by equivalent endpoints, SMD time labels need not equal compiled frame indices, and equivalent Euler triples can differ per channel.
- **Reliable approach:** key observable intermediate quarter poses in the intended direction, export a final seam pose whose local matrices duplicate the first pose, compare SMD poses to compiled frames in declaration order, and measure rotation with matrices or quaternion angle.
- **Avoid:** giving a loop a distinct final pose, relying on equivalent `0`/`360` endpoints without proven interpolation and intermediate samples, assuming SMD frames start at zero, or loosening per-axis Euler tolerances to hide an equivalent representation.
- **Verify:** first/final local matrices match, quarter samples show the complete directed motion, EXPORT reports a passing loop-endpoint audit, and INSPECT plus five-point readback preserve the motion.
- **Evidence:** full-rotation Blender 5.2 fixture and Toolchain `1.3.1` nonzero-frame/matrix regressions.

<a id="player-compatibility"></a>
### PLAYER-01 Player Animation Tables Are Baseline Contracts

- **Applies to:** Half-Life/Counter-Strike player-model repair, reskinning, retargeting, or added accessories.
- **Visible symptom:** weapon aim, movement, death, or crouch animations map to the wrong motion in game even though each sequence plays in a viewer.
- **Root cause:** sequence count/order, FPS, frame ceiling, or the baseline bone/hitbox/body structure changed; game code relies on that structure.
- **Reliable approach:** compare the candidate with the exact player baseline. Preserve sequence names/count/order and FPS, keep candidate frames at or below baseline, preserve the ordered baseline bone prefix and hitboxes, and append bones only below baseline leaves.
- **Avoid:** inserting bones, adding an extra sequence, expanding a sequence because the animation looks smoother, or claiming full blend-sequence authoring through the single-source contract path.
- **Verify:** run `compatibility.role = "player"` during INSPECT and review every reported table; separately validate the `164x200` indexed portrait.
- **Evidence:** the303 `gold_player_mdl`; 77-sequence SDK Barney baseline and Extension compatibility regressions.

<a id="npc-root-motion"></a>
### NPC-01 Recompiled Linear Movement Can Drift

- **Applies to:** NPC walk/run sequences using `LX` or related root-motion extraction after decompile/remap cycles.
- **Visible symptom:** the NPC walks in place, slides, or moves at a different speed while the pose animation appears correct.
- **Root cause:** root displacement, motion token, or compiled linear-movement metadata changed; repeated decompile/recompile passes can quantize or degrade the value.
- **Reliable approach:** retain the trusted baseline MDL/SMD, preserve its sequence prefix, append new sequences at the QC end, and compare linear movement plus root transforms directly.
- **Avoid:** repairing the value from another decompiled copy or inserting a new sequence among baseline indices.
- **Verify:** inspect the compiled sequence's `linear_movement`, motion type, frame count, and root displacement against the baseline before game testing.
- **Evidence:** the303 `gold_mdl_dynpc`; NPC compatibility metadata regression.

## Physics

<a id="physics-lifecycle"></a>
### PHYS-01 Bullet Handles Outlive Blender Datablock Assumptions

- **Applies to:** reruns, teardown, cache resets, and crash recovery.
- **Visible symptom:** Blender crashes during evaluation or cleanup, often after rebuilding a rigid-body scene.
- **Root cause:** stale world membership, invalid constraint/body handles, cache invalidation, or dependency-graph evaluation after Bullet-owned data was removed.
- **Reliable approach:** create complete membership before evaluation; tear down constraints, bodies, world, objects, then data; inspect the crash log and matching Blender/Bullet source first.
- **Avoid:** guessing friction/substeps as the first crash fix or deleting arbitrary datablocks while evaluating.
- **Verify:** repeat scene creation, bake, teardown, and recreation in one Blender session.
- **Evidence:** Blender 5.2 crash/source investigation and lifecycle regression guidance.

<a id="contact-gating"></a>
### PHYS-02 Contact Reports Must Not Drive A Second Simulation

- **Applies to:** fracture, staged collapse, impact release, and chain reactions.
- **Visible symptom:** pieces remain intact before contact only because a script enables or moves them after detecting the contact frame.
- **Root cause:** validation data was reused as an authoring gate, replacing solver causality.
- **Reliable approach:** express frame-0 state with bodies, constraints, supports, sleeping, and collision collections; run one complete solve and use contact time only for acceptance.
- **Avoid:** contact-time keyframes, collision toggles, geometry creation, scripted release, or rebaking with the observed frame.
- **Verify:** audit non-driver Actions and prove all passive motion came from evaluated Rigid Body World matrices.
- **Evidence:** event-chain contract and motion-ownership regressions.

<a id="collision-proxies"></a>
### PHYS-03 Render Geometry Is Often A Bad Collision Shape

- **Applies to:** concave slopes, flush fragments, irregular rocks, barrels, ropes, and narrow channels.
- **Visible symptom:** tunneling, false early contact, jitter, hovering, or a response in the wrong direction.
- **Root cause:** box approximations change contact normals, concave meshes are unstable, or perfectly flush surfaces start overlapped.
- **Reliable approach:** choose box, convex hull, cylinder, or mesh per physical role; use slightly inset hidden proxies when exact render contact causes overlap.
- **Avoid:** forcing one collision shape on all objects or fixing visible penetration only by moving render geometry.
- **Verify:** inspect actual contact pairs, proxy clearances, penetration samples, and post-contact direction.
- **Evidence:** mixed-object, rockfall, bridge, and fracture stress tests.

<a id="settlement"></a>
### PHYS-04 A Fixed Short End Frame Is Not Stability

- **Applies to:** debris, rolling objects, swinging chains, and long collision chains.
- **Visible symptom:** animation ends while pieces still move, or a very long fixed bake wastes frames without proving settlement.
- **Root cause:** no activity gate or continuous stillness window, or only the final frame was inspected.
- **Reliable approach:** use a generous maximum, last-event activity gate, translation/rotation thresholds, consecutive stillness, hold frames, and receiver bounds.
- **Avoid:** lowering solver quality or truncating motion to satisfy animation size.
- **Verify:** report detection frame, stable window, most-active tail objects, kinematic end state, unwoken bodies, and receiver escapes.
- **Evidence:** adaptive rigid-body API and settlement regressions.

<a id="frame-count-semantics"></a>
### ANIM-07 Blender Frame Ranges Are Inclusive

- **Applies to:** requested cycle lengths, duplicate loop endpoints, viewport animation renders, and GoldSrc sequence frame counts.
- **Visible symptom:** a request for a 64-frame period is implemented as `1..64` with a duplicate endpoint, but the measured period is 63 frame intervals; or `render.opengl(animation=True)` omits the intended endpoint.
- **Root cause:** Blender's integer scene/playback range is inclusive, while a cycle's duration is the difference between endpoint frame numbers. The OpenGL animation operator renders from `playback_start()` through `playback_end()` inclusive. `Action.use_frame_range` describes an intended range and does not change evaluation; `Action.use_cyclic` describes a cycle but does not automatically loop it.
- **Reliable approach:** define whether the user means samples or intervals. Use `1..64` for 64 integer samples with a duplicate seam and 63 intervals. Use `1..65` (or `0..64`) for 64 intervals with a duplicate seam. Set the scene playback range and the Action range deliberately, then verify the exported SMD/MDL frame count separately.
- **Avoid:** silently treating "64 frames" as both 64 samples and 64 intervals, relying on `Action.use_cyclic` alone, or dropping the duplicate seam to hide the ambiguity.
- **Verify:** report the start/end frame numbers, sample count, interval count, endpoint matrix delta, and compiled sequence frame count.

## Author Preview

<a id="viewport-material-preview"></a>
### VIEW-01 Material Preview Evidence Must Not Mutate The Asset Material

- **Applies to:** author-side stills requested to match Blender's Material Preview, especially flat logos and props whose ordinary scene render is black because no lights or world illumination are configured.
- **Visible symptom:** `RENDER`/EEVEE output is black or dark while Material Preview looks correct, or adding Emission makes the preview readable but changes normal renders or MDL readback.
- **Root cause:** `bpy.ops.render.render(write_still=True)` follows the scene render engine, lights, and world; Material Preview is a `VIEW_3D` viewport shading path that can use a studio light when scene lights and the scene world are disabled.
- **Reliable approach:** use a valid `VIEW_3D` context, preferably with `bpy.context.temp_override(window=..., area=..., region=..., space_data=...)`, then call `bpy.ops.render.opengl(write_still=True, view_context=True)` after setting `space.shading.type = 'MATERIAL'`, `use_scene_lights = False`, and `use_scene_world = False`. `view_context=True` uses the current `RegionView3D` and does not enter camera view; switch to camera view explicitly when required. The operator's off-screen buffer uses the scene render resolution and percentage, not the viewport dimensions. If no valid `VIEW_3D` exists, Blender disables the view-context path and uses the scene camera, which must exist. In a Workbench scene, `OB_MATERIAL` is handled by Workbench using scene display shading rather than the EEVEE studio-light lookdev path. Temporarily set that area's `overlay.show_overlays = False` only for the capture, restore the saved value in a `finally` block, and restore any temporary render settings before saving the Blend. Set `scene.render.film_transparent = True` when a transparent source is needed, then composite a black background outside the material. Keep the asset material nodes unchanged and retain the independent toolchain renderer for `ROUNDTRIP`.
- **Avoid:** adding Emission, making diffuse materials self-lit, adding ad hoc lights, leaving `show_overlays = False` in the saved Blend, or treating a viewport PNG as deterministic MDL readback evidence.
- **Verify:** compare the capture with the visible Material Preview, inspect alpha and foreground coverage, check the intended frame, reopen the saved Blend to confirm the grid and axes are visible, and separately inspect the normal scene render when that render path is part of delivery.
- **Evidence:** Blender 5.2 source inspection of `render_opengl.cc`, `view3d_draw.cc`, `workbench_state.cc`, `eevee_instance.hh`, `rna_space.cc`, and `DNA_view3d_types.h`; live RNA inspection and a temporary `render.opengl` PNG trigger with restored state.

## Session And Readback

<a id="namespace-collisions"></a>
### SESSION-01 Long-Lived Blender Sessions Accumulate `.001` Data

- **Applies to:** MCP reruns and asset rebuilds.
- **Visible symptom:** export resolves a stale mesh, Action, material, or armature; new names gain numeric suffixes.
- **Root cause:** previous scene attempts left datablocks in the shared session.
- **Reliable approach:** define an exact asset-owned namespace, release rigid-body ownership, purge exact names and numeric suffixes, rebuild, then assert the expected namespace.
- **Avoid:** deleting unrelated user data or accepting whichever suffix Blender generated.
- **Verify:** contract object/Action names resolve exactly before preflight.
- **Evidence:** namespace purge/assertion unit regressions.

<a id="readback-collisions"></a>
### READBACK-01 Repeated MDL Readback Must Be Idempotent

- **Applies to:** independent roundtrip validation.
- **Visible symptom:** a second readback creates `.001` meshes/Actions or starts playback on the wrong frame.
- **Root cause:** incomplete readback cleanup, stale namespace, or unsaved Action binding/range state.
- **Reliable approach:** clean only the readback-owned namespace, reconstruct all embedded Actions and skin families, bind the selected Action, set the start frame, and save.
- **Avoid:** reusing the export parser as the independent reader or accepting suffix collisions as cosmetic.
- **Verify:** use `export_selected_static(..., assurance="strict")`; its internal pipeline performs two isolated ROUNDTRIP runs and compares names, structure, weighted vertices, decoded `pixel_sha256`, and contact-sheet pixel hashes. Do not manually repeat a passing stage. PNG byte hashes identify artifacts but are not a pixel-equivalence test.
- **Evidence:** repeated SourceIO-derived roundtrip fixture.

<a id="blank-readback"></a>
### READBACK-02 A Passing Stage With Zero Foreground Is Not Visual Evidence

- **Applies to:** every animated MDL readback, especially flat, wide, very large, or axis-swapped models.
- **Visible symptom:** ROUNDTRIP writes five distinct PNG byte hashes but every decoded frame is identical, black, or empty, often leading to repeated brightness edits that do not reveal the model.
- **Root cause:** PNG metadata/compression can change file bytes without changing pixels; separately, a fixed diagonal camera can look along the model's thickness, a bounds-scaled camera can exceed Blender's default far clip, and whole-image luminance cannot reliably distinguish model pixels from the background.
- **Reliable approach:** use decoded RGBA `pixel_sha256` for frame-variation checks, choose the front view from readback bounds, frame the thinnest axis orthographically, scale near/far clipping with model span, count foreground from render alpha, and fail when all generated previews contain zero foreground pixels.
- **Avoid:** treating PNG byte hashes as motion evidence, repeatedly brightening textures or world lighting before checking camera axis/clipping, or accepting phase status and hashes without opening the images.
- **Verify:** inspect reported bounds, view axis, camera clip range, foreground fractions, and the actual start/quarter/mid/three-quarter/end PNGs.
- **Evidence:** `64x512x128` full-rotation readback fixture and Toolchain `1.3.1` blank-preview regression.

<a id="contact-sheet-overview"></a>
### READBACK-03 Contact Sheets Are An Index, Not Replacement Evidence

- **Applies to:** multi-frame Action review, author/readback comparisons, and physical event chains.
- **Visible symptom:** a very wide strip is scaled until poses and labels are unreadable, or a compact overview appears acceptable while a source frame still contains a seam, penetration, brightness, or framing defect.
- **Root cause:** layout optimized for sequence scanning cannot preserve every source pixel, and equal-time samples do not necessarily represent contact-driven event order.
- **Reliable approach:** use at most three columns for the ordinary five-point overview, put Action/frame or event labels in caption bands outside images, preserve every original PNG with both artifact and decoded-pixel hashes, and retain a JSON frame-to-cell mapping.
- **Avoid:** one ultra-wide row, drawing labels over the model, deleting source stills after composition, or labeling physical samples as quarters when they are event-selected.
- **Verify:** inspect the contact sheet first, then open suspicious source frames at full resolution; confirm cell paths, byte hashes, `source_pixel_sha256`, labels, and image/caption rectangles in the layout JSON. Repeat `ROUNDTRIP` and reject stale sheets.
- **Evidence:** Toolchain `1.4.0` compositor unit tests, clean Blender 5.2 five-stage fixture, repeated roundtrip, and live official Blender MCP API regression.
