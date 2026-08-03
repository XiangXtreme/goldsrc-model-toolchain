## Change summary

<!-- Describe the Skill, Plugin, or workspace behavior that changed. -->

## Validation

- [ ] `python scripts/validate_workspace.py`
- [ ] `python -m unittest discover -s skill/build-goldsrc-models/.github/tests -v`
- [ ] `python -m unittest discover -s scripts/tests -v`
- [ ] `python scripts/audit_repository.py`
- [ ] `git diff --check`

## Compatibility and release impact

- [ ] Skill and Plugin compatibility was checked.
- [ ] `workspace-manifest.json` and release metadata were updated when needed.
- [ ] No generated model, Blender, or runtime artifact is included.
