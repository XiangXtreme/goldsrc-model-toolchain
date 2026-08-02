# Production Workflow

## Artifact Boundary

Use two separate roots:

- The internal artifact directory stores the contract, Blend checkpoints, SMD, indexed BMP, QC, compiled MDL, stage JSON, still review renders, logs, and final report. Author scripts may live beside it. This directory is private working state and may be retained for recovery, but it must be outside every Skill directory tree.
- The delivery directory contains only the files explicitly requested by the user. Populate it after validation passes by copying from the internal artifact directory. Never point `spec.artifacts` at the delivery directory.
- Scope searches and path resolution to the active artifact root, pipeline spec directory, canonical Skill source, and explicit user paths. Sibling Codex thread directories and archived runs are not reference implementations and must not be searched for reusable scene content during independent authoring or forward tests.

Never place generated assets in the Skill folder. Do not create preview video by default; animated-model visual review uses a labeled contact sheet for scanning, the bounded five-point stills for full-resolution evidence, and sequence-table inspection. Include quarter-cycle frames because start/mid/end can all be the same neutral pose in a symmetric loop. For physics, use event labels such as `INTACT`, `CONTACT`, `RESPONSE`, and `SETTLED` rather than implying equal time spacing. If the request is only for one self-contained MDL, the delivery directory must contain that `.mdl` and nothing else.

## Deterministic CLIs

```powershell
python scripts/check_environment.py
python scripts/model_contract_cli.py validate <contract>
python scripts/compile_model.py <contract> --artifacts <dir>
python scripts/inspect_model.py <contract> --artifacts <dir>
python scripts/validate_model.py <contract> --artifacts <dir> --preview <author.png> --preview <roundtrip.png>
python scripts/run_model_pipeline.py <pipeline.json>
```

`compile_model.py` uses the bundled Sven StudioMDL and writes the model with the exact contract filename.

Run `PREFLIGHT`, `EXPORT`, and `ROUNDTRIP` through `bpy.ops.goldsrc_toolchain.execute_stage` in the live MCP session, not a second authoring process. The host scripts are compatibility wrappers around the same Extension implementation.

The independent round-trip stage owns an isolated readback scene. Before import it rejects remaining Bullet ownership, removes the prior readback namespace, reconstructs the MDL through the SourceIO-derived GoldSrc-only reader, and rejects numeric suffix collisions. This prevents repeated readbacks from appending `.001` without bypassing physics teardown order.

## Pipeline Specification

The JSON root contains `artifacts`, `contract`, optional shared `environment`, and `stages`. Paths support `{artifacts}`, `{skill}`, and `{spec_dir}` substitutions.

Each stage contains:

- Unique `name` and one ordered `phase`.
- `runner`: `blender_mcp`, `python`, `runpy`, or `reuse_report`.
- `script`, declared `inputs`, and at least one declared `output` inside the artifact directory.
- A `reuse_report` stage replaces `script` with `source_report` and must declare exactly one JSON output. It copies a previously passing report without rerunning authoring, physics, or Blender; the source must remain inside `artifacts`, and its content is part of the stage fingerprint.
- Optional `blend_checkpoint` for a `blender_mcp` stage. The runner loads it in a separate MCP request before executing the stage, preserving Blender operator context.
- Optional `args`, `environment`, `timeout_seconds`, and `clean_outputs`.
- Optional `result_json` naming one declared output. Its status must be `pass` or `pass_with_known_blockers`.

For a version 2 contract, result JSON from each phase named by a requirement's `evidence_phases` must include a `requirement_evidence` entry with the requirement `id`, `status`, a concise `summary`, and non-empty task evidence. The pipeline matches evidence by ID and phase. It rejects unknown IDs, malformed evidence, failed evidence, and any requirement missing evidence from a declared phase.

Make every `blender_mcp` stage independent of the current interactive scene. Declare `blend_checkpoint` for preflight and export; do not open it inside the stage script or trust scene names/custom marker properties. A cached prior stage may leave Blender on an unrelated scene, while opening and running an operator in one MCP callback can invalidate Blender's context.

