#!/usr/bin/env python3
"""Inject a Unity-generated TMP font and atlas into copied game resources.

The source game files are hash-checked and opened read-only.  The replacement
TMP data comes from an AssetBundle produced by the exact game Unity/TMP version.
Game-local PPtr references are preserved, while the generated glyph, character,
metric, packing, and feature data replace the original NotoSans-JP data.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

import UnityPy
from UnityPy.helpers import TypeTreeHelper
from UnityPy.streams import EndianBinaryReader, EndianBinaryWriter


FONT_PATH_ID = 3710
ATLAS_PATH_ID = 280
EXPECTED_RESOURCES_SHA256 = "afcdcc6086a69c5d92864ad21351fa589dfc9a83cd770a87e13e0e555b9daa8f"
EXPECTED_RESS_SHA256 = "27a08bb454566f2df23e4444f2f059b8256a7f522627a61a9be016ca5d7353f1"
EXPECTED_ATLAS_BYTES = 4096 * 4096

PRESERVE_FROM_GAME = (
    "m_GameObject",
    "m_Enabled",
    "m_Script",
    "m_Name",
    "hashCode",
    "material",
    "materialHashCode",
    "m_SourceFontFileGUID",
    "m_SourceFontFile",
    "m_AtlasTextures",
    "atlas",
    "fallbackFontAssets",
    "m_FallbackFontAssetTable",
    "m_FontWeightTable",
    "fontWeights",
    "normalStyle",
    "normalSpacingOffset",
    "boldStyle",
    "boldSpacing",
    "italicStyle",
    "tabSize",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hash(path: Path, expected: str) -> None:
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"source hash mismatch for {path}: {actual}")


def parse_with_tree(raw: bytes, root_node, assetsfile) -> dict:
    return TypeTreeHelper.read_typetree(
        root_node,
        EndianBinaryReader(raw, endian="<"),
        as_dict=True,
        byte_size=len(raw),
        check_read=True,
        assetsfile=assetsfile,
    )


def serialize_with_tree(data: dict, root_node, assetsfile) -> bytes:
    writer = EndianBinaryWriter(endian="<")
    TypeTreeHelper.write_typetree(data, root_node, writer, assetsfile=assetsfile)
    return writer.bytes


def inject(
    source_assets: Path,
    source_ress: Path,
    generated_bundle: Path,
    out_assets: Path,
    out_ress: Path,
) -> dict[str, int]:
    if source_assets.resolve() == out_assets.resolve() or source_ress.resolve() == out_ress.resolve():
        raise ValueError("source and output paths must differ")
    require_hash(source_assets, EXPECTED_RESOURCES_SHA256)
    require_hash(source_ress, EXPECTED_RESS_SHA256)

    generated_env = UnityPy.load(str(generated_bundle))
    generated_fonts = [
        obj for obj in generated_env.objects
        if obj.type.name == "MonoBehaviour" and getattr(obj.read(), "m_Name", "") == "NotoSans-JP"
    ]
    generated_atlases = [
        obj for obj in generated_env.objects
        if obj.type.name == "Texture2D" and getattr(obj.read(), "m_Name", "") == "NotoSans-JP Atlas"
    ]
    if len(generated_fonts) != 1 or len(generated_atlases) != 1:
        raise ValueError("generated bundle must contain one NotoSans-JP font and atlas")
    generated_font_obj = generated_fonts[0]
    generated_font = generated_font_obj.read_typetree()
    generated_atlas = generated_atlases[0].read()
    atlas_bytes = bytes(generated_atlas.image_data)
    if (
        generated_atlas.m_Width != 4096
        or generated_atlas.m_Height != 4096
        or generated_atlas.m_TextureFormat != 1
        or len(atlas_bytes) != EXPECTED_ATLAS_BYTES
    ):
        raise ValueError("generated atlas is not 4096x4096 Alpha8")
    generated_character_count = len(generated_font["m_CharacterTable"])
    if generated_character_count == 0:
        raise ValueError("generated TMP font has an empty character table")

    source_env = UnityPy.load(str(source_assets))
    source_font_obj = next(
        (obj for obj in source_env.objects if obj.path_id == FONT_PATH_ID), None
    )
    source_atlas_obj = next(
        (obj for obj in source_env.objects if obj.path_id == ATLAS_PATH_ID), None
    )
    if source_font_obj is None or source_font_obj.type.name != "MonoBehaviour":
        raise ValueError(f"missing game TMP font path_id {FONT_PATH_ID}")
    if source_atlas_obj is None or source_atlas_obj.type.name != "Texture2D":
        raise ValueError(f"missing game atlas path_id {ATLAS_PATH_ID}")

    root_node = generated_font_obj.serialized_type.node
    source_font = parse_with_tree(
        source_font_obj.get_raw_data(), root_node, source_env.file
    )
    if source_font["m_Name"] != "NotoSans-JP" or len(source_font["m_CharacterTable"]) != 7130:
        raise ValueError("unexpected source NotoSans-JP font data")

    merged_font = generated_font
    for field in PRESERVE_FROM_GAME:
        merged_font[field] = source_font[field]
    merged_font["m_AtlasPopulationMode"] = 0
    merged_font["m_AtlasTextureIndex"] = 0
    merged_font["m_IsMultiAtlasTexturesEnabled"] = 0
    merged_font["m_ClearDynamicDataOnBuild"] = 0
    merged_raw = serialize_with_tree(merged_font, root_node, source_env.file)
    reparsed = parse_with_tree(merged_raw, root_node, source_env.file)
    if len(reparsed["m_CharacterTable"]) != generated_character_count:
        raise ValueError("serialized replacement font failed round-trip validation")
    if reparsed["m_AtlasTextures"] != source_font["m_AtlasTextures"]:
        raise ValueError("game atlas reference changed during font serialization")
    source_font_obj.set_raw_data(merged_raw)

    source_atlas = source_atlas_obj.read()
    stream = source_atlas.m_StreamData
    if (
        source_atlas.m_Name != "NotoSans-JP Atlas"
        or source_atlas.m_Width != 4096
        or source_atlas.m_Height != 4096
        or source_atlas.m_TextureFormat != 1
        or stream.path != "resources.assets.resS"
        or stream.size != EXPECTED_ATLAS_BYTES
    ):
        raise ValueError("unexpected source atlas layout")
    if stream.offset + stream.size > source_ress.stat().st_size:
        raise ValueError("source atlas stream range exceeds resources.assets.resS")

    out_assets.parent.mkdir(parents=True, exist_ok=True)
    out_ress.parent.mkdir(parents=True, exist_ok=True)
    out_assets.write_bytes(source_env.file.save())
    shutil.copyfile(source_ress, out_ress)
    with out_ress.open("r+b") as handle:
        handle.seek(stream.offset)
        handle.write(atlas_bytes)

    return {
        "characters": generated_character_count,
        "hangul_syllables": sum(
            0xAC00 <= row["m_Unicode"] <= 0xD7A3
            for row in reparsed["m_CharacterTable"]
        ),
        "glyphs": len(reparsed["m_GlyphTable"]),
        "atlas_offset": stream.offset,
        "atlas_bytes": len(atlas_bytes),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-assets", type=Path, required=True)
    parser.add_argument("--source-ress", type=Path, required=True)
    parser.add_argument("--generated-bundle", type=Path, required=True)
    parser.add_argument("--out-assets", type=Path, required=True)
    parser.add_argument("--out-ress", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inject(
            args.source_assets,
            args.source_ress,
            args.generated_bundle,
            args.out_assets,
            args.out_ress,
        )
    except (OSError, StopIteration, ValueError, EOFError) as exc:
        sys.exit(f"ERROR: {exc}")
    for key, value in result.items():
        print(f"{key}: {value}")
    print(f"assets_sha256: {sha256(args.out_assets)}")
    print(f"ress_sha256: {sha256(args.out_ress)}")


if __name__ == "__main__":
    main()
