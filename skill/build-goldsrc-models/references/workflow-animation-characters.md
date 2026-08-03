# Animation And Character Models

## Choose The Structure

Use this route for animated props, animation remapping, NPC/player models, bodygroups, skin families, controllers, attachments, hitboxes, mouth controls, activities, events, and root motion.

Define the exported skeleton and model variants before animation. Keep bodygroup geometry choices separate from skin-family texture substitutions.

## Rig And Weight

1. Name bones deterministically and keep the final hierarchy at or below 128 bones.
2. Apply armature/object transforms before animation transfer.
3. Assign each exported vertex to exactly one owner bone at weight `1.0`; GoldSrc does not preserve Blender blended weights.
4. Add a deliberate intermediate deformation bone when a rigid two-bone bend cannot preserve the silhouette. Reassign the transition region rather than leaving multiple weights.
5. Verify rest pose, parent order, origin, and attachment/controller bones before making Actions.

<a id="rotation-axis-space"></a>
## Resolve Rotation Axis Space

Treat a requested axis, a pose-channel suffix, and the compiled MDL axis as three different facts.

1. Blender edit-bone local `+Y` runs from head to tail. Local `X` and `Z` depend on bone roll, so `rotation_euler.z` means local Z, not world Z.
2. Compute the rest local-to-world basis as `B = (armature.matrix_world @ bone.matrix_local).to_3x3()`. Convert a requested normalized world axis with `axis_local = B.inverted() @ axis_world`.
3. Use one Euler channel only when `axis_local` is approximately a signed cardinal axis. Apply the sign to the keyed angle. For a non-cardinal result or a moving parent, construct the target pose matrix and convert it to keyframeable local basis with `bone.convert_local_to_pose(..., invert=True)`, supplying the evaluated parent pose and parent rest matrices.
4. Do not compensate manually for StudioMDL after solving the Blender world transform. This Toolchain writes Blender matrices into SMD; StudioMDL then applies a root `+90 degree Z` convention, giving `X_mdl = -Y_blender`, `Y_mdl = X_blender`, and `Z_mdl = Z_blender`. Thus Blender world Z remains compiled MDL Z.
5. Independently measure the authored and roundtrip axes. For sample rotations `R0` and `Rq`, compute `delta = Rq @ R0.inverted()` and inspect `delta.to_quaternion().axis`. At a quarter-turn around Z, evaluated Z span remains constant while X/Y spans change; around X, X span remains constant; around Y, Y span remains constant.

For the common zero-roll bone aligned head-to-tail along Blender world Z:

| Pose channel | Blender world axis |
|---|---|
| local `X` | `+X` |
| local `Y` | `+Z` |
| local `Z` | `-Y` |

Therefore world-Z rotation uses `rotation_euler.y` for that exact rest orientation. This table is a measured special case, not a reusable substitute for computing `B`.

## Author Actions

1. Create explicitly named Actions and bind the intended Action and slot to the armature.
2. Set scene FPS, inclusive scene playback range, Action frame range, sequence FPS, loop state, activity, events, root motion, and motion axes deliberately. `Action.use_frame_range` is intended-range metadata and `Action.use_cyclic` does not automatically make playback loop.
3. Bake constraints and retargeting results to ordinary pose-bone keys before export, then remove temporary constraints.
4. For remapping, build an explicit source-to-target bone map and solve rest-pose/proportion differences before baking.
5. Compare start, quarter, midpoint, three-quarter, end, and motion extremes. Use a labeled `START / 1/4 / MID / 3/4 / END` contact sheet for timing and pose overview, while retaining the original stills for full-resolution checks. Start/mid/end alone can all land on the same neutral pose in a symmetric loop. For axis-specific motion, report the measured world axis and orthogonal span that stays invariant.
6. For a loop, author enough intermediate poses to preserve direction and complete rotations, then add a duplicate seam endpoint whose local pose matrices match the first frame. The duplicate is a loop seam, not extra motion. Distinguish 64 samples (`1..64`, 63 intervals) from a 64-interval period (`1..65` or `0..64`).
7. Restore the sequence start frame and save the Blend with the Action bound so spacebar playback works immediately after opening.

## Retarget In Blender

1. Keep source and target armatures separate. Apply intended object scale before binding constraints and preserve both rest poses for comparison.
2. Build an explicit source-to-target map. Use Copy Location/Rotation/Transforms constraints in proven local/pose spaces; add rest-pose correction transforms instead of assigning world deltas to pose channels.
3. Resolve proportion differences before baking. Do not use an armature scale change after skinning to hide a bone-length mismatch.
4. Bake the evaluated constrained pose over the complete target Action, remove temporary constraints, bind the baked Action/slot, and restore the start frame.
5. Compare named contact landmarks at start, quarters, end, and motion extremes. Then compare exported SMD global matrices and compiled local channels.

