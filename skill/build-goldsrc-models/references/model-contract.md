# Model Contract Version 2

Use one `model_contract.json` per deliverable. Keep it in the artifact directory and use only relative paths inside it. Validate structure before authoring; after EXPORT, let each downstream stage apply `export_plan.json` and validate the resulting effective file set.

Version 2 is required for new production work. Version 1 remains readable only so old artifacts can be recovered; it does not provide request-evidence gating.

## Required Shape

| Field | Type and rule |
|---|---|
| `version` | Integer `2` for new work |
| `intent` | `{request,requirements,assumptions?,revision?}` preserving user intent and evidence coverage |
| `target_profile` | `half-life-cs` by default; `sven-coop` only for an explicit Sven target |
| `model_name` | Relative `.mdl` path with a portable filename |
| `scale` | Finite positive number |
| `bones` | Ordered `{name,parent}` list; `parent` is a name or `null`; include a root for static models |
| `bone_renames` | Optional `{source,target}` pairs for `$renamebone`; `bones` contains final compiled names |
| `bodies` | `{name,source,object?}` entries; `source` is a relative reference SMD |
| `bodygroups` | `{name,choices}`; every choice is exactly `{studio,object?}` or `{blank:true}` |
| `textures` | `{name,source,width,height,modes,require_masked_pixels?}`; names/sources end in `.bmp` |
| `large_textures` | Logical `{name,image,width,height,tile_size?}` atlases; EXPORT expands possible tiles and compiles only referenced ones |
| `skin_families` | Texture-name rows of equal width; each slot keeps identical dimensions across rows |
| `sequences` | `{name,source,action?,fps,frame?,loop?,activity?,events?,motion?,origin?}` |
| `hitboxes` | `{group,bone,min,max}`; an empty list leaves hitbox generation to StudioMDL and does not require a zero-count MDL table |
| `attachments` | `{index,bone,origin}` with unique indices `0..3` |
| `controllers` | `{index,bone,type,start,end}` with unique indices `0..4` |
| `bounds` | Explicit `bbox` and `cbox`, each containing numeric `min` and `max` vec3 values |
| `outputs` | Optional relative `qc`, `sven_mdl`, `export_plan`, and `report` overrides |
| `acceptance` | Optional `required_phases`, `visual_views`, and `allow_known_blockers` |
| `physics` | Optional `{mode,simulation,stages,interactions}` for pre-baked multi-stage rigid-body validation |
| `limitations` | Optional `{external_sequence_groups:[sequence names]}`; names must match actual external sequence entries |
| `compatibility` | Optional `{role,baseline_mdl}` for `player` or `npc`; baseline path is artifact-relative MDL v10 |

Texture roles are intentionally asymmetric. A prepared Blender material references a logical PNG and carries the logical `.bmp` token used by SMD. A normal `textures[].source` names the indexed BMP that EXPORT writes for compilation; it is not the image that must be rebound to the prepared material. A `large_textures[].image` names the logical PNG, while generated `512x512` tile BMPs exist only in the effective export plan and compiled artifact set.

## Intent And Evidence

Copy the relevant user request into `intent.request` without rewriting its meaning. Add one requirement per explicit observable behavior or appearance constraint:

```json
{
  "intent": {
    "request": "...the user's request...",
    "requirements": [
      {
        "id": "wall-no-air-gaps",
        "source": "完整墙体不能有空气缝",
        "evidence_phases": ["author", "visual_review"]
      }
    ],
    "assumptions": []
  }
}
```

Keep `source` as a literal user phrase; validation requires it to appear verbatim in `intent.request`. Do not add a requirement for an outcome the user did not request. Ordinary target-profile, naming, dimensions, topology, physics, or material choices belong in their contract fields or in `assumptions`, each as `{id,statement,reason}`. Assumptions may resolve implementation details but may not intensify or redirect the requested effect.

Every listed evidence phase must be present in `acceptance.required_phases`. Do not use `environment` as content evidence because that phase is intentionally reusable across contract changes. Each content-evidence stage result JSON must include:

```json
{
  "requirement_evidence": [
    {
      "id": "wall-no-air-gaps",
      "status": "pass",
      "summary": "Frame 0 wall coverage audit found no visible opening.",
      "evidence": {"gap_count": 0, "render": "intact_front.png"}
    }
  ]
}
```

Evidence may contain measurements, frame numbers, contact pairs, report paths, or inspected-view findings. Measurements describe the result. Do not promote them to reusable creative targets. Numeric pass thresholds must come from the user or a documented technical constraint; otherwise validate the requested behavior directly and retain the measured value as evidence.

