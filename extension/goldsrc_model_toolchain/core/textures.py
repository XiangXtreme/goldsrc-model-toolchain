"""Create and validate GoldSrc-compatible 8-bit indexed BMP textures."""

from __future__ import annotations

import struct
from collections import Counter
from pathlib import Path
from typing import Iterable


class TextureError(ValueError):
    """Raised when a texture cannot be represented by the target profile."""


def _linear_to_srgb(value: float) -> float:
    clamped = max(0.0, min(1.0, float(value)))
    if clamped <= 0.0031308:
        return clamped * 12.92
    return 1.055 * clamped ** (1.0 / 2.4) - 0.055


def _expand_palette_to_256(path: Path) -> None:
    data = bytearray(path.read_bytes())
    dib_size = struct.unpack_from("<I", data, 14)[0]
    bits = struct.unpack_from("<H", data, 28)[0]
    if dib_size < 40 or bits != 8:
        return
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    palette_start = 14 + dib_size
    entries = max(0, (pixel_offset - palette_start) // 4)
    if entries >= 256:
        struct.pack_into("<II", data, 46, 256, 256)
        path.write_bytes(data)
        return
    missing = bytes((256 - entries) * 4)
    expanded = data[:pixel_offset] + missing + data[pixel_offset:]
    struct.pack_into("<I", expanded, 2, len(expanded))
    struct.pack_into("<I", expanded, 10, pixel_offset + len(missing))
    struct.pack_into("<II", expanded, 46, 256, 256)
    path.write_bytes(expanded)


def inspect_indexed_bmp(
    path: Path | str,
    *,
    modes: list[str] | tuple[str, ...] = (),
    require_model_dimensions: bool = True,
) -> dict:
    resolved = Path(path).expanduser().resolve()
    data = resolved.read_bytes()
    if len(data) < 54 or data[:2] != b"BM":
        raise TextureError(f"not a Windows BMP: {resolved}")
    declared_size, pixel_offset = struct.unpack_from("<II", data, 2)[0], struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        raise TextureError(f"OS/2 or unsupported BMP DIB header ({dib_size} bytes): {resolved.name}")
    width, height, planes, bits, compression, image_size, _xppm, _yppm, colors_used, _important = struct.unpack_from(
        "<iiHHIIiiII", data, 18
    )
    palette_entries = colors_used or (1 << bits if bits <= 8 else 0)
    palette_start = 14 + dib_size
    palette_end = palette_start + palette_entries * 4
    if declared_size != len(data):
        raise TextureError(f"BMP declared size differs from file size: {resolved.name}")
    if planes != 1 or bits != 8:
        raise TextureError(f"GoldSrc texture must be 8-bit indexed: {resolved.name}")
    if compression != 0:
        raise TextureError(f"GoldSrc texture must be uncompressed: {resolved.name}")
    if width <= 0 or height == 0 or width > 512 or abs(height) > 512:
        raise TextureError(f"GoldSrc texture dimensions must be 1..512: {resolved.name}")
    if require_model_dimensions and (width % 16 or abs(height) % 16):
        raise TextureError(f"GoldSrc texture dimensions must be multiples of 16: {resolved.name}")
    if palette_entries != 256 or palette_end > pixel_offset or pixel_offset > len(data):
        raise TextureError(f"GoldSrc texture requires a complete 256-color palette: {resolved.name}")
    stride = (width + 3) & ~3
    required_pixels = stride * abs(height)
    if pixel_offset + required_pixels > len(data):
        raise TextureError(f"BMP pixel data is truncated: {resolved.name}")
    palette = [tuple(data[offset : offset + 3][::-1]) for offset in range(palette_start, palette_end, 4)]
    frequencies: Counter[int] = Counter()
    for row in range(abs(height)):
        start = pixel_offset + row * stride
        frequencies.update(data[start : start + width])
    indices = sorted(frequencies)
    masked = "masked" in {mode.casefold() for mode in modes}
    visible_indices = [index for index in indices if not (masked and index == 255)]
    visible_pixels = sum(frequencies[index] for index in visible_indices)
    luminances = {
        index: sum(channel * weight for channel, weight in zip(palette[index], (0.2126, 0.7152, 0.0722)))
        for index in visible_indices
    }
    luminance_min = min(luminances.values()) if luminances else None
    luminance_max = max(luminances.values()) if luminances else None
    weighted_luminance = (
        sum(luminances[index] * frequencies[index] for index in visible_indices) / visible_pixels
        if visible_pixels else None
    )
    risk_labels = []
    if not visible_pixels:
        risk_labels.append("no_visible_pixels")
    if len(visible_indices) == 1:
        risk_labels.append("single_color")
    if luminance_max is not None and luminance_max <= 0.5:
        risk_labels.append("all_visible_pixels_black")
    return {
        "path": str(resolved),
        "width": width,
        "height": abs(height),
        "bits_per_pixel": bits,
        "compression": compression,
        "palette_entries": palette_entries,
        "palette": palette,
        "indices_used": indices,
        "used_color_count": len(visible_indices),
        "pixel_frequencies": {str(index): frequencies[index] for index in indices},
        "visible_pixel_count": visible_pixels,
        "transparent_pixel_count": frequencies[255] if masked else 0,
        "weighted_mean_luminance": round(weighted_luminance, 3) if weighted_luminance is not None else None,
        "luminance_min": round(luminance_min, 3) if luminance_min is not None else None,
        "luminance_max": round(luminance_max, 3) if luminance_max is not None else None,
        "luminance_range": round(luminance_max - luminance_min, 3) if luminance_min is not None else None,
        "risk_labels": risk_labels,
        "file_size": len(data),
        "pixel_offset": pixel_offset,
        "image_size": image_size,
    }


def validate_indexed_bmp(
    path: Path | str,
    *,
    width: int | None = None,
    height: int | None = None,
    modes: list[str] | tuple[str, ...] = (),
    require_masked_pixels: bool = True,
) -> dict:
    facts = inspect_indexed_bmp(path, modes=modes)
    if width is not None and facts["width"] != width:
        raise TextureError(f"texture width is {facts['width']}, expected {width}: {Path(path).name}")
    if height is not None and facts["height"] != height:
        raise TextureError(f"texture height is {facts['height']}, expected {height}: {Path(path).name}")
    if "masked" in {mode.casefold() for mode in modes}:
        if facts["palette"][255] != (0, 0, 255):
            raise TextureError(f"masked texture palette index 255 must be blue: {Path(path).name}")
        if require_masked_pixels and 255 not in facts["indices_used"]:
            raise TextureError(f"masked texture never uses transparent index 255: {Path(path).name}")
    return {key: value for key, value in facts.items() if key != "palette"}


def _write_indexed_bmp_from_rgba(
    rgba: Iterable[float],
    destination: Path,
    *,
    width: int,
    height: int,
    masked: bool,
    alpha_threshold: int,
) -> None:
    """Write a deterministic 3-3-2 indexed BMP without external image packages.

    This is deliberately a small fallback for Blender's embedded Python, where
    the host Pillow installation is not necessarily importable.  The normal
    host-side path still uses Pillow's higher quality quantizer.
    """

    values = list(rgba)
    expected = width * height * 4
    if len(values) != expected:
        raise TextureError(f"Blender image returned {len(values)} channels, expected {expected}")
    indices = bytearray(width * height)
    for pixel in range(width * height):
        red = round(_linear_to_srgb(values[pixel * 4]) * 255.0)
        green = round(_linear_to_srgb(values[pixel * 4 + 1]) * 255.0)
        blue = round(_linear_to_srgb(values[pixel * 4 + 2]) * 255.0)
        alpha = max(0, min(255, round(float(values[pixel * 4 + 3]) * 255.0)))
        if masked and alpha < alpha_threshold:
            indices[pixel] = 255
            continue
        # Reserve palette index 255 for GoldSrc masked blue.
        indices[pixel] = min(254, ((red >> 5) << 5) | ((green >> 5) << 2) | (blue >> 6))

    stride = (width + 3) & ~3
    image_size = stride * height
    pixel_offset = 14 + 40 + 256 * 4
    file_size = pixel_offset + image_size
    palette = bytearray()
    for index in range(255):
        red = round(((index >> 5) & 7) * 255 / 7)
        green = round(((index >> 2) & 7) * 255 / 7)
        blue = round((index & 3) * 255 / 3)
        palette.extend((blue, green, red, 0))
    palette.extend((255, 0, 0, 0))
    rows = bytearray()
    padding = bytes(stride - width)
    for row in range(height - 1, -1, -1):
        start = row * width
        rows.extend(indices[start : start + width])
        rows.extend(padding)
    header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    dib = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 8, 0, image_size, 2835, 2835, 256, 256)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(header + dib + palette + rows)