The Half-Life SDK player QC uses these Gearbox-to-final rename rules:

| Source bone | Final bone |
|---|---|
| `Bip01 L Thigh` | `Bip01 L Leg` |
| `Bip01 L Calf` | `Bip01 L Leg1` |
| `Bip01 R Thigh` | `Bip01 R Leg` |
| `Bip01 R Calf` | `Bip01 R Leg1` |
| `Bip01 L Clavicle` | `Bip01 L Arm` |
| `Bip01 L UpperArm` | `Bip01 L Arm1` |
| `Bip01 L Forearm` | `Bip01 L Arm2` |
| `Bip01 R Clavicle` | `Bip01 R Arm` |
| `Bip01 R UpperArm` | `Bip01 R Arm1` |
| `Bip01 R Forearm` | `Bip01 R Arm2` |

Put final names in contract `bones` and source/final pairs in `bone_renames`. Do not create chained mappings.

## Customize An NPC

- Preserve the baseline sequence list as an ordered prefix. Append new sequences at the QC end; inserting them can change hard-coded animation indices.
- For repeated activities, assign deliberate ACT weights. The engine chooses among sequences sharing an activity according to those weights.
- Write events as `event <id> <frame> [options]` inside the sequence and keep the event frame inside the exported range. Event `1003` can target map-side behavior where the game/mod supports it; that does not make map entities part of the MDL.
- Use `LX`/other declared motion tokens when StudioMDL should extract linear root movement. Repeated decompile/recompile passes can reduce stored linear-movement precision, so compare against the trusted baseline rather than another decompile.
- Preserve hitbox group intent when migrating armor, helmet, or damage-region metadata. Check group, bone, and bounds in the binary table.
- Declare `compatibility.role = "npc"` and a baseline MDL when preserving an existing NPC. Treat activity, weight, event, and linear-movement differences as explicit review evidence.

## Preserve A Player Model

- Start from the exact target-game player baseline. Do not add, remove, or reorder its sequences; preserve each FPS and never exceed the corresponding baseline frame count.
- Keep baseline bones as an unchanged ordered prefix. Append new bones only as terminal subtrees below baseline leaf bones; never insert a new bone into the existing hierarchy.
- Preserve the baseline hitbox table. Keep one skin family and only the standard `body` bodypart with one or two model choices used by the player high/low-model behavior.
- Build portrait BMPs as `164x200`, uncompressed 8-bit indexed images. Non-remapped portraits may use at most 160 colors. Validate remap palette construction and `topcolor`/`bottomcolor` behavior separately in the target game.
- Declare `compatibility.role = "player"` with an artifact-relative baseline MDL and run `INSPECT`. Use `api.validate_player_portrait(path, remapped=False)` for the separate portrait resource.
- Toolchain API 1 validates blend-count differences but does not author standard dual-source/four-source player blend sequences. Do not claim a full player rebuild through the single-source contract path.

## Add Character Metadata

- Export each bodygroup choice as its own mesh choice; represent an empty choice as QC `blank`.
- Give every skin family the same number of texture references and identical dimensions per slot.
- Validate controller indices/types, attachment indices and bones, hitboxes, mouth behavior, activities, events, and player remap textures in the compiled MDL records.
- Start player/NPC work from an explicitly licensed or user-provided skeleton/model baseline. Do not silently substitute geometry from previous projects.

## Inspect Transfer

- Compare evaluated Blender vertices, exported SMD global bone matrices, and decoded MDL sequence channels independently. Compare compiled rotations as matrices or quaternion angle, not as three independent Euler differences.
- Treat world-space-to-pose transfer as an armature/parent/rest-space problem. Never assign a decomposed world delta directly to a pose bone unless those spaces have been proven identical.
- Read [pitfalls.md](pitfalls.md) sections `single-weight`, `action-channelbags`, `bone-space`, `playback-start`, `loop-endpoint`, `player-compatibility`, `npc-root-motion`, `readback-collisions`, `blank-readback`, and `contact-sheet-overview` before changing animation content.

## Export And Read Back

Use the enhanced contract for character metadata, multiple Actions, bodygroups, skins, controllers, attachments, hitboxes, remapping, or imported animation. Inspect every compiled sequence's frame count, FPS, flags, events, root motion, contact sheet, and representative source frames. Match compiled frames to source poses in SMD declaration order because SMD time labels need not start at zero. Missing Actions, skin families, numeric-suffix readback names, contact-sheet frame mappings, or five animated previews with zero foreground are regressions, not acceptable reader limitations.