For a user-requested revision, add:

```json
{
  "revision": {
    "baseline_report": "baseline/model_pipeline_report.json",
    "changed_factors": ["impactor speed"],
    "preserve": ["geometry", "materials", "sequences", "all unrelated requirements", "delivery whitelist"]
  }
}
```

The baseline must be a passing pipeline report when file validation runs. Keep `changed_factors` limited to what the user requested. Use `preserve` to state the unaffected contract surface in task-specific language rather than selecting from a fixed workflow taxonomy.

Allowed texture modes are `flatshade`, `chrome`, `fullbright`, `nomips`, `alpha`, `additive`, and `masked`. Allowed sequence motion tokens are `X/Y/Z`, `XR/YR/ZR`, `LX/LY/LZ`, `AX/AY/AZ`, and `AXR/AYR/AZR`.

Use unique, non-chained `bone_renames`; every target must exist in final `bones`. SMD file validation canonicalizes source names through this map, QC emits `$renamebone` before models and sequences, and MDL inspection requires final names. A compatibility baseline is read only during file validation/INSPECT and must stay inside the artifact directory.

The `half-life-cs` profile rejects Fullbright and requires effective Chrome textures to be `64x64`. A `CHROME_` filename implies Chrome+Flatshade; explicit `chrome` mode does not require the prefix. Masked directives are emitted after non-Masked directives so Additive precedes Masked.

An activity is `{"name":"ACT_IDLE","weight":1}`. An event is `{"frame":10,"id":1,"options":"..."}`. Event frames must be inside the declared and exported sequence range.

For `physics.mode = "baked_event_chain"`, `simulation` may declare `source_fps`, `sample_step`, `export_fps`, `sequence`, `max_frame`, settlement thresholds, contact margin, penetration tolerance, and receiver bounds. `export_fps` must equal `source_fps / sample_step` when both are declared. `physics.constraints` may describe frame-0 Blender rigid-body constraints and their break thresholds. Each stage has a unique `name`, optional `depends_on`, a `trigger` of `frame` or `contact`, and at least one of `release`, `break_constraints`, or `participants`; it may also declare expected motion/break windows. A contact trigger declares exactly one exact `pair` or a non-empty candidate `pairs` list. The evaluator resolves the earliest observed pair and applies `offset_frames` only to validation. It must not insert transform paths or toggle rigid-body state after contact. Each interaction likewise declares exactly one `pair` or candidate `pairs`, a frame `window`, and a `response` of `deflect`, `reverse`, or `separate`. Add a minimum response threshold only when the user supplies one or the task-specific requirement defines a defensible binary boundary; never reuse a global artistic threshold across assets. This metadata validates a single Blender solve; it does not add runtime spawning, map entities, or scripted event gates to the MDL.

## Defaults

Omitted collection fields become empty lists. Omitted outputs make the Sven build the primary `model_name` without a compiler suffix. Omitted acceptance fields require all eight production phases, three useful visual views, and allow no known blockers. External sequence groups require an explicit contract limitation; missing embedded Actions or skin families are regressions. New contracts default to version 2 and therefore fail until `intent` is present.

## Extension API

```python
api = bpy.app.driver_namespace["goldsrc_model_toolchain"]
contract = api.load_contract(path, artifacts_dir=artifact_dir, require_files=False)
api.execute_stage("PREFLIGHT", path, artifact_dir)
```

After `EXPORT`, inspect export-plan version 2 and execute COMPILE normally; COMPILE applies its `compiled` texture list before `require_files=True` validation, so omitted sparse-atlas tiles need not exist. A direct `api.load_contract(..., require_files=True)` call validates the raw declaration and does not apply the plan. Use `api.inspect_mdl(path)` for direct binary inspection, `api.validate_model_compatibility(...)` for player/NPC baselines, and `api.validate_player_portrait(...)` for the separate portrait BMP. The implementation and host maintenance CLIs live in the public `goldsrc-model-toolchain` repository, not in this Skill.

Validation rejects missing version 2 intent, untraceable or duplicate requirement IDs, impossible evidence phases, malformed revision scope, non-passing revision baselines, absolute or escaping paths, duplicate names/indices, bone cycles and missing parents, invalid bodygroup choices, missing SMD/BMP files, skeleton divergence, non-single-weight SMD vertices, material-token mismatches, invalid BMP encodings, mismatched skin rows/dimensions, invalid FPS/motion/events, missing bone references, and invalid bounds.
