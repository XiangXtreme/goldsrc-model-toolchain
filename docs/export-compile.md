# Extension Export And StudioMDL Compilation

## Contract-Driven Export

Run the Extension stage through the live session:

```python
result = bpy.ops.goldsrc_toolchain.execute_stage(
    stage="EXPORT",
    contract_path=contract_path,
    artifacts_dir=artifacts_dir,
    report_path="export.json",
)
assert result == {"FINISHED"}
```

The exporter resolves only contract-owned Blender data:

- each `bodies[].object` to a real mesh object;
- each non-blank `bodygroups[].choices[].object` to a real mesh object;
- each `sequences[].action` to a real Action and compatible Armature;
- each material/image hint to an exact declared texture token.

Missing objects or Actions are hard failures. The exporter does not use collection export flags, `scene.vs`, legacy export lists, or state stored by another add-on.

For every reference SMD, evaluate final modifiers, triangulate loop triangles, export one `1.0` bone owner per vertex, and emit the declared BMP token. For every animation SMD, bind the declared Action, sample its declared frame range, and write local skeleton matrices in parent order. Restore the original scene frame and Action binding after export.

Generated/loaded texture pixels are converted to indexed BMP inside the Extension. Pillow `12.3.0` is preferred; the Blender image fallback must produce the same legal 8-bit indexed structure deterministically. Validate dimensions, 256-entry palette, masked index 255, used colors, and visible luminance before compilation.

## Large Textures And SMD Budgets

GoldSrc MDL texture records remain limited to `512x512` in the conservative Half-Life/Counter-Strike route. Declare a source atlas in `large_textures` when the author image is larger than 512 pixels:

```json
{
  "name": "terrain_base.bmp",
  "image": "TerrainBase_2K",
  "width": 2048,
  "height": 2048,
  "tile_size": 512,
  "modes": ["nomips"]
}
```

The contract expands this logical record to sixteen `512x512` texture records. EXPORT reads the file-backed atlas or Blender RGBA buffer, crops bottom-origin tiles, emits indexed BMPs, and rewrites the SMD material token to the tile filename. Triangles crossing a tile boundary are UV-clipped and fan-triangulated before their local UVs are written. A single MDL supports at most 64 declared tiles; a larger atlas needs multiple deliverables and is rejected by the current contract.

Each reference SMD is then measured by compiled `(bone, position)` vertices, `(bone, normal)` entries, and triangles. When one source exceeds `2048`, `2048`, or `20000`, EXPORT preserves triangle order and emits `*_partNNN.smd` files. COMPILE adds those parts as separate always-present `$body` entries and INSPECT checks the resulting bodyparts. This is a per-submodel split, not a raw text slice; `smdcutpy.py` does not handle compiled deduplication, UV clipping, bones, or QC contract updates.

## Stage Interfaces

The public operator supports only `PREFLIGHT`, `EXPORT`, `COMPILE`, `INSPECT`, and `ROUNDTRIP`. Every invocation executes one stage and writes one JSON report inside `artifacts_dir`. A report path escaping that directory is rejected.

Host compatibility CLIs call the same Extension core:

```powershell
python scripts/compile_model.py <contract> --artifacts <dir>
python scripts/inspect_model.py <contract> --artifacts <dir>
```

They are wrappers, not alternate compilers or parsers.

## Compiler Route

`COMPILE` renders QC from the normalized contract and invokes the bundled Sven StudioMDL. Keep the exact contract `model_name`; do not append a compiler suffix. Record compiler path, return code, bounded logs, QC path, MDL path, animation budget, and immediate contract inspection.

Sven StudioMDL is used for corrected UV/attachment behavior while the contract target remains authoritative. A `half-life-cs` model must stay within Half-Life/Counter-Strike budgets and directives even if Sven accepts more.

## Independent Inspection

`INSPECT` uses the project-owned `core/mdl_v10.py` parser to validate:

- `IDST` version 10;
- bones, sequences, events, bodyparts/bodygroups, controllers, hitboxes, attachments;
- embedded textures, dimensions, flags, skin references, and every skin family;
- explicit bbox/cbox;
- decoded animation channels against source SMD global transforms.

`ROUNDTRIP` deliberately uses a separate SourceIO-derived GoldSrc-only reader. Do not merge it with `core/mdl_v10.py`; shared parsing would make the acceptance claim circular. It reconstructs meshes, armature, embedded textures, bodygroups, all skin-family metadata, and embedded sequence Actions, then saves five-point previews, one labeled contact sheet per Action, and a playback-ready Blend. Each contact sheet has a JSON layout sidecar containing source paths, hashes, frame labels, and non-overlapping image/caption rectangles; original stills remain authoritative for detailed inspection.

External sequence groups remain a limitation only when explicitly declared by the contract. Missing embedded Actions or later skin families are regressions, not default blockers.

## Texture Modes

Sven StudioMDL writes `$texrendermode` flags. Verify the binary table rather than inferring success from QC output. Supported contract modes are `flatshade`, `chrome`, `fullbright`, `nomips`, `alpha`, `additive`, and `masked`; target-engine compatibility remains separate from compiler acceptance.

Contract v2 also accepts deterministic `$renamebone` mappings and an optional player/NPC compatibility baseline. `INSPECT` compares the compiled candidate with that independent MDL v10 baseline. Runtime callers can invoke `validate_model_compatibility(candidate_mdl, baseline_mdl, role)` directly, and can validate the separate `164x200` indexed player portrait with `validate_player_portrait(path, remapped=False)`.
