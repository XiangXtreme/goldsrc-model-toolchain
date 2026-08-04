# Workspace Scripts

This directory is repository tooling, not Blender Extension source. The Extension builder receives only `plugin/goldsrc_model_toolchain/`, so none of these files are included in the installed ZIP or imported by the runtime.

The Python files have four roles:

- Validation and release checks: `validate_workspace.py`, `audit_repository.py`, `audit_release_archives.py`, and `build_extension.py`.
- Local environment and installation: `bootstrap_environment.py`, `check_environment.py`, `configure_blender_addons.py`, `resolve_toolchain.py`, and `sync_install.py`.
- Regression fixtures and smoke tests: files ending in `_fixture.py`, `_regression.py`, or `_smoke_test.py`, plus `tests/`.
- Manual API-1 compatibility CLIs: the remaining export, compile, inspect, contract, and round-trip commands. The installed Extension does not call them; the normal selected-static route is `api.export_selected_static(...)`.

Do not move runtime behavior into this directory. Shared runtime behavior belongs under `plugin/goldsrc_model_toolchain/core/`, and Blender-dependent runtime behavior belongs under `plugin/goldsrc_model_toolchain/blender/`.
