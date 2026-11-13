"""Reliable picture embedding for the privately bundled formatter renderer.

The original Windows texconv resize can fail with E_NOINTERFACE in Wine or a
Windows installation missing the relevant image component. Retry at the source
size, within a strict memory limit, then perform the original NumPy downsample.
Geometry, materials, shaders and all original viewer controls are unchanged.
"""
from __future__ import annotations

import base64
import html
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import zlib

MAX_RGBA_BYTES = 256 * 1024 * 1024
MAX_PREVIEW_SIDE = 1024
_diagnostics = []


def reset_diagnostics():
    _diagnostics.clear()


def diagnostics():
    return list(_diagnostics)


def _fallback_picture(path, alpha=False):
    import numpy as np
    from gmf_legacy23_normals import _tool_path, rgba_layout
    from gmf_local_textures import prepare_local_dds

    source = Path(path)
    if not source.is_file() or source.stat().st_size > MAX_RGBA_BYTES:
        raise ValueError("DDS is unavailable or exceeds the 256 MiB input limit")
    raw, _ = prepare_local_dds(source.read_bytes())
    if len(raw) < 128 or raw[:4] != b"DDS ":
        raise ValueError("The texture is not a supported DDS image")
    height, width = struct.unpack_from("<II", raw, 12)
    if not width or not height or max(width, height) > 16384:
        raise ValueError("DDS dimensions are invalid or exceed 16384 pixels")
    if width * height * 4 > MAX_RGBA_BYTES:
        raise ValueError("Full-size texture decode would exceed 256 MiB")

    with tempfile.TemporaryDirectory(prefix="mykelovve-picture-fallback-") as temporary:
        directory = Path(temporary)
        source_copy = directory / "picture.dds"
        source_copy.write_bytes(raw)
        out = directory / "decoded"
        out.mkdir()
        completed = subprocess.run(
            [str(_tool_path()), "-nogpu", "-nologo", "-y", "-m", "1", "-dx10",
             "-f", "R8G8B8A8_UNORM", "-ft", "DDS", "-o", str(out), str(source_copy)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=45,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "OMP_NUM_THREADS": "2"},
        )
        decoded_file = out / "picture.dds"
        if completed.returncode or not decoded_file.is_file():
            detail = completed.stdout.decode(errors="replace").strip()[-1200:]
            raise ValueError("Texture conversion failed (exit %s): %s" % (completed.returncode, detail))
        if decoded_file.stat().st_size > MAX_RGBA_BYTES + 148:
            raise ValueError("Decoded image exceeds the 256 MiB limit")
        decoded = decoded_file.read_bytes()
        layout = rgba_layout(decoded)
    width, height = layout["width"], layout["height"]
    if not width or not height or width * height * 4 > MAX_RGBA_BYTES:
        raise ValueError("Decoded DDS dimensions exceed the memory limit")
    pixels = np.frombuffer(decoded, np.uint8, count=width * height * 4, offset=148).reshape(height, width, 4)
    step = max(1, (max(width, height) + MAX_PREVIEW_SIDE - 1) // MAX_PREVIEW_SIDE)
    pixels = pixels[::step, ::step].copy()
    if not alpha:
        pixels[:, :, 3] = 255
    height, width = pixels.shape[:2]
    scanlines = b"".join(b"\x00" + row.tobytes() for row in pixels)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(scanlines, 6)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def install(renderer=None):
    """Install before renderer() or build(); safe to call repeatedly."""
    if renderer is None:
        import gmf_preview as renderer
    original = renderer._decode_picture
    if getattr(original, "_mykelovve_resize_fallback", False):
        return renderer

    def decode_picture(path, alpha=False):
        try:
            picture = original(path, alpha)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            picture = None
        if picture:
            return picture
        try:
            picture = _fallback_picture(path, alpha)
            _diagnostics.append({"path": str(path), "status": "recovered", "method": "full decode then NumPy downsample"})
            return picture
        except (OSError, ValueError, struct.error, subprocess.TimeoutExpired) as error:
            _diagnostics.append({"path": str(path), "status": "failed", "error": str(error)})
            return None

    decode_picture._mykelovve_resize_fallback = True
    renderer._decode_picture = decode_picture
    # The shipped version has no picture cache, but clear one if a future private
    # runtime adds it so a previously cached None cannot survive installation.
    page = sys.modules.get("gmf.preview_page")
    for function in (getattr(renderer, "_png_picture", None), getattr(page, "picture", None)):
        clear = getattr(function, "cache_clear", None)
        if clear:
            clear()
    return renderer


def _validate_png(url):
    if not isinstance(url, str) or not url.startswith("data:image/png;base64,"):
        raise ValueError("Texture is not an embedded PNG data URL")
    png = base64.b64decode(url.split(",", 1)[1], validate=True)
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG signature is invalid")
    at, compressed, width, height, ended = 8, [], None, None, False
    while at + 12 <= len(png):
        size = struct.unpack_from(">I", png, at)[0]
        kind, data = png[at + 4:at + 8], png[at + 8:at + 8 + size]
        if at + size + 12 > len(png):
            raise ValueError("PNG chunk is truncated")
        crc = struct.unpack_from(">I", png, at + size + 8)[0]
        if zlib.crc32(kind + data) != crc:
            raise ValueError("PNG chunk CRC is invalid")
        if kind == b"IHDR":
            width, height, depth, colour, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if (depth, colour, compression, filtering, interlace) != (8, 6, 0, 0, 0):
                raise ValueError("Unexpected preview PNG pixel format")
            if not width or not height or max(width, height) > MAX_PREVIEW_SIDE:
                raise ValueError("PNG dimensions exceed the preview limit")
        elif kind == b"IDAT":
            compressed.append(data)
        elif kind == b"IEND":
            ended = True
            break
        at += size + 12
    if not ended or width is None:
        raise ValueError("PNG is incomplete")
    expected = (width * 4 + 1) * height
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(b"".join(compressed), expected + 1)
    if len(pixels) != expected or not decoder.eof:
        raise ValueError("PNG pixel payload has the wrong size")
    result = {"width": width, "height": height, "png_bytes": len(png)}
    stride = width * 4 + 1
    # Both formatter PNG encoders use filter type 0. Other valid PNG filters
    # remain accepted; alpha statistics are simply omitted for those images.
    if all(pixels[row * stride] == 0 for row in range(height)):
        alpha = b"".join(pixels[row * stride + 4:(row + 1) * stride:4] for row in range(height))
        result.update(alpha_min=min(alpha), alpha_max=max(alpha), transparent_pixels=alpha.count(0),
                      opaque_pixels=alpha.count(255), partial_alpha_pixels=len(alpha) - alpha.count(0) - alpha.count(255))
    return result


def inspect_preview(path):
    """Validate actual inline pictures and their mesh indices, not just JMTLs."""
    source = Path(path).read_text(encoding="utf-8")
    found = re.search(r'<script\s+id="data"\s+type="application/json">(.*?)</script>', source, re.S)
    if not found:
        raise ValueError("Preview HTML has no model payload")
    payload = json.loads(found.group(1))
    textures, invalid = [], []
    for index, url in enumerate(payload.get("textures", [])):
        try:
            textures.append(dict(index=index, **_validate_png(url)))
        except (ValueError, zlib.error, struct.error) as error:
            invalid.append({"index": index, "error": str(error)})
    textured, missing, missing_alpha, fallbacks = [], [], [], []
    for part in payload.get("parts", []):
        for group in part.get("groups", []):
            index = group.get("tex", -1)
            row = {"mesh": part.get("name"), "material": group.get("mat"),
                   "kind": group.get("kind"), "source": group.get("where"), "texture_index": index}
            if isinstance(index, int) and 0 <= index < len(payload.get("textures", [])):
                textured.append(row)
                texture = next((item for item in textures if item["index"] == index), {})
                if group.get("kind") == "lash" and texture.get("alpha_min") == 255:
                    invalid.append({**row, "error": "Eyelash picture has no transparency"})
            elif index != -1:
                invalid.append({**row, "error": "Mesh references an invalid texture index"})
            else:
                fallbacks.append(row)
                if group.get("where") == "mod" and group.get("kind") in ("skin", "gear", "cut"):
                    missing.append(row)
                if group.get("kind") in ("lash", "cut", "see"):
                    missing_alpha.append(row)
    decoder_failures = [event for event in diagnostics() if event.get("status") == "failed"]
    return {"embedded_pngs": len(textures), "textures": textures, "invalid_textures": invalid,
            "textured_groups": textured, "missing_mod_pictures": missing, "fallback_groups": fallbacks,
            "missing_alpha_pictures": missing_alpha,
            "decoder_events": diagnostics(), "decoder_failures": decoder_failures,
            "payload_valid": not invalid and not missing and not missing_alpha and not decoder_failures}


def annotate_failures(path, report):
    """Show incomplete picture embedding in the page instead of silently gray."""
    failures = report.get("missing_mod_pictures", []) + report.get("missing_alpha_pictures", [])
    invalid = report.get("invalid_textures", [])
    decode_failures = report.get("decoder_failures", [])
    if not failures and not invalid and not decode_failures:
        return
    materials = ", ".join(sorted({str(item.get("material")) for item in failures}))
    message = "Some preview pictures could not be embedded"
    if materials:
        message += ": " + materials
    if invalid:
        message += ". %d embedded image error(s)." % len(invalid)
    if decode_failures:
        message += ". %d texture decode failure(s); see the application log." % len(decode_failures)
    warning = '<p style="color:#ffc94a;font-weight:600">' + html.escape(message) + "</p>"
    target = Path(path)
    target.write_text(target.read_text(encoding="utf-8").replace('<div id="foot">', '<div id="foot">' + warning, 1), encoding="utf-8")
