#!/usr/bin/env python3
"""Validate a GoldSrc model export and its Blender round-trip evidence."""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import zlib
from pathlib import Path


class Report:
    def __init__(self) -> None:
        self.checks: dict[str, object] = {}
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def warn(self, condition: bool, message: str) -> None:
        if not condition:
            self.warnings.append(message)


def read_json(path: Path, report: Report) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少阶段文件: {path.name}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.errors.append(f"阶段文件无效: {path.name}: {exc}")
        return {}
    report.require("FINISHED" in value.get("operator_result", []), f"阶段未成功: {path.name}")
    return value


def validate_smd(path: Path, root: Path, report: Report) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少 SMD: {path.name}")
        return {}
    lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    report.require(bool(lines) and lines[0].lower() == "version 1", f"{path.name} 不是 SMD version 1")
    try:
        start = lines.index("triangles") + 1
        end = lines.index("end", start)
    except ValueError:
        report.errors.append(f"{path.name} 缺少完整 triangles 段")
        return {}

    body = lines[start:end]
    report.require(len(body) % 4 == 0, f"{path.name} triangles 段不是材质行加 3 顶点结构")
    triangle_count = len(body) // 4
    materials: set[str] = set()
    invalid_vertices = 0
    extra_weights = 0
    missing_textures: set[str] = set()
    positions: list[tuple[float, float, float]] = []
    for offset in range(0, len(body) - 3, 4):
        material = body[offset]
        materials.add(material)
        if Path(material).suffix.lower() != ".bmp":
            report.errors.append(f"SMD 材质不是 BMP 名称: {material}")
        if not (root / material).is_file():
            missing_textures.add(material)
        for vertex in body[offset + 1 : offset + 4]:
            fields = vertex.split()
            if len(fields) < 9:
                invalid_vertices += 1
                continue
            try:
                parent = int(fields[0])
                values = [float(value) for value in fields[1:9]]
                positions.append((values[0], values[1], values[2]))
                if parent < 0 or not all(math.isfinite(value) for value in values):
                    invalid_vertices += 1
                if len(fields) > 9 and int(fields[9]) > 0:
                    extra_weights += 1
            except ValueError:
                invalid_vertices += 1
    report.require(triangle_count > 0, f"{path.name} 没有三角形")
    report.require(invalid_vertices == 0, f"{path.name} 有 {invalid_vertices} 个无效顶点")
    report.require(extra_weights == 0, f"{path.name} 有 {extra_weights} 个多权重顶点，GoldSrc 只支持单骨权重")
    report.require(not missing_textures, f"SMD 引用缺失纹理: {', '.join(sorted(missing_textures))}")
    bounds = []
    if positions:
        bounds = [min(point[axis] for point in positions) for axis in range(3)]
        bounds.extend(max(point[axis] for point in positions) for axis in range(3))
    return {
        "triangles": triangle_count,
        "vertex_records": triangle_count * 3,
        "materials": sorted(materials),
        "single_bone_weights": extra_weights == 0,
        "bounds": bounds,
    }


def validate_bmp(path: Path, report: Report) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少 BMP: {path.name}")
        return {}
    data = path.read_bytes()
    if len(data) < 54:
        report.errors.append(f"BMP 太短: {path.name}")
        return {}
    signature, declared_size, _, _, pixel_offset = struct.unpack_from("<2sIHHI", data, 0)
    dib_size = struct.unpack_from("<I", data, 14)[0]
    width, height, planes, bits_per_pixel, compression = struct.unpack_from("<iiHHI", data, 18)
    colors_used = struct.unpack_from("<I", data, 46)[0] if dib_size >= 40 else 0
    palette_entries = colors_used or (1 << bits_per_pixel if bits_per_pixel <= 8 else 0)
    report.require(signature == b"BM", f"{path.name} 缺少 BM 文件头")
    report.require(declared_size == len(data), f"{path.name} 声明长度与实际长度不一致")
    report.require(planes == 1, f"{path.name} planes 必须为 1")
    report.require(bits_per_pixel == 8, f"{path.name} 必须是 8-bit indexed BMP，当前为 {bits_per_pixel}-bit")
    report.require(compression == 0, f"{path.name} 必须为未压缩 BMP")
    report.require(0 < width <= 512 and 0 < abs(height) <= 512, f"{path.name} 尺寸超出 GoldSrc 512 上限")
    report.require(width % 16 == 0 and abs(height) % 16 == 0, f"{path.name} 宽高必须是 16 的倍数")
    report.require(palette_entries == 256, f"{path.name} 必须包含 256 色调色板")
    report.require(pixel_offset >= 14 + dib_size + palette_entries * 4, f"{path.name} 调色板或像素偏移无效")
    report.warn(not (width == 512 and abs(height) == 512), f"{path.name} 为 512x512；当前转换器可能把小纹理无条件放大，增加 MDL 体积")
    return {
        "width": width,
        "height": abs(height),
        "bits_per_pixel": bits_per_pixel,
        "palette_entries": palette_entries,
    }