def _convert_with_blender_image(
    source: Path,
    destination: Path,
    *,
    width: int,
    height: int,
    masked: bool,
    alpha_threshold: int,
) -> None:
    """Use Blender's image datablock when Pillow is unavailable in Blender."""

    try:
        import bpy  # type: ignore
    except ImportError as exc:
        raise TextureError("Pillow is unavailable and Blender image fallback is not active") from exc
    try:
        # A long-lived Blender MCP session may retain an older image datablock
        # for the same path in another scene. Always read the just-written file.
        image = bpy.data.images.load(str(source), check_existing=False)
    except Exception as exc:  # Blender raises RNA-specific exceptions here.
        raise TextureError(f"could not load source image through Blender: {source}") from exc
    try:
        if tuple(image.size) != (width, height):
            image.scale(width, height)
        _write_indexed_bmp_from_rgba(
            image.pixels,
            destination,
            width=width,
            height=height,
            masked=masked,
            alpha_threshold=alpha_threshold,
        )
    finally:
        if image.users == 0:
            bpy.data.images.remove(image)


def convert_to_indexed_bmp(
    source: Path | str,
    destination: Path | str,
    *,
    width: int,
    height: int,
    modes: list[str] | tuple[str, ...] = (),
    alpha_threshold: int = 128,
) -> dict:
    if width <= 0 or height <= 0 or width > 512 or height > 512 or width % 16 or height % 16:
        raise TextureError("destination dimensions must be multiples of 16 within 1..512")
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    masked = "masked" in {mode.casefold() for mode in modes}
    try:
        from PIL import Image
    except ImportError:
        _convert_with_blender_image(
            source_path,
            destination_path,
            width=width,
            height=height,
            masked=masked,
            alpha_threshold=alpha_threshold,
        )
        _expand_palette_to_256(destination_path)
        return validate_indexed_bmp(
            destination_path, width=width, height=height, modes=modes, require_masked_pixels=masked
        )
    image = Image.open(source_path).convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
    if masked:
        alpha = image.getchannel("A")
        rgb = Image.new("RGB", image.size, (0, 0, 255))
        rgb.paste(image.convert("RGB"), mask=alpha.point(lambda value: 255 if value >= alpha_threshold else 0))
        indexed = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        palette = list(indexed.getpalette()[: 255 * 3])
        palette.extend([0] * (255 * 3 - len(palette)))
        palette.extend([0, 0, 255])
        indexed.putpalette(palette)
        values = bytearray(indexed.tobytes())
        alpha_values = alpha.tobytes()
        for index, value in enumerate(alpha_values):
            if value < alpha_threshold:
                values[index] = 255
        indexed.frombytes(bytes(values))
    else:
        indexed = image.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    indexed.save(destination_path, format="BMP", compression="raw")
    _expand_palette_to_256(destination_path)
    return validate_indexed_bmp(
        destination_path, width=width, height=height, modes=modes, require_masked_pixels=masked
    )
