# Model Contract Version 2

Use one `model_contract.json` per deliverable. Keep it in the artifact directory and use only relative paths inside it. Validate before authoring and again with `--require-files` after export.

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
| `bone_renames` | Optional `{source,target}` list for QC `$renamebone`; `bones` always contains final compiled names |
| `bodies` | `{name,source,object?}` entries; `source` is a relative reference SMD |
| `bodygroups` | `{name,choices}`; every choice is exactly `{studio,object?}` or `{blank:true}` |
| `large_textures` | Optional logical atlas records `{name,image,width,height,tile_size:512,modes}`; expanded into `512x512` texture entries |
| `textures` | `{name,source,width,height,modes,require_masked_pixels?}`; names/sources end in `.bmp` |
| `texture_bake` | Optional authoring assertion `{uv_layer,require_active_render?}`; when present, PREFLIGHT requires the declared UV to be both the evaluated export UV and Blender's active-render UV. It does not prove that a source bake is unlit; a strict no-shadow bake must be authored as a temporary Emission/`EMIT` bake before EXPORT |
| `skin_families` | Texture-name rows of equal width; each slot keeps identical dimensions across rows |
| `sequences` | `{name,source,action?,fps,frame?,loop?,activity?,events?,motion?,origin?}` |
| `hitboxes` | `{group,bone,min,max}`; an empty list leaves hitbox generation to StudioMDL and does not require a zero-count MDL table |
| `attachments` | `{index,bone,origin}` with unique indices `0..3` |
| `controllers` | `{index,bone,type,start,end}` with unique indices `0..4` |
| `bounds` | Explicit `bbox` and `cbox`, each containing numeric `min` and `max` vec3 values |
| `outputs` | Optional relative `qc`, `sven_mdl`, `report`, and `export_plan` overrides |
| `acceptance` | Optional `required_phases`, `visual_views`, and `allow_known_blockers` |
| `physics` | Optional `{mode,simulation,stages,interactions}` for pre-baked multi-stage rigid-body validation |
| `limitations` | Optional `{external_sequence_groups:[sequence names]}`; names must match actual external sequence entries |
| `compatibility` | Optional `{role,baseline_mdl}`; role is `player` or `npc`, and the baseline is an artifact-relative MDL v10 path |

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

Half-Life/Counter-Strike rejects `fullbright` and requires every effective Chrome texture to be exactly `64x64`. A filename beginning with `CHROME_` is the legacy name-set form and implies both Chrome and Flatshade; an explicit `chrome` mode is the flag-set form and needs no prefix. Sven keeps the ordinary texture bounds. A logical atlas may exceed 512 only when both dimensions are tile-aligned, each generated tile is no larger than `512x512`, and the total is at most 64 tiles. QC generation preserves declared texture/mode order except that all Masked directives are emitted after non-Masked directives, which guarantees Additive precedes Masked.

The export plan is generated after Blender evaluation. It records tiled materials, generated texture facts, and the SMD parts used for compilation. Do not hand-edit the plan or use a text-only SMD slicer; rerun EXPORT after changing the contract or author mesh.

When `texture_bake` is declared, set the named UV layer as both `mesh.uv_layers.active` and the layer's `active_render` flag before `bpy.ops.object.bake`. Blender's bake operator can read the active-render layer while EXPORT reads the evaluated active UV; a mismatch produces a visually valid but incorrectly mapped texture. `require_active_render` defaults to `true`. Without this declaration, PREFLIGHT still reports an active/active-render mismatch as a warning but does not reject otherwise valid explicit UV workflows.

Each bone rename source and target is unique, targets one final contract bone, and may not form a chain or cycle. Artifact SMD skeletons are canonicalized through this map before comparison, while Blender preflight and compiled MDL inspection continue to require the final names. Compatibility baselines are checked during `INSPECT`: player models preserve the sequence table, FPS, frame ceilings, ordered baseline bone prefix, terminal appendages, hitboxes, skin/bodypart shape; NPC models preserve the baseline sequence prefix and may only append sequences. Blend-count differences are reported as the explicit API-1 multi-source-authoring limitation rather than treated as implemented support.

An activity is `{"name":"ACT_IDLE","weight":1}`. An event is `{"frame":10,"id":1,"options":"..."}`. Event frames must be inside the declared and exported sequence range.

For `physics.mode = "baked_event_chain"`, `simulation` may declare `source_fps`, `sample_step`, `export_fps`, `sequence`, `max_frame`, settlement thresholds, contact margin, penetration tolerance, and receiver bounds. `export_fps` must equal `source_fps / sample_step` when both are declared. `physics.constraints` may describe frame-0 Blender rigid-body constraints and their break thresholds. Each stage has a unique `name`, optional `depends_on`, a `trigger` of `frame` or `contact`, and at least one of `release`, `break_constraints`, or `participants`; it may also declare expected motion/break windows. A contact trigger declares exactly one exact `pair` or a non-empty candidate `pairs` list. The evaluator resolves the earliest observed pair and applies `offset_frames` only to validation. It must not insert transform paths or toggle rigid-body state after contact. Each interaction likewise declares exactly one `pair` or candidate `pairs`, a frame `window`, and a `response` of `deflect`, `reverse`, or `separate`. Add a minimum response threshold only when the user supplies one or the task-specific requirement defines a defensible binary boundary; never reuse a global artistic threshold across assets. This metadata validates a single Blender solve; it does not add runtime spawning, map entities, or scripted event gates to the MDL.

## Defaults

Omitted collection fields become empty lists. Omitted outputs make the Sven build the primary `model_name` without a compiler suffix. Omitted acceptance fields require all eight production phases, three useful visual views, and allow no known blockers. External sequence groups require an explicit contract limitation; missing embedded Actions or skin families are regressions. New contracts default to version 2 and therefore fail until `intent` is present.

## Commands

```powershell
python scripts/model_contract_cli.py validate <model_contract.json>
python scripts/model_contract_cli.py validate <model_contract.json> --artifacts <artifact-dir> --require-files
python scripts/model_contract_cli.py qc <model_contract.json>
python scripts/model_contract_cli.py inspect-mdl <model.mdl> --output <inspection.json>
```

Validation rejects missing version 2 intent, untraceable or duplicate requirement IDs, impossible evidence phases, malformed revision scope, non-passing revision baselines, absolute or escaping paths, duplicate names/indices, bone cycles and missing parents, invalid bodygroup choices, missing SMD/BMP files, skeleton divergence, non-single-weight SMD vertices, material-token mismatches, invalid BMP encodings, mismatched skin rows/dimensions, invalid FPS/motion/events, missing bone references, and invalid bounds.
