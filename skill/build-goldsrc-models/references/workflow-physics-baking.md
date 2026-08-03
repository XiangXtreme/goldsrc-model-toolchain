# Physics And Exportable Effects

## Select A Source Simulation

Choose the Blender source representation from observable behavior:

| Effect | Preferred source | Exportable result |
|---|---|---|
| Discrete impacts, debris, rolling objects | Rigid bodies and fixed collision proxies | One bone per moving rigid piece |
| Ropes, chains, cables | Segmented rigid bodies, joints, or bone chain | Segment bones and skinned/segmented mesh |
| Hinges and mechanisms | Explicit driver plus solver-owned passive bodies and constraints | Local bone rotations/translations |
| Cloth, banners, soft sheets | Cloth/Soft Body when stable, otherwise low-frequency bones or cloth segments | Baked deformation bones or rigid segments |
| Fracture and collapse | Pre-cut render pieces, supports, breakable constraints, one solve | Piece bones with baked transforms |

Record approximation boundaries. Do not claim that GoldSrc runs Cloth, Bullet, or fracture at runtime.

## Build The Simulation

1. Put every eventual render piece in the scene at frame 0. Assemble intact surfaces without visible air gaps or early motion.
2. Separate visible geometry from collision proxies when a flush or concave render mesh produces jitter, tunneling, or the wrong contact normal.
3. Declare the rigid-body world, gravity, cache range, substeps, solver iterations, collision collections, sleeping/deactivation, mass, friction, restitution, damping, and constraints before evaluation.
4. Treat only explicitly declared impactors or launchers as kinematic drivers. All other body motion belongs to Blender's evaluated Rigid Body World.
5. Run one simulation from the complete frame-0 state. Contact detection is evidence; it must not trigger a scripted enable, transform path, second bake, or geometry creation.
6. Use constraints/supports whose normal travel loads remain below fracture loads. Test the full pre-impact path before lowering thresholds.

## Capture And Transfer

1. Sample every solved frame from `evaluated_get(depsgraph)` before downsampling.
2. Transfer rigid-body world transforms through armature world, parent pose, and bone rest spaces. Preserve quaternion compatibility between frames.
3. Audit evaluated armature vertices against captured body matrices, then audit exported SMD global matrices independently.
4. Downsample only after complete capture. Preserve duration with `export_fps = source_fps / sample_step`; never truncate the tail to fit an animation budget.
5. Save the Action bound to the armature at its start frame and remove or isolate simulation dependencies only after the baked animation is verified.

## Settle And Accept

- Use a generous maximum frame, an activity gate after the last intentional launch/contact, translation and rotation stillness checks, a continuous stillness window, hold frames, and receiver bounds.
- Inspect intact, pre-contact, contact, break/release, secondary collision/response, and settled frames. Build a labeled event contact sheet using semantic labels such as `INTACT`, `PRE-CONTACT`, `CONTACT`, `POST-BREAK`, `RESPONSE`, and `SETTLED`; these samples need not be evenly spaced.
- Require the requested event order, actual contact pair, intended deflection/reversal/separation, no unexplained penetration or escape, no final kinematic export body, and a stable final window.
- Keep physical evidence separate from artistic judgment. Measurements support the decision but do not replace checking whether the requested motion looks correct.

## Blender Lifecycle Safety

Create world collections, bodies, constraints, and proxies before dependency-graph evaluation. After membership changes update the graph once. When rebuilding, tear down constraints, bodies, world, objects, then object data; do not evaluate after invalidating Bullet-owned handles.

If Blender crashes, read the crash log and matching Blender/Bullet source before changing physical parameters. Check stale handles, world membership, cache invalidation, constraints, and dependency-graph lifetime first.

Read [pitfalls.md](pitfalls.md) sections `physics-lifecycle`, `contact-gating`, `collision-proxies`, `settlement`, `bone-space`, and `contact-sheet-overview` before iterating.

## Export

Physics assets always use the enhanced contract. Record drivers, solver-owned bodies, sampled frames, contact/constraint events, transfer audits, approximation boundaries, representative renders, and the event contact-sheet frame mapping. Keep original event stills because the overview can hide fine penetration or seams. GoldSrc receives only the final mesh, bones, textures, and baked sequence.
