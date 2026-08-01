#!/usr/bin/env python3
"""Extract release ZIPs in system temp, audit them, and clean up automatically."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from goldsrc_toolchain.paths import ensure_outside_skill_tree


FORBIDDEN_PARTS = {".claude", ".git", "__pycache__", "artifacts", "dist", "outputs", "work"}


def _validate_member(info: zipfile.ZipInfo) -> str | None:
    path = PurePosixPath(info.filename.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        return f"unsafe archive member: {info.filename}"
    if {part.casefold() for part in path.parts} & FORBIDDEN_PARTS:
        return f"forbidden local/runtime archive member: {info.filename}"
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        return f"symlink archive member: {info.filename}"
    return None


def audit_archive(archive_path: Path, destination: Path) -> dict:
    archive_path = ensure_outside_skill_tree(archive_path, label="Release archive")
    errors: list[str] = []
    with zipfile.ZipFile(archive_path) as archive:
        members = archive.infolist()
        errors.extend(error for info in members if (error := _validate_member(info)))
        skill_manifests = [info.filename for info in members if PurePosixPath(info.filename).name == "SKILL.md"]
        if len(skill_manifests) > 1:
            errors.append(f"archive contains multiple Skill manifests: {skill_manifests}")
        corrupt = archive.testzip()
        if corrupt:
            errors.append(f"corrupt archive member: {corrupt}")
        if not errors:
            archive.extractall(destination)
    extracted = [path for path in destination.rglob("*") if path.is_file()]
    return {
        "archive": str(archive_path),
        "bytes": archive_path.stat().st_size,
        "sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "members": len(members),
        "skill_manifests": skill_manifests,
        "extracted_files": len(extracted),
        "top_level": sorted({path.relative_to(destination).parts[0] for path in extracted}),
        "errors": errors,
        "status": "pass" if not errors else "fail",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", nargs="+", type=Path)
    args = parser.parse_args()
    reports = []
    with tempfile.TemporaryDirectory(prefix="goldsrc-release-audit-") as temporary:
        temporary_root = ensure_outside_skill_tree(temporary, label="ZIP audit directory")
        for index, archive in enumerate(args.archive):
            destination = temporary_root / f"archive_{index}"
            destination.mkdir()
            reports.append(audit_archive(archive, destination))
    result = {
        "status": "pass" if all(item["status"] == "pass" for item in reports) else "fail",
        "temporary_directory_removed": not temporary_root.exists(),
        "archives": reports,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" and result["temporary_directory_removed"] else 1


if __name__ == "__main__":
    sys.exit(main())
