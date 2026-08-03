# Import, Repair, And Troubleshooting

## Establish The Baseline

1. Preserve the original file and create a new external artifact directory.
2. When starting from MDL, call `decompile_mdl(mdl_path, artifacts_dir)` to recover reference SMDs, animation SMDs, exact indexed BMP data, QC, and a manifest. Treat external sequence groups as an explicit unsupported case rather than accepting a partial result.
3. Record skeleton, bodyparts, skin families, textures/flags, sequences, frame ranges, controllers, attachments, hitboxes, bounds, and external sequence groups before editing.
4. Render representative baseline views and animation frames. Do not diagnose a visual defect from binary structure alone.
5. Establish the frame-0 orientation before editing animation: record the source image's upright direction, visible-front plane, and thickness axis; inspect the first imported MDL from orthographic front/top/side views. If a vertically presented logo arrives lying in the XY plane, correct the rest mesh/armature orientation and apply the intended transforms before creating or remapping keys. For a logo that should spin around world Z while remaining upright, its rest silhouette must occupy an XZ or YZ plane, with thickness along the remaining axis. Treat raw MDL header bounds as compiled-coordinate evidence only; StudioMDL's root convention can make those axes look swapped, so accept final orientation from independent readback bounds and sampled images.

## Classify Before Editing

| Symptom | First owner to inspect |
|---|---|
| Wrong shape, holes, shading, silhouette | Geometry, winding, normals, modifiers |
| Shifted or stretched texture | UV coordinates, dimensions, material token |
| Dark, blank, or wrong-color texture | Image color space, indexed conversion, palette, MDL flag |
| Exploding or rigid animation | Weights, hierarchy, rest pose, parent/local transforms |
| No spacebar animation | Action binding/slot, scene range, current frame, saved state |
| Compiles but reads back differently | SMD/QC output, MDL binary, independent reader |
| Physics moves early or penetrates | Initial overlap, proxy, support, constraint, world lifecycle |
| `.001` objects or stale materials | Long-lived Blender namespace contamination |

Change only the owning layer first. Keep a passing baseline report when revising an already accepted asset.

## Repair Geometry And UVs

- Compare before/after loop triangles, positions, normals, UVs, material slots, and bounds.
- Apply the legacy `1.007` UV compensation or a manual island shift only when the source defect proves it is needed.
- Build custom normals in polygon-loop order in Blender 5.2; `MeshLoop` has no `polygon_index`.
- Preserve intentional reverse-wound geometry for double-sided planes.

## Repair Skeleton And Animation

- Detect zero-weight and multi-weight vertices, assign the intended single owner at `1.0`, and remove competing memberships.
- Verify bone parent order and rest pose before editing keys.
- An animation SMD carries local animation channels, not a trustworthy bind pose. Matching node names and parents do not make its Action portable. Bind it with `import_smd_animation(..., reference_smd=...)` or an explicit target armature.
- Inspect Blender 5.2 Action layers, strips, channelbags, slots, and the armature's actual bound Action.
- Bake constraints/remapping to pose keys, restore the start frame, and compare global matrices rather than only local channels.

For part transplants, assign ownership before editing:

- The source model owns only the visible replacement component and its texture.
- The target model owns the skeleton, trusted animation SMDs, sequence metadata, bodypart layout, and unrelated geometry.
- Keep target animation SMDs byte-for-byte when their motion is not being edited. Do not import and re-export them through Blender merely to rebuild QC.
- If animation editing is required, bind against the target reference rest, bake the result, then compare five-point global bone matrices and evaluated weighted vertices.
- Validate each final reference SMD separately. A component merge that exceeds 2048 compiled vertices or 2048 compiled normals must be split along natural component, material, or bone boundaries into multiple always-present `$body` entries.

## Repair Textures And Compile Output

- Rebuild the final indexed BMP from the saved file source when available, reload it into the Blender material, and verify dimensions, 256-entry palette, used indices, Masked-only index 255, visible luminance, row orientation, and SMD token.
- Inspect the `EXPORT` conversion method and fidelity report. A legal BMP with very few colors, a direct error worse than a vertically flipped comparison, or an unexplained source/BMP error is still a failed texture conversion.
- Regenerate QC from the contract; do not hand-edit generated outputs as the primary fix.
- Compare source SMD, QC, compiled MDL inspection, and independent readback to locate the first divergent layer.
- Compare embedded indexed texture indices and palette bytes or hashes against the final BMP; then compare independent readback pixels. A matching material name alone is not preservation evidence.

Custom transplant scripts must run the same SMD geometry budget, skeleton, single-weight, material-token, compile, inspect, and readback gates as contract-driven exports. A script that calls StudioMDL directly is not exempt from preflight.

Read [pitfalls.md](pitfalls.md) by symptom before applying a repair. Use the enhanced contract for every imported repair so the original behavior and unrelated model surfaces remain explicitly preserved.
