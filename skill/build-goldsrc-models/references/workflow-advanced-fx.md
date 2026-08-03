# Advanced Exportable MDL Effects

Use this route when the visible result depends on animated planes, shells, rigid deformation helpers, or texture-state changes rather than runtime particles or shaders. Keep every rendered surface present from frame 0 and bake all motion into ordinary GoldSrc bones.

## Build Flame, Smoke, And Embers

1. Build flame from a small crossed or layered set of one-sided planes. Use indexed black-background Additive textures for luminous regions and Masked textures where a hard cutout is required.
2. Build smoke from a tapered low-sided tube or stacked cards. Give successive sections separate bones so translation, rotation, and scale create broad curling without blended vertex weights.
3. Build embers from irregular cards or tiny rigid pieces with Masked textures. Offset their cycles and rotations so the repetition is not synchronized.
4. Keep loop endpoints visually continuous. When a layer must disappear at a seam, turn or collapse its already-present one-sided geometry rather than spawning or deleting it.
5. In Blender 5.2, approximate Additive with a Transparent/Emission node composition; the old native `blend_method = ADDITIVE` mode is not available. The legacy `Material.blend_method` property remains for modes such as `CLIP`, but it does not create GoldSrc Additive behavior. Treat the preview as intent and verify the compiled flags and MDL readback.

Read `TEX-01`, `TEX-04`, and `SMD-02` in [pitfalls.md](pitfalls.md) before judging brightness or transparency.

## Add A Fake Specular Shell

1. Duplicate only the surfaces that need the moving highlight and offset the shell slightly along evaluated normals. Keep enough clearance to avoid Z-fighting without visibly changing the silhouette.
2. Use a black indexed texture with a small white or bright highlight region. Assign both Chrome and Additive modes to the shell.
3. For Half-Life/Counter-Strike, keep every effective Chrome texture exactly `64x64`. Sven 5.18+ removes that classic restriction; prefer at most `256x256` unless the target and visual evidence justify more.
4. Use either explicit `chrome` mode with any valid filename, or the legacy `CHROME_` filename form. The filename form also implies Flatshade; do not add the prefix merely to satisfy the flag-set route.
5. Compare the author shell at several view angles with MDL readback. A normal Chrome surface reflects across the whole shell; fake specular limits the visible response through the sparse highlight texture.

## Approximate Soft Deformation With Intermediate Bones

1. Insert one or more deformation bones between rigid visual regions when single-bone ownership would create a hard fold.
2. Use constraints to place or orient the intermediate bones between the driving endpoints. A midpoint constraint is a starting construction, not a reusable artistic threshold.
3. Assign every exported vertex to exactly one endpoint or intermediate bone at weight `1.0`.
4. Bake the evaluated constrained pose to ordinary pose keys across the complete Action, remove temporary constraints, and bind the baked Action before export.
5. Compare evaluated surface landmarks before and after baking, then inspect SMD global matrices and compiled animation channels.

Read `GEO-02`, `ANIM-01`, and `ANIM-02` in [pitfalls.md](pitfalls.md).

## Layer Image-Plane Detail

1. Place a Masked or Additive detail plane just above the host surface for labels, gauges, switches, or other detail that should not consume the host texture palette.
2. Choose the offset relative to model scale and camera distance. Verify it is large enough to avoid Z-fighting and small enough to remain visually attached.
3. Reserve palette index 255 as blue `(0, 0, 255)` for Masked transparency; keep antialiased edge pixels out of that transparent index unless they should disappear.
4. Duplicate reverse-wound faces when both sides must render. Texture flags do not disable GoldSrc back-face culling.

## Switch States With Skin Families

- Use a skin family when geometry stays fixed and one texture slot changes between on/off, day/night, intact/damaged, or similar states.
- Give every family the same slot count and keep each slot's dimensions identical across families.
- Use a bodygroup when geometry changes or becomes blank. Combine bodygroups and skin families only when both geometry and texture state genuinely vary.
- Declare every family through the contract and verify the compiled skin table plus each readback state; changing Blender material assignments alone does not create a GoldSrc skin family.

## Validate The Effect

- Inspect author and readback views at loop start, quarter, midpoint, three-quarter, and end, plus any visibility or deformation extreme.
- Confirm final indexed texture pixels, effective MDL flags, one-bone ownership, back-face behavior, shell clearance, and skin-table mappings.
- Keep particle systems, runtime materials, procedural modifiers, and unbaked constraints out of the final claim. GoldSrc receives geometry, texture flags, skin tables, and bone animation only.

Source basis: the303 `gold_research`, `gold_qc`, and validated Blender 5.2/StudioMDL regressions listed in [sources.md](sources.md).
