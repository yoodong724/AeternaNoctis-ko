#!/usr/bin/env python3
"""Build a reproducible, original-file-free Windows xdelta3 patch package."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BUILD_GUID = "2afee6e7990049e895cb7917d9784414"
PATCH_NAME = "Aeterna Noctis Korean Patch"
PACKAGE_NAME = "AeternaNoctis-ko"
PATCH_VERSION = "1.0.1"
# User confirmed operation; full-game review is still incomplete.
HUMAN_ACCEPTANCE_STATUS = "FUNCTIONAL_CHECK_PASSED_FULL_REVIEW_PENDING"
PACKAGING_AUTHORIZATION = "USER_REQUESTED"
XDELTA_VERSION = "3.2.0"
XDELTA_WINDOWS_SHA256 = "53d90226615f217d3380c39892833311b4e24acd863e1ca01f14b5e772e2e6d0"

COMPATIBILITY = (
    {
        "relative_path": "Aeterna Noctis.exe",
        "sha256": "c8b3e2828c6e4c38eaab89ae849ecc7f8d1b614b2ca76e3a28195ded4feaab2e",
    },
    {
        "relative_path": "Aeterna Noctis_Data/sharedassets1.assets",
        "sha256": "5b405e64c5bbbeda269209209ed9e6fa04cd4d313f5f2dc4b28cb40e4ad8a9d7",
    },
    {
        "relative_path": "Aeterna Noctis_Data/resources.assets",
        "sha256": "afcdcc6086a69c5d92864ad21351fa589dfc9a83cd770a87e13e0e555b9daa8f",
    },
    {
        "relative_path": "Aeterna Noctis_Data/resources.assets.resS",
        "sha256": "27a08bb454566f2df23e4444f2f059b8256a7f522627a61a9be016ca5d7353f1",
    },
)

RESOURCE_SPECS = (
    {
        "name": "sharedassets1.assets",
        "source_sha256": "5b405e64c5bbbeda269209209ed9e6fa04cd4d313f5f2dc4b28cb40e4ad8a9d7",
        "target_sha256": "41f2b107d51774fce24ada47aac72d3948b2ccea115bb5537aeddbf0aae9d6ff",
    },
    {
        "name": "resources.assets",
        "source_sha256": "afcdcc6086a69c5d92864ad21351fa589dfc9a83cd770a87e13e0e555b9daa8f",
        "target_sha256": "e16438675c73b3be6520ccc07fea385c76ad011097bac94e0026c988ef5f741c",
    },
    {
        "name": "resources.assets.resS",
        "source_sha256": "27a08bb454566f2df23e4444f2f059b8256a7f522627a61a9be016ca5d7353f1",
        "target_sha256": "63ac809ca4b5527457d6ec27788524c13099567a96c97dcdc79ca20171ceeabc",
    },
)

TEMPLATE_FILES = (
    "Install-KoreanPatch.ps1",
    "Uninstall-KoreanPatch.ps1",
    "install.cmd",
    "uninstall.cmd",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_version(value: str) -> str:
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?", value):
        raise ValueError(f"invalid patch version: {value!r}")
    return value


def reject_overlap(output: Path, protected: tuple[Path, ...]) -> None:
    resolved = output.resolve()
    for item in protected:
        item = item.resolve()
        if resolved == item or resolved in item.parents or item in resolved.parents:
            raise ValueError(f"release output overlaps protected input: {item}")


def require_hash(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"missing {label}: {path}")
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")


def run_xdelta_encode(xdelta3: Path, source: Path, target: Path, delta: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="aeterna-xdelta-") as temp_name:
        work = Path(temp_name)
        os.symlink(source.resolve(), work / "source.bin")
        os.symlink(target.resolve(), work / "target.bin")
        result = subprocess.run(
            [
                str(xdelta3.resolve()),
                "-9",
                "-S",
                "lzma",
                "-e",
                "-f",
                "-s",
                "source.bin",
                "target.bin",
                str(delta.resolve()),
            ],
            cwd=work,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(f"xdelta3 encode failed for {source.name}: {result.stdout.strip()}")


def write_checksums(package: Path) -> None:
    rows = []
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            rows.append(f"{sha256(path)}  {path.relative_to(package).as_posix()}\n")
    (package / "SHA256SUMS.txt").write_text("".join(rows), encoding="ascii", newline="\n")


def write_reproducible_zip(package: Path, archive: Path) -> None:
    temp_archive = archive.with_suffix(archive.suffix + ".tmp")
    if temp_archive.exists():
        temp_archive.unlink()
    with zipfile.ZipFile(temp_archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(package.rglob("*")):
            if not path.is_file():
                continue
            relative = (Path(package.name) / path.relative_to(package)).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(2026, 8, 30, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            executable = path.suffix.lower() in {".exe", ".bat", ".cmd", ".ps1"}
            info.external_attr = ((0o755 if executable else 0o644) & 0xFFFF) << 16
            output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    temp_archive.replace(archive)


def build_release(
    source_root: Path,
    candidate_data: Path,
    output_parent: Path,
    version: str,
    xdelta3: Path,
    windows_xdelta3: Path,
    xdelta_license: Path,
    noto_license: Path,
    template_root: Path,
) -> dict[str, object]:
    version = validate_version(version)
    if version != PATCH_VERSION:
        raise ValueError(f"target resource hashes are pinned to patch {PATCH_VERSION}")
    source_root = source_root.resolve()
    source_data = source_root / "Aeterna Noctis_Data"
    candidate_data = candidate_data.resolve()
    output_parent = output_parent.resolve()
    package_name = PACKAGE_NAME
    package = output_parent / package_name
    reject_overlap(package, (source_root, candidate_data))

    for item in COMPATIBILITY:
        require_hash(source_root / item["relative_path"], item["sha256"], item["relative_path"])
    for spec in RESOURCE_SPECS:
        require_hash(source_data / spec["name"], spec["source_sha256"], f"source {spec['name']}")
        require_hash(candidate_data / spec["name"], spec["target_sha256"], f"target {spec['name']}")
    require_hash(windows_xdelta3, XDELTA_WINDOWS_SHA256, "official Windows xdelta3.exe")
    for required in (xdelta3, xdelta_license, noto_license):
        if not required.is_file():
            raise ValueError(f"missing build dependency: {required}")
    version_output = subprocess.run(
        [str(xdelta3.resolve()), "-V"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if version_output.returncode != 0 or f"version {XDELTA_VERSION}" not in version_output.stdout.lower():
        raise ValueError(f"unexpected xdelta3 build tool: {version_output.stdout.strip()}")
    for name in TEMPLATE_FILES:
        if not (template_root / "windows" / name).is_file():
            raise ValueError(f"missing Windows launcher template: {name}")
    for name in ("INSTALL.md", "KNOWN_ISSUES.md", "CREDITS.md"):
        if not (template_root / name).is_file():
            raise ValueError(f"missing release document template: {name}")

    output_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{package_name}.", dir=output_parent) as temp_name:
        stage = Path(temp_name) / package_name
        (stage / "patch").mkdir(parents=True)
        (stage / "bin").mkdir()
        (stage / "LICENSES").mkdir()

        files = []
        for spec in RESOURCE_SPECS:
            patch_relative = f"patch/{spec['name']}.vcdiff"
            patch_path = stage / patch_relative
            run_xdelta_encode(
                xdelta3,
                source_data / spec["name"],
                candidate_data / spec["name"],
                patch_path,
            )
            files.append(
                {
                    "relative_path": f"Aeterna Noctis_Data/{spec['name']}",
                    "patch_path": patch_relative,
                    "source_sha256": spec["source_sha256"],
                    "target_sha256": spec["target_sha256"],
                    "source_size": (source_data / spec["name"]).stat().st_size,
                    "target_size": (candidate_data / spec["name"]).stat().st_size,
                    "patch_sha256": sha256(patch_path),
                    "patch_size": patch_path.stat().st_size,
                }
            )

        shutil.copyfile(windows_xdelta3, stage / "bin" / "xdelta3.exe")
        shutil.copyfile(xdelta_license, stage / "LICENSES" / "xdelta3-Apache-2.0.txt")
        shutil.copyfile(noto_license, stage / "LICENSES" / "NotoSansCJK-OFL-1.1.txt")
        for name in TEMPLATE_FILES:
            shutil.copyfile(template_root / "windows" / name, stage / name)
        for name in ("INSTALL.md", "KNOWN_ISSUES.md", "CREDITS.md"):
            text = (template_root / name).read_text(encoding="utf-8")
            text = text.replace("{{PATCH_VERSION}}", version).replace("{{PACKAGE_NAME}}", package_name)
            (stage / name).write_text(text, encoding="utf-8", newline="\n")

        manifest: dict[str, object] = {
            "schema_version": 1,
            "patch_name": PATCH_NAME,
            "patch_version": version,
            "package_name": package_name,
            "target_build": {
                "engine": "Unity 2022.3.62f1",
                "build_guid": BUILD_GUID,
                "platform": "Windows x64",
            },
            "language_slot": "Japanese",
            "language_output": "ko-KR",
            "included_content_packs": [
                "BASE",
                "DLC-POTD",
                "DLC-BOSSRUSH",
                "DLC-LUCISLURE",
                "FEATURE-CHAOSTRIALS",
            ],
            "human_acceptance_status": HUMAN_ACCEPTANCE_STATUS,
            "packaging_authorization": PACKAGING_AUTHORIZATION,
            "compatibility": list(COMPATIBILITY),
            "files": files,
            "tool": {
                "name": "xdelta3",
                "version": XDELTA_VERSION,
                "format": "VCDIFF/RFC 3284",
                "license": "Apache-2.0",
                "binary_path": "bin/xdelta3.exe",
                "binary_sha256": sha256(stage / "bin" / "xdelta3.exe"),
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        write_checksums(stage)

        if package.exists():
            if package.name != package_name or package.parent != output_parent:
                raise ValueError("refusing to replace unexpected release directory")
            shutil.rmtree(package)
        stage.replace(package)

    archive = output_parent / f"{package_name}-{version}.zip"
    write_reproducible_zip(package, archive)
    archive_hash = sha256(archive)
    checksum_path = output_parent / f"{archive.name}.sha256"
    checksum_path.write_text(f"{archive_hash}  {archive.name}\n", encoding="ascii", newline="\n")
    return {
        "package": str(package),
        "archive": str(archive),
        "archive_sha256": archive_hash,
        "archive_size": archive.stat().st_size,
        "files": files,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("AeternaNoctis"))
    parser.add_argument(
        "--candidate-data", type=Path, default=Path("build/opening-ko/Aeterna Noctis_Data")
    )
    parser.add_argument("--output-parent", type=Path, default=Path("dist"))
    parser.add_argument("--version", default=PATCH_VERSION)
    parser.add_argument("--xdelta3", type=Path, required=True)
    parser.add_argument("--windows-xdelta3", type=Path, required=True)
    parser.add_argument("--xdelta-license", type=Path, required=True)
    parser.add_argument(
        "--noto-license", type=Path, default=Path("third_party/NotoSansCJK/LICENSE.txt")
    )
    parser.add_argument("--template-root", type=Path, default=Path("release"))
    args = parser.parse_args()
    try:
        result = build_release(
            args.source_root,
            args.candidate_data,
            args.output_parent,
            args.version,
            args.xdelta3,
            args.windows_xdelta3,
            args.xdelta_license,
            args.noto_license,
            args.template_root,
        )
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(f"ERROR: {exc}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
