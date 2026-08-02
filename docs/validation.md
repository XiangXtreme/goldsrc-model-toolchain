# Validation And Acceptance

## Evidence Layers

Keep each layer separate:

1. Blender authoring state: objects, UVs, weights, normals, Actions, bake settings, and preview.
2. Extension export: contract resolution and parsed SMD/QC artifacts.
3. StudioMDL result: compiler identity, logs, MDL v10 binary structure, and texture flags.
4. Independent round trip: reconstructed geometry, skin metadata, textures, Actions, five-point renders, and labeled contact sheets.
5. Visual review: production-side and MDL-side images inspected by the agent.
6. In-game validation: a separate claim, false unless the named game/mod was actually run.

In addition, bind each explicit version 2 requirement to stage-local `requirement_evidence`. Review the literal source phrase before deciding what proves it. Treat task measurements as facts, not reusable artistic targets, and never add an intensity threshold merely because another asset used one.

For rigid-body animation, additionally require an adaptive-settlement report: a completed stillness window, zero final kinematic export objects, no unexplained never-woken bodies, no receiver-bound escapes, and representative impact/aftermath frames. Report the configured maximum, actual final frame, and avoided frames so a long maximum is not mistaken for baked animation length.

For `baked_event_chain` physics, require `physics_event_report.json` or an equivalent section in the simulation report. It must list each stage's resolved frame, actual resolved contact pair, released objects, first-motion frames, constraint-break frames, dependency/order issues, and each interaction's measured relative velocity before/after contact and direction change. Include the final stable/kinematic/receiver state. Reject early motion/fracture, missing contact triggers, unresolved dependencies, unbroken requested joints, responses that do not satisfy the literal requirement, full-frame penetration above tolerance, final kinematic bodies, receiver escapes, and unsettled captures. Use a numeric response threshold only when its source is the user or a documented task-specific correctness boundary. Penetration evidence must honor fixed Blender collision collections and use sampled evaluated OBB broad phase plus collision-proxy-appropriate narrow phase; a hard-coded zero, incompatible collision pair, or final-frame-only claim is invalid. Inspect stills at intact, pre-fracture, post-fracture, secondary-impact, and settled frames; a nonblank image alone is not acceptance.

Also require a solver-owned motion audit. Any non-driver body with scripted location/rotation/scale keyframes, a hand-authored fallback path, or a report that does not identify Blender 5.2 Rigid Body World as the capture source is a hard failure. Kinematic impactor travel is evidence only for the named impactor; it does not validate the motion of the bodies it strikes.

Require three independent animation-transfer checks for rigid-body models:

1. Compare every sampled rigid-body matrix with the actual vertices after Blender evaluates the Armature modifier. Reject any position error above the declared author tolerance.
2. Parse the reference and animation SMDs, rebuild global bone matrices through their parent hierarchy, and compare all weighted vertices with the persisted Blender capture. Do not validate only local channel values.
3. Decode every compiled MDL v10 sequence's `mstudioanimvalue_t` channels and compare them with its source animation SMD. Account for StudioMDL's default root `+90 degree Z` import convention. Reject missing frames or excess position/rotation quantization error.

Compilation success, sequence FPS/frame counts, an unchanged `matrix_basis`, or a static readback cannot replace these checks. The independent SourceIO-derived reader must reconstruct embedded sequences as Actions; missing or unbindable Actions are regressions unless the contract names a separately proven external-sequence limitation.

For animated round trips, bind each imported Action in turn and render start, quarter, middle, three-quarter, and end frames up to the bounded preview budget. Record Action, frame, path, image hash, model-region occupancy, and foreground luminance for each still. Compose those stills as a labeled `3x2` contact sheet so sequence timing can be scanned without an unreadable ultra-wide image. Keep labels in a caption band outside the image area. Preserve every source PNG and a JSON layout sidecar; the contact sheet is an index, not a replacement for the source evidence. Warn when an Action has varying channels but all sampled render hashes are identical. Before saving `mdl_roundtrip.blend`, rebind the first Action and restore its start/range so viewport playback works immediately. Pixel metrics prove that evidence is present; the agent must still inspect the images for the requested visible result.

Make the readback renderer own a deterministic World and lights instead of inheriting the author Blend environment. Scale light position and size with imported bounds, and scale AREA-light power linearly with the same extent so large GoldSrc-coordinate assets do not become black silhouettes or washed-out white shapes. Inspect color and surface detail in the actual five-point images; a non-empty PNG or changing hash is not brightness acceptance.

For contact-triggered stages, require a captured contact frame and actual `resolved_pair`. Ensure every declared released body's first motion and every declared breakable constraint's separation obey the resolved frame. Reject any author script that uses the observed frame to mutate rigid-body state or produce a second gated bake.

Require the intact assembly's initial audit separately from dynamic penetration: visible cut geometry must be flush, hidden collision proxies must have non-negative clearance, and the first support contact must not be an unintended drop that fractures the assembly. For composite objects, evaluate all declared candidate contact pairs and retain the actual pair in the report.

## Extension Acceptance

Run `scripts/extension_fixture.py` for a textured skeletal Action across all five public stages. Run `scripts/extension_feature_fixture.py` for non-empty bodygroups, blank choices, all skin families, controller, attachment, hitbox, special texture flags, and adaptive rigid-body settlement. Run `scripts/extension_smoke_test.py` from a clean ZIP installation to prove no legacy dependency or UI registration.

Require round-trip Actions, five representative images, one labeled contact sheet per Action, image variation for animated Actions, no `.001` names after repeated readback, and a saved Blend with the first Action bound at its start frame. Inspect the contact sheet first, then open any suspicious original frame at full resolution. Open the Blend and verify Space playback after any change to Action reconstruction.

For Half-Life/Counter-Strike delivery, reject Sven-only limits or directives not supported by the target engine. Compare explicit MDL bbox/cbox fields with QC. Actual game loading remains a separate claim.

When `compatibility` is declared, require the `INSPECT` report to contain the candidate and baseline paths, role, issues, metadata differences, and appended-sequence facts. Player mismatches in sequence order/count, FPS, frame ceiling, baseline bone prefix, terminal appendages, hitboxes, skin family, or standard bodypart are hard failures. NPC sequence-prefix changes are hard failures; activity, weight, event, and linear-movement changes are retained as evidence. A blend-count warning documents the API-1 limitation and does not claim that multi-source sequence authoring was performed.

## Explicit Limitations

External sequence groups are unsupported unless the contract explicitly declares that limitation. Embedded Actions and all skin-family metadata are required by default. Do not allow `sourceio_actions` or `sourceio_skin_families` as routine blockers.

## Failure Handling

Delete stale stage JSON before rerunning a phase. Preserve compiler logs and failed artifacts. Change one cause at a time and regenerate every downstream layer after the changed layer.

Read [fixtures.md](fixtures.md) for the maintained Extension and physics regression entry points.