Treat the saved author `.blend` as the recovery boundary. Before a full author rerun, release Bullet ownership in dependency order, then use `api.purge_asset_namespace` with explicit asset-owned names. Do not clear unrelated scene data. Rebuild, require `api.assert_exact_asset_namespace`, bind a contract Action, restore `scene.frame_start`, save, reopen through `blend_checkpoint`, and pass preflight before export.

The default phase order is `environment`, `author`, `preflight`, `export`, `compile_sven`, `mdl_inspect`, `sourceio_roundtrip`, `visual_review`. A versioned contract requires every phase named by `acceptance.required_phases` in this order.

The cache fingerprints the pipeline runner, stage declaration, stage script, declared inputs, and preceding result. Non-environment stages also fingerprint the contract, thin host bridge, complete Extension tree, and tool manifest. The environment phase deliberately excludes content inputs so contract or authoring changes cannot trigger another warm-up. Starting an invalidated stage clears its cached state and every later stage. Use `--force-stage <name>` only for an intentional rerun and `--no-cache` for diagnosis.

Use `reuse_report` only when the referenced report still describes the valid author checkpoint and unchanged upstream content. Its first execution is reported as `reused`; later unchanged runs are reported as `cached`. A reused report is a control-flow optimization, not a new authoring or physics result. If the checkpoint, contract, physics inputs, or source evidence changed, rerun the earliest affected stage instead.

Keep one responsive Blender 5.2 MCP process alive for the production run. Check or repair the environment once per active session, then resume from the earliest invalidated content stage. Do not reinstall add-ons, relaunch Blender, or rerun upstream stages merely because a downstream script, contract, texture, mesh, or animation changed.

For physics assets, keep the author checkpoint's solver audit with the simulation report. The audit must identify declared kinematic drivers and prove that all other rigid-body transforms were sampled from evaluated Blender matrices; no post-bake path replacement is allowed. Before a rerun, purge old rigid-body worlds, collections, Actions, and caches so a stale collision world cannot masquerade as a new solve.

Before export, treat preflight diagnostics as repair clues: when Blender appends a numeric suffix such as `.001`, compare the suffixed object or bone with its unsuffixed contract name before changing the contract; when a material is unknown, use the reported image filename hints to locate the intended texture, then fix the material assignment or contract explicitly. Hints never turn an unknown name into a pass.

Before compilation, record the SMD animation budget hint for every sequence. It estimates frame-count, bone-count, and channel-density pressure and may suggest a `sample_step`, but it is not a compiler result. Never reduce sampling solely to hide a timing or motion failure; any permitted downsampling must preserve the declared duration/FPS contract and be revalidated against the exported animation.

Treat declared outputs as immutable after their stage finishes. A cache hit re-hashes every output and rejects missing or modified evidence; downstream stages must write new files rather than save changes back into an upstream Blend checkpoint.

Every `export` phase automatically reloads the contract with `require_files=True` after outputs exist and before the stage is cached as passing. The same postcondition runs on cache hits, so a tampered SMD, skeleton, material token, texture, frame range, or artifact path cannot reach compilation behind a stale stage report.

## Report Contract

The final report always includes `status`, `contract_version`, `target_profile`, `issues`, `known_blockers`, `stages`, `requirements`, `revision`, and `claims`.

- `pass`: every required stage passed without blockers.
- `pass_with_known_blockers`: required stages passed and every limitation is listed and contract-allowed.
- `fail`: at least one phase or evidence report failed; success claims remain false.

`requirements` retains the literal source phrase, required phases, and all task-local evidence for every version 2 requirement. `revision` records the baseline, changed factors, and preserved surface for an incremental request. These fields prove coverage; they do not authorize the agent to add an unstated effect or convert a measurement into a universal target.

Compiler success alone never grants compatibility. Structured MDL inspection, independent GoldSrc readback, and visual review make separate claims.

## Delivery Gate

After the report passes, resolve the requested deliverables as an explicit whitelist and copy only those files to the delivery directory. Internal `.blend`, SMD, QC, BMP, JSON, logs, PNGs, and authoring scripts remain under the artifact root unless individually requested. Do not create README, ZIP, MP4, GIF, or other convenience files without an explicit request.
