#!/usr/bin/env python3
"""Aggregate contract, compile, MDL, SourceIO, and preview evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageStat

from goldsrc_toolchain.mdl_v10 import inspect_mdl, validate_mdl_contract
from goldsrc_toolchain.model_contract import ContractError, contract_summary, load_contract
from goldsrc_toolchain.paths import ensure_outside_skill_tree, resolve_artifact_root
from goldsrc_toolchain.textures import validate_indexed_bmp


def _read_report(path: Path, label: str, issues: list[dict]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        issues.append({"severity": "error", "code": f"{label}.missing", "message": str(exc), "context": {"path": str(path)}})
        return {}
    if value.get("status") not in {"pass", "pass_with_known_blockers"}:
        issues.append({"severity": "error", "code": f"{label}.failed", "message": f"{label} report is not passing", "context": {"status": value.get("status")}})
    return value


def _preview(path: Path, issues: list[dict]) -> dict:
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            statistics = ImageStat.Stat(rgb)
            extrema = statistics.extrema
            dynamic = max(high for _low, high in extrema) - min(low for low, _high in extrema)
            mean_luminance = sum(value * weight for value, weight in zip(statistics.mean, (0.2126, 0.7152, 0.0722)))
            pixels = list(rgb.getdata())
            border = []
            for x in range(rgb.width):
                border.extend((rgb.getpixel((x, 0)), rgb.getpixel((x, rgb.height - 1))))
            for y in range(1, rgb.height - 1):
                border.extend((rgb.getpixel((0, y)), rgb.getpixel((rgb.width - 1, y))))
            background = tuple(sorted(pixel[channel] for pixel in border)[len(border) // 2] for channel in range(3))
            mask_values = [255 if max(abs(pixel[channel] - background[channel]) for channel in range(3)) >= 12 else 0 for pixel in pixels]
            foreground_count = sum(value != 0 for value in mask_values)
            foreground_ratio = foreground_count / len(mask_values)
            foreground_luminance = None
            foreground_bbox = None
            if foreground_count:
                foreground_luminance = sum(
                    sum(pixel[channel] * weight for channel, weight in enumerate((0.2126, 0.7152, 0.0722)))
                    for pixel, selected in zip(pixels, mask_values)
                    if selected
                ) / foreground_count
                mask = Image.new("L", rgb.size)
                mask.putdata(mask_values)
                foreground_bbox = list(mask.getbbox()) if mask.getbbox() else None
            facts = {
                "path": str(path),
                "width": rgb.width,
                "height": rgb.height,
                "dynamic_range": dynamic,
                "mean_luminance": round(mean_luminance, 3),
                "estimated_background_rgb": list(background),
                "foreground_ratio": round(foreground_ratio, 6),
                "foreground_bbox": foreground_bbox,
                "foreground_mean_luminance": round(foreground_luminance, 3) if foreground_luminance is not None else None,
            }
            if rgb.width < 64 or rgb.height < 64 or dynamic < 20 or foreground_ratio < 0.002:
                issues.append({"severity": "error", "code": "visual.blank", "message": f"preview is too small or visually blank: {path.name}", "context": facts})
            elif foreground_luminance is not None and foreground_luminance < 12:
                issues.append({"severity": "warning", "code": "visual.dark_foreground", "message": f"preview foreground is very dark and needs visual review: {path.name}", "context": facts})
            return facts
    except (OSError, ValueError) as exc:
        issues.append({"severity": "error", "code": "visual.invalid", "message": str(exc), "context": {"path": str(path)}})
        return {"path": str(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--sven-report", type=Path)
    parser.add_argument("--roundtrip", type=Path)
    parser.add_argument("--preview", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    contract_path = args.contract.expanduser().resolve()
    try:
        root = resolve_artifact_root(args.artifacts or contract_path.parent)
        output = ensure_outside_skill_tree(
            args.output or root / "acceptance_report.json", label="Acceptance report",
        )
    except ValueError as exc:
        parser.error(str(exc))
    issues: list[dict] = []
    blockers: list[dict] = []
    evidence = {}
    contract = None
    try:
        contract = load_contract(contract_path, artifact_dir=root, require_files=True)
        evidence["contract"] = contract_summary(contract)
        reports = {
            "preflight": args.preflight or root / "preflight.json",
            "compile_sven": args.sven_report or root / "compile_sven.json",
            "roundtrip": args.roundtrip or root / "roundtrip_stage.json",
        }
        for label, path in reports.items():
            value = _read_report(path.expanduser().resolve(), label, issues)
            evidence[label] = value
            blockers.extend(value.get("known_blockers", []))
            issues.extend(item for item in value.get("issues", []) if isinstance(item, dict))
        inspection = inspect_mdl(root / contract["outputs"]["sven_mdl"])
        binary_issues = validate_mdl_contract(inspection, contract)
        issues.extend(binary_issues)
        evidence["mdl_sven"] = inspection
        texture_evidence = []
        for texture in contract["textures"]:
            facts = validate_indexed_bmp(
                root / texture.get("source", texture["name"]),
                width=texture.get("width"),
                height=texture.get("height"),
                modes=texture.get("modes", []),
            )
            texture_evidence.append(facts)
            for risk in facts.get("risk_labels", []):
                issues.append({
                    "severity": "warning",
                    "code": f"texture.{risk}",
                    "message": f"indexed texture needs visual review: {texture['name']} ({risk})",
                    "context": {"texture": texture["name"], "facts": facts},
                })
        evidence["textures"] = texture_evidence
        if args.preview:
            previews = args.preview
        else:
            previews = [root / "author_preview.png"]
            roundtrip_previews = evidence.get("roundtrip", {}).get("previews", [])
            previews.extend(
                Path(item["path"])
                for item in roundtrip_previews
                if isinstance(item, dict) and isinstance(item.get("path"), str)
            )
            if len(previews) == 1:
                previews.append(root / "mdl_roundtrip_preview.png")
        previews = list(dict.fromkeys(path.expanduser().resolve() for path in previews))
        evidence["previews"] = [_preview(path, issues) for path in previews]
    except (ContractError, OSError, ValueError) as exc:
        issues.extend({"severity": "error", "code": "acceptance.exception", "message": message, "context": {}} for message in getattr(exc, "errors", [str(exc)]))
    blockers = list({item.get("code", json.dumps(item, sort_keys=True)): item for item in blockers}.values())
    status = "fail" if any(item.get("severity") == "error" for item in issues) else "pass_with_known_blockers" if blockers else "pass"
    report = {
        "status": status,
        "contract_version": contract["version"] if contract else 1,
        "target_profile": contract["target_profile"] if contract else "half-life-cs",
        "issues": issues,
        "known_blockers": blockers,
        "claims": {
            "sven_compiled": bool(evidence.get("compile_sven", {}).get("status") == "pass"),
            "sourceio_geometry_roundtrip": evidence.get("roundtrip", {}).get("status") in {"pass", "pass_with_known_blockers"},
            "visual_evidence_nonblank": bool(evidence.get("previews")) and not any(item.get("code", "").startswith("visual.") for item in issues),
            "in_game_validated": False,
        },
        "evidence": evidence,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("status", "issues", "known_blockers", "claims")}, indent=2, ensure_ascii=False))
    return 0 if status in {"pass", "pass_with_known_blockers"} else 1


if __name__ == "__main__":
    sys.exit(main())