def validate_qc(path: Path, root: Path, report: Report, expected_scale: float) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少 QC: {path.name}")
        return {}
    text = path.read_text(encoding="utf-8-sig")
    commands = []
    references = []
    scale_value = None
    boxes: dict[str, list[float]] = {}
    allowed = {"modelname", "cd", "cdtexture", "scale", "bbox", "cbox", "body", "sequence"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("$"):
            continue
        match = re.match(r"\$(\w+)\b(.*)", line)
        if not match:
            continue
        command, arguments = match.group(1).lower(), match.group(2).strip()
        commands.append(command)
        report.require(command in allowed, f"QC 含未审计命令: ${command}")
        if command == "body":
            quoted = re.findall(r'"([^"]+)"', arguments)
            if quoted:
                references.append(quoted[-1] + ".smd")
        elif command == "sequence":
            quoted = re.findall(r'"([^"]+)"', arguments)
            if len(quoted) >= 2:
                references.append(quoted[1] + ".smd")
        elif command == "scale":
            try:
                scale = float(arguments.split()[0])
                scale_value = scale
                report.warn(math.isclose(scale, expected_scale), f"QC 使用 $scale {scale:g}，预期为 {expected_scale:g}")
            except (ValueError, IndexError):
                report.errors.append("QC 的 $scale 无效")
        elif command in {"bbox", "cbox"}:
            try:
                values = [float(value) for value in arguments.split()]
                boxes[command] = values
                report.require(len(values) == 6 and all(math.isfinite(v) for v in values), f"QC 的 ${command} 无效")
                report.warn(any(value != 0 for value in values), f"QC 的 ${command} 全为 0；碰撞/剔除边界需要人工或脚本计算")
            except ValueError:
                report.errors.append(f"QC 的 ${command} 含非数值参数")
    missing = [reference for reference in references if not (root / reference).is_file()]
    report.require(not missing, f"QC 引用缺失: {', '.join(missing)}")
    return {"commands": commands, "references": references, "scale": scale_value, **boxes}


def validate_mdl(path: Path, report: Report) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少 MDL: {path.name}")
        return {}
    data = path.read_bytes()
    if len(data) < 220:
        report.errors.append(f"MDL 太短: {path.name}")
        return {}
    magic, version = struct.unpack_from("<4si", data, 0)
    declared_length = struct.unpack_from("<i", data, 72)[0]
    counts = {
        "bones": struct.unpack_from("<i", data, 140)[0],
        "sequences": struct.unpack_from("<i", data, 164)[0],
        "textures": struct.unpack_from("<i", data, 180)[0],
        "skin_references": struct.unpack_from("<i", data, 192)[0],
        "body_parts": struct.unpack_from("<i", data, 204)[0],
        "attachments": struct.unpack_from("<i", data, 212)[0],
    }
    report.require(magic == b"IDST", f"{path.name} magic 不是 IDST")
    report.require(version == 10, f"{path.name} 不是 GoldSrc MDL version 10")
    report.require(declared_length == len(data), f"{path.name} 声明长度与实际长度不一致")
    for name in ("bones", "sequences", "textures", "body_parts"):
        report.require(counts[name] > 0, f"{path.name} 的 {name} 计数无效")
    return {"version": version, "bytes": len(data), **counts}


def png_pixel_stats(path: Path, report: Report) -> dict:
    if not path.is_file():
        report.errors.append(f"缺少预览图: {path.name}")
        return {}
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        report.errors.append(f"预览图不是 PNG: {path.name}")
        return {}
    pos = 8
    width = height = color_type = bit_depth = 0
    compressed = bytearray()
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack_from(">IIBB", payload, 0)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    report.require(width >= 64 and height >= 64, f"{path.name} 分辨率过小")
    report.require(bit_depth == 8 and color_type in {0, 2, 4, 6}, f"{path.name} 使用验收器不支持的 PNG 像素格式")
    try:
        raw = zlib.decompress(compressed)
    except zlib.error as exc:
        report.errors.append(f"{path.name} PNG 数据损坏: {exc}")
        return {"width": width, "height": height}
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type, 0)
    stride = width * channels
    expected = height * (stride + 1)
    report.require(len(raw) == expected, f"{path.name} 解压像素长度异常")
    if not channels or len(raw) != expected:
        return {"width": width, "height": height}
    previous = bytearray(stride)
    values = bytearray()
    for row_index in range(height):
        row_start = row_index * (stride + 1)
        filter_type = raw[row_start]
        scan = bytearray(raw[row_start + 1 : row_start + 1 + stride])
        for i in range(stride):
            left = scan[i - channels] if i >= channels else 0
            up = previous[i]
            upper_left = previous[i - channels] if i >= channels else 0
            if filter_type == 1:
                scan[i] = (scan[i] + left) & 255
            elif filter_type == 2:
                scan[i] = (scan[i] + up) & 255
            elif filter_type == 3:
                scan[i] = (scan[i] + ((left + up) // 2)) & 255
            elif filter_type == 4:
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
                scan[i] = (scan[i] + predictor) & 255
            elif filter_type != 0:
                report.errors.append(f"{path.name} 含未知 PNG filter {filter_type}")
                return {"width": width, "height": height}
        values.extend(scan)
        previous = scan
    luminance = []
    for i in range(0, len(values), channels):
        if color_type in {0, 4}:
            luminance.append(values[i])
        else:
            luminance.append((54 * values[i] + 183 * values[i + 1] + 19 * values[i + 2]) >> 8)
    dynamic_range = max(luminance) - min(luminance)
    non_dark_fraction = sum(value > 20 for value in luminance) / len(luminance)
    report.require(dynamic_range >= 20 and non_dark_fraction >= 0.01, f"{path.name} 疑似空白或全黑")
    return {
        "width": width,
        "height": height,
        "luminance_range": dynamic_range,
        "non_dark_fraction": round(non_dark_fraction, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", type=Path)
    parser.add_argument("--model-base", default="Collection")
    parser.add_argument("--expected-scale", type=float, default=15.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    from goldsrc_toolchain.paths import resolve_artifact_root

    root = resolve_artifact_root(args.artifacts)
    report = Report()

    blender_stage = read_json(root / "blender_stage.json", report)
    roundtrip_stage = read_json(root / "roundtrip_stage.json", report)
    smd_name = f"{args.model_base}.smd"
    qc_name = f"{args.model_base}.qc"
    mdl_name = f"{args.model_base}.mdl"
    smd = validate_smd(root / smd_name, root, report)
    bmp = {
        material: validate_bmp(root / material, report)
        for material in smd.get("materials", [])
    }
    qc = validate_qc(root / qc_name, root, report, args.expected_scale)
    mdl = validate_mdl(root / mdl_name, report)
    previews = {
        "blender": png_pixel_stats(root / "blender_preview.png", report),
        "roundtrip": png_pixel_stats(root / "mdl_roundtrip_preview.png", report),
    }

    if smd and roundtrip_stage:
        report.require(smd["triangles"] == roundtrip_stage.get("polygons"), "SMD 三角形数与 MDL 回读面数不一致")
        imported = roundtrip_stage.get("materials", [])
        for material in smd["materials"]:
            report.require(any(name.endswith(material) for name in imported), f"MDL 回读缺少材质: {material}")
        report.require(roundtrip_stage.get("mesh_objects", 0) > 0, "MDL 回读没有网格对象")
        report.require(roundtrip_stage.get("vertices", 0) > 0, "MDL 回读没有顶点")
    if smd.get("bounds") and qc.get("scale") is not None:
        expected_bounds = [value * qc["scale"] for value in smd["bounds"]]
        for command in ("bbox", "cbox"):
            actual = qc.get(command, [])
            matches = len(actual) == 6 and all(math.isclose(a, b, abs_tol=1e-4) for a, b in zip(actual, expected_bounds))
            report.require(matches, f"QC 的 ${command} 与 SMD bounds × scale 不一致")
    expected_files = set(blender_stage.get("artifacts", []))
    required_files = {smd_name, qc_name, mdl_name, *smd.get("materials", [])}
    for required in required_files:
        report.require(required in expected_files or (root / required).is_file(), f"制作阶段缺少必需产物: {required}")

    report.checks = {
        "stages": {"blender": blender_stage, "roundtrip": roundtrip_stage},
        "smd": smd,
        "bmp": bmp,
        "qc": qc,
        "mdl": mdl,
        "previews": previews,
    }
    result = {
        "status": "pass" if not report.errors else "fail",
        "errors": report.errors,
        "warnings": report.warnings,
        "checks": report.checks,
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
