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
    input_color_space: str = "linear",
    row_origin: str = "bottom-left",
) -> None:
    """Write a deterministic indexed BMP without external image packages.

    This is deliberately a small fallback for Blender's embedded Python, where
    the host Pillow installation is not necessarily importable.  The normal
    path uses Pillow's higher quality median-cut quantizer. Blender image
    buffers are scene-linear and bottom-left-origin unless a caller explicitly
    identifies a different representation.
    """

    values = list(rgba)
    expected = width * height * 4
    if len(values) != expected:
        raise TextureError(f"Blender image returned {len(values)} channels, expected {expected}")
    if input_color_space not in {"linear", "srgb"}:
        raise TextureError(f"unsupported RGBA input color space: {input_color_space}")
    if row_origin not in {"bottom-left", "top-left"}:
        raise TextureError(f"unsupported RGBA row origin: {row_origin}")

    indices = bytearray(width * height)
    for top_row in range(height):
        source_row = height - 1 - top_row if row_origin == "bottom-left" else top_row
        for column in range(width):
            source_pixel = source_row * width + column
            destination_pixel = top_row * width + column
            channels = []
            for channel in range(3):
                value = float(values[source_pixel * 4 + channel])
                encoded = _linear_to_srgb(value) if input_color_space == "linear" else max(0.0, min(1.0, value))
                channels.append(round(encoded * 255.0))
            red, green, blue = channels
            alpha = max(0, min(255, round(float(values[source_pixel * 4 + 3]) * 255.0)))
            if masked and alpha < alpha_threshold:
                indices[destination_pixel] = 255
            elif masked:
                # 6 x 7 x 6 leaves indices 252..254 unused and index 255
                # exclusively available for GoldSrc masked transparency.
                red_bin = min(5, red * 6 // 256)
                green_bin = min(6, green * 7 // 256)
                blue_bin = min(5, blue * 6 // 256)
                indices[destination_pixel] = red_bin * 42 + green_bin * 6 + blue_bin
            else:
                # Non-masked textures own all 256 entries, including white at 255.
                indices[destination_pixel] = ((red >> 5) << 5) | ((green >> 5) << 2) | (blue >> 6)

    stride = (width + 3) & ~3
    image_size = stride * height
    pixel_offset = 14 + 40 + 256 * 4
    file_size = pixel_offset + image_size
    palette = bytearray()
    if masked:
        for index in range(252):
            red = round((index // 42) * 255 / 5)
            green = round(((index % 42) // 6) * 255 / 6)
            blue = round((index % 6) * 255 / 5)
            palette.extend((blue, green, red, 0))
        palette.extend(bytes(3 * 4))
        palette.extend((255, 0, 0, 0))
    else:
        for index in range(256):
            red = round(((index >> 5) & 7) * 255 / 7)
            green = round(((index >> 2) & 7) * 255 / 7)
            blue = round((index & 3) * 255 / 3)
            palette.extend((blue, green, red, 0))
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


def _rgba_to_display_bytes(
    rgba: Iterable[float],
    *,
    width: int,
    height: int,
    input_color_space: str,
    row_origin: str,
) -> bytes:
    values = list(rgba)
    expected = width * height * 4
    if len(values) != expected:
        raise TextureError(f"RGBA input returned {len(values)} channels, expected {expected}")
    if input_color_space not in {"linear", "srgb"}:
        raise TextureError(f"unsupported RGBA input color space: {input_color_space}")
    if row_origin not in {"bottom-left", "top-left"}:
        raise TextureError(f"unsupported RGBA row origin: {row_origin}")
    output = bytearray(expected)
    for top_row in range(height):
        source_row = height - 1 - top_row if row_origin == "bottom-left" else top_row
        for column in range(width):
            source = (source_row * width + column) * 4
            target = (top_row * width + column) * 4
            for channel in range(3):
                value = float(values[source + channel])
                encoded = _linear_to_srgb(value) if input_color_space == "linear" else max(0.0, min(1.0, value))
                output[target + channel] = round(encoded * 255.0)
            output[target + 3] = max(0, min(255, round(float(values[source + 3]) * 255.0)))
    return bytes(output)


def _quantize_pillow_image(image, destination: Path, *, masked: bool, alpha_threshold: int):
    from PIL import Image

    rgba = image.convert("RGBA")
    alpha_values = bytearray(rgba.getchannel("A").tobytes())
    if masked:
        rgb_values = list(rgba.convert("RGB").getdata())
        visible = [index for index, alpha in enumerate(alpha_values) if alpha >= alpha_threshold]
        fill = rgb_values[visible[0]] if visible else (0, 0, 0)
        for index, alpha in enumerate(alpha_values):
            if alpha < alpha_threshold:
                rgb_values[index] = fill
        rgb = Image.new("RGB", rgba.size)
        rgb.putdata(rgb_values)
        indexed = rgb.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        palette = list(indexed.getpalette()[: 255 * 3])
        palette.extend([0] * (255 * 3 - len(palette)))
        palette.extend([0, 0, 255])
        indexed.putpalette(palette)
        values = bytearray(indexed.tobytes())
        for index, alpha in enumerate(alpha_values):
            if alpha < alpha_threshold:
                values[index] = 255
        indexed.frombytes(bytes(values))
    else:
        indexed = rgba.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    indexed.save(destination, format="BMP", compression="raw")
    _expand_palette_to_256(destination)
    return rgba


def _row_mean(image, row: int) -> list[float]:
    pixels = list(image.convert("RGB").crop((0, row, image.width, row + 1)).getdata())
    return [round(sum(pixel[channel] for pixel in pixels) / len(pixels), 3) for channel in range(3)]


def _texture_fidelity(source, destination: Path, *, masked: bool, alpha_threshold: int) -> dict:
    from PIL import Image

    source = source.convert("RGBA")
    output = Image.open(destination).convert("RGB")
    source_rgb = list(source.convert("RGB").getdata())
    output_rgb = list(output.getdata())
    alpha = source.getchannel("A").tobytes()
    visible = [index for index, value in enumerate(alpha) if not masked or value >= alpha_threshold]

    def errors(candidate: list[tuple[int, int, int]]) -> tuple[float | None, int | None]:
        channel_errors = [
            abs(source_rgb[index][channel] - candidate[index][channel])
            for index in visible for channel in range(3)
        ]
        if not channel_errors:
            return None, None
        return sum(channel_errors) / len(channel_errors), max(channel_errors)

    direct_mae, maximum = errors(output_rgb)
    flipped = list(output.transpose(Image.Transpose.FLIP_TOP_BOTTOM).getdata())
    flipped_mae, _unused = errors(flipped)
    if direct_mae is None or flipped_mae is None or abs(direct_mae - flipped_mae) < 0.000001:
        orientation = "tie"
    elif direct_mae < flipped_mae:
        orientation = "direct"
    else:
        orientation = "vertically_flipped"
    return {
        "source_visible_color_count": len({source_rgb[index] for index in visible}),
        "output_visible_color_count": len({output_rgb[index] for index in visible}),
        "mean_absolute_channel_error": round(direct_mae, 6) if direct_mae is not None else None,
        "max_absolute_channel_error": maximum,
        "orientation": {
            "preferred": orientation,
            "direct_mae": round(direct_mae, 6) if direct_mae is not None else None,
            "vertically_flipped_mae": round(flipped_mae, 6) if flipped_mae is not None else None,
            "source_top_rgb_mean": _row_mean(source, 0),
            "source_bottom_rgb_mean": _row_mean(source, source.height - 1),
            "output_top_rgb_mean": _row_mean(output, 0),
            "output_bottom_rgb_mean": _row_mean(output, output.height - 1),
        },
    }


def convert_rgba_to_indexed_bmp(
    rgba: Iterable[float],
    destination: Path | str,
    *,
    width: int,
    height: int,
    modes: list[str] | tuple[str, ...] = (),
    alpha_threshold: int = 128,
    input_color_space: str = "linear",
    row_origin: str = "bottom-left",
    require_masked_pixels: bool = True,
) -> dict:
    """Convert an explicitly described RGBA buffer to a GoldSrc BMP."""

    destination_path = Path(destination).expanduser().resolve()
    masked = "masked" in {mode.casefold() for mode in modes}
    values = list(rgba)
    try:
        from PIL import Image
    except ImportError:
        _write_indexed_bmp_from_rgba(
            values, destination_path, width=width, height=height, masked=masked,
            alpha_threshold=alpha_threshold, input_color_space=input_color_space, row_origin=row_origin,
        )
        facts = validate_indexed_bmp(
            destination_path, width=width, height=height, modes=modes,
            require_masked_pixels=masked and require_masked_pixels,
        )
        facts["conversion"] = {
            "method": "deterministic_fallback", "input_color_space": input_color_space,
            "source_row_origin": row_origin, "fidelity": None,
        }
        return facts
    display_bytes = _rgba_to_display_bytes(
        values, width=width, height=height, input_color_space=input_color_space, row_origin=row_origin,
    )
    source = Image.frombytes("RGBA", (width, height), display_bytes)
    source = _quantize_pillow_image(source, destination_path, masked=masked, alpha_threshold=alpha_threshold)
    facts = validate_indexed_bmp(
        destination_path, width=width, height=height, modes=modes,
        require_masked_pixels=masked and require_masked_pixels,
    )
    facts["conversion"] = {
        "method": "pillow_mediancut_rgba", "input_color_space": input_color_space,
        "source_row_origin": row_origin,
        "fidelity": _texture_fidelity(source, destination_path, masked=masked, alpha_threshold=alpha_threshold),
    }
    return facts


def _convert_with_blender_image(
    source: Path,
    destination: Path,
    *,
    width: int,
    height: int,
    masked: bool,
    alpha_threshold: int,
    require_masked_pixels: bool = True,
) -> dict:
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
        return convert_rgba_to_indexed_bmp(
            image.pixels,
            destination,
            width=width,
            height=height,
            modes=["masked"] if masked else [],
            alpha_threshold=alpha_threshold,
            input_color_space="linear",
            row_origin="bottom-left",
            require_masked_pixels=require_masked_pixels,
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
    require_masked_pixels: bool = True,
) -> dict:
    if width <= 0 or height <= 0 or width > 512 or height > 512 or width % 16 or height % 16:
        raise TextureError("destination dimensions must be multiples of 16 within 1..512")
    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    masked = "masked" in {mode.casefold() for mode in modes}
    try:
        from PIL import Image
    except ImportError:
        return _convert_with_blender_image(
            source_path,
            destination_path,
            width=width,
            height=height,
            masked=masked,
            alpha_threshold=alpha_threshold,
            require_masked_pixels=require_masked_pixels,
        )
    with Image.open(source_path) as opened:
        image = opened.convert("RGBA").resize((width, height), Image.Resampling.LANCZOS)
    source = _quantize_pillow_image(image, destination_path, masked=masked, alpha_threshold=alpha_threshold)
    facts = validate_indexed_bmp(
        destination_path, width=width, height=height, modes=modes,
        require_masked_pixels=masked and require_masked_pixels,
    )
    facts["conversion"] = {
        "method": "pillow_mediancut_file", "input_color_space": "file_encoded_srgb",
        "source_row_origin": "top-left",
        "source": str(source_path),
        "fidelity": _texture_fidelity(source, destination_path, masked=masked, alpha_threshold=alpha_threshold),
    }
    return facts
