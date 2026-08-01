# Maintained Fixture Entry Points

Use fixtures only after shared toolchain changes. Give each run a fresh artifact directory outside every Skill and repository tree, and run Blender scripts through the same live 5.2 MCP session unless the test explicitly requires a clean temporary profile. Fixture outputs belong in the current thread workspace or system temp, never under the repository root.

## Fixture Matrix

| Capability | Entry point | Required evidence |
|---|---|---|
| Clean Extension installation | `scripts/extension_smoke_test.py` | one namespaced module, Pillow, no UI/legacy dependency, unified errors |
| Textured skeletal end to end | `scripts/extension_fixture.py` | all five public stages, MDL v10, Action readback, five-point playback-ready Blend |
| Bodygroup/skin/metadata | `scripts/extension_feature_fixture.py` | non-empty/blank choices, every skin family, controller, attachment, hitbox, special flags |
| Adaptive rigid-body API | `scripts/extension_feature_fixture.py` | explicit world, settlement window, receiver bounds, no final kinematic body |
| Matrix transfer regression | `scripts/blender_rigidbody_transfer_regression.py` | parent/rest-space evaluated vertex agreement |
| Event-chain evaluator | `scripts/fixtures/rigidbody_event_chain.py` and unit tests | contact order, motion ownership, penetration, response, settlement |
| Physics stress suite | independent saved artifacts for six prompts | full phase reports and inspected author/readback frames |

## Live Pattern

For the basic fixture:

```python
import os
import runpy

os.environ["GOLDSRC_EXTENSION_FIXTURE"] = r"<fresh-artifact-dir>"
runpy.run_path(r"<skill-dir>\scripts\extension_fixture.py", run_name="__main__")
```

For feature and rigid-body coverage:

```python
import os
import runpy

os.environ["GOLDSRC_EXTENSION_FEATURE_FIXTURE"] = r"<fresh-artifact-dir>"
runpy.run_path(r"<skill-dir>\scripts\extension_feature_fixture.py", run_name="__main__")
```

Both scripts call `bpy.ops.goldsrc_toolchain.execute_stage`; they do not import old add-ons. Keep their geometry synthetic and deterministic. Never use fixture builders as production asset templates.

## Clean Install Pattern

1. Build the Extension ZIP with `scripts/build_extension.py`.
2. Set temporary Blender user/config/data/cache directories.
3. Install into `user_default` and enable `bl_ext.user_default.goldsrc_model_toolchain`.
4. Run `scripts/extension_smoke_test.py` under that profile.
5. Confirm no file from the user's normal add-on directory was required.

## Regression Rules

- A basic fixture proves animation; a controller fixture may use a static sequence when StudioMDL controller semantics suppress root position channels.
- Compare structure and transforms, not byte-for-byte SMD or MDL identity.
- Repeated readback must not append `.001` names.
- Every shared fix needs a unit test, Blender fixture assertion, or preserved independent stress-scene regression.
- Do not rerun six expensive physics scenes when a source/input fingerprint proves the unchanged artifact is still valid; do rerun the affected stage and a forward independent scene after physics logic changes.
