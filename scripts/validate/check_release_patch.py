#!/usr/bin/env python3
"""Audit an Aeterna Noctis xdelta release package and reproduce every target."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.release.build_patch import (  # noqa: E402
    BUILD_GUID,
    COMPATIBILITY,
    HUMAN_ACCEPTANCE_STATUS,
    PACKAGE_NAME,
    PACKAGING_AUTHORIZATION,
    PATCH_VERSION,
    RESOURCE_SPECS,
    XDELTA_VERSION,
    XDELTA_WINDOWS_SHA256,
)


STATIC_FILES = {
    "CREDITS.md",
    "INSTALL.md",
    "KNOWN_ISSUES.md",
    "Install-KoreanPatch.ps1",
    "LICENSES/NotoSansCJK-OFL-1.1.txt",
    "LICENSES/xdelta3-Apache-2.0.txt",
    "SHA256SUMS.txt",
    "Uninstall-KoreanPatch.ps1",
    "bin/xdelta3.exe",
    "install.cmd",
    "manifest.json",
    "uninstall.cmd",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe package path: {value!r}")
    return path


def package_file_set(package: Path) -> set[str]:
    return {path.relative_to(package).as_posix() for path in package.rglob("*") if path.is_file()}


def parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), start=1):
        if len(line) < 67 or line[64:66] != "  ":
            raise ValueError(f"invalid SHA256SUMS row {line_number}")
        digest, relative = line[:64], line[66:]
        if not all(character in "0123456789abcdef" for character in digest):
            raise ValueError(f"invalid checksum on row {line_number}")
        safe_relative(relative)
        if relative in result:
            raise ValueError(f"duplicate checksum path: {relative}")
        result[relative] = digest
    return result


def validate_manifest(manifest: dict[str, object], package_name: str) -> list[dict[str, object]]:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema")
    if manifest.get("package_name") != package_name:
        raise ValueError("manifest package_name differs from directory name")
    if package_name != PACKAGE_NAME or manifest.get("patch_version") != PATCH_VERSION:
        raise ValueError("manifest package name or version differs from the pinned release")
    target_build = manifest.get("target_build")
    if not isinstance(target_build, dict) or target_build.get("build_guid") != BUILD_GUID:
        raise ValueError("manifest build GUID differs from the pinned build")
    if manifest.get("human_acceptance_status") != HUMAN_ACCEPTANCE_STATUS:
        raise ValueError("manifest human acceptance status differs from the recorded result")
    if manifest.get("packaging_authorization") != PACKAGING_AUTHORIZATION:
        raise ValueError("manifest packaging authorization differs from the user request")
    if manifest.get("language_slot") != "Japanese" or manifest.get("language_output") != "ko-KR":
        raise ValueError("manifest language direction differs from project policy")
    if manifest.get("compatibility") != list(COMPATIBILITY):
        raise ValueError("manifest compatibility list differs from pinned hashes")
    tool = manifest.get("tool")
    if not isinstance(tool, dict):
        raise ValueError("missing manifest tool record")
    if (
        tool.get("name") != "xdelta3"
        or tool.get("version") != XDELTA_VERSION
        or tool.get("license") != "Apache-2.0"
        or tool.get("binary_path") != "bin/xdelta3.exe"
        or tool.get("binary_sha256") != XDELTA_WINDOWS_SHA256
    ):
        raise ValueError("manifest xdelta3 record differs from pinned tool")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(RESOURCE_SPECS):
        raise ValueError("manifest must contain exactly three resource patches")
    by_name = {spec["name"]: spec for spec in RESOURCE_SPECS}
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("manifest file entry is not an object")
        relative = str(item.get("relative_path", ""))
        safe_relative(relative)
        name = PurePosixPath(relative).name
        if name in seen or name not in by_name:
            raise ValueError(f"unexpected or duplicate resource entry: {relative}")
        seen.add(name)
        expected = by_name[name]
        if relative != f"Aeterna Noctis_Data/{name}":
            raise ValueError(f"unexpected resource path: {relative}")
        if item.get("patch_path") != f"patch/{name}.vcdiff":
            raise ValueError(f"unexpected patch path for {name}")
        if item.get("source_sha256") != expected["source_sha256"]:
            raise ValueError(f"source hash differs for {name}")
        if item.get("target_sha256") != expected["target_sha256"]:
            raise ValueError(f"target hash differs for {name}")
    return files


def validate_archive(package: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        expected = {f"{package.name}/{name}" for name in package_file_set(package)}
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError("archive entries differ from the audited package directory")
        for name in names:
            path = safe_relative(name)
            if path.parts[0] != package.name:
                raise ValueError(f"archive entry escapes package root: {name}")
            disk_path = package.joinpath(*path.parts[1:])
            if hashlib.sha256(source.read(name)).hexdigest() != sha256(disk_path):
                raise ValueError(f"archive entry differs from package directory: {name}")


def decode_patch(xdelta3: Path, source: Path, patch: Path, output: Path) -> None:
    result = subprocess.run(
        [str(xdelta3.resolve()), "-d", "-f", "-s", str(source.resolve()), str(patch.resolve()), str(output)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError(f"xdelta3 decode failed for {patch.name}: {result.stdout.strip()}")


def audit_release(
    package: Path,
    archive: Path,
    source_root: Path,
    candidate_data: Path,
    xdelta3: Path,
    forbidden_strings: tuple[str, ...] = (),
) -> dict[str, object]:
    package = package.resolve()
    archive = archive.resolve()
    source_root = source_root.resolve()
    candidate_data = candidate_data.resolve()
    if not package.is_dir() or not archive.is_file() or not xdelta3.is_file():
        raise ValueError("missing package, archive, or xdelta3 verifier")
    if archive.name != f"{PACKAGE_NAME}-{PATCH_VERSION}.zip":
        raise ValueError("archive filename differs from the pinned release")

    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = validate_manifest(manifest, package.name)
    expected_files = STATIC_FILES | {str(item["patch_path"]) for item in files}
    actual_files = package_file_set(package)
    if actual_files != expected_files:
        extra = sorted(actual_files - expected_files)
        missing = sorted(expected_files - actual_files)
        raise ValueError(f"package allowlist mismatch: extra={extra}, missing={missing}")

    checksums = parse_checksums(package / "SHA256SUMS.txt")
    expected_checksum_paths = actual_files - {"SHA256SUMS.txt"}
    if set(checksums) != expected_checksum_paths:
        raise ValueError("SHA256SUMS coverage differs from package files")
    for relative, expected in checksums.items():
        actual = sha256(package / relative)
        if actual != expected:
            raise ValueError(f"package checksum mismatch: {relative}")
    if sha256(package / "bin" / "xdelta3.exe") != XDELTA_WINDOWS_SHA256:
        raise ValueError("packaged Windows xdelta3 hash differs from official pinned binary")

    full_original_hashes = {item["sha256"] for item in COMPATIBILITY}
    full_target_hashes = {spec["target_sha256"] for spec in RESOURCE_SPECS}
    for path in package.rglob("*"):
        if path.is_file() and sha256(path) in full_original_hashes | full_target_hashes:
            raise ValueError(f"package contains a full original or localized resource: {path}")
    forbidden_bytes = [value.encode("utf-8") for value in forbidden_strings if value]
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        payload = path.read_bytes()
        for value in forbidden_bytes:
            if value in payload:
                raise ValueError(f"private path or token found in package file {path.name}")

    decoded = []
    with tempfile.TemporaryDirectory(prefix="aeterna-release-audit-") as temp_name:
        temp = Path(temp_name)
        for item in files:
            relative = PurePosixPath(str(item["relative_path"]))
            name = relative.name
            source = source_root.joinpath(*relative.parts)
            patch = package.joinpath(*PurePosixPath(str(item["patch_path"])).parts)
            target = candidate_data / name
            if patch.read_bytes()[:4] != b"\xd6\xc3\xc4\x00":
                raise ValueError(f"patch is not a VCDIFF stream: {patch.name}")
            if sha256(source) != item["source_sha256"] or source.stat().st_size != item["source_size"]:
                raise ValueError(f"source input differs for {name}")
            if sha256(target) != item["target_sha256"] or target.stat().st_size != item["target_size"]:
                raise ValueError(f"candidate input differs for {name}")
            if sha256(patch) != item["patch_sha256"] or patch.stat().st_size != item["patch_size"]:
                raise ValueError(f"patch metadata differs for {name}")
            if patch.stat().st_size >= target.stat().st_size:
                raise ValueError(f"patch is not smaller than the complete target: {name}")
            output = temp / name
            decode_patch(xdelta3, source, patch, output)
            if sha256(output) != item["target_sha256"] or output.stat().st_size != target.stat().st_size:
                raise ValueError(f"decoded output is not byte-identical to final candidate: {name}")

            no_source_output = temp / f"{name}.without-source"
            isolated = temp / f"isolated-{len(decoded)}"
            isolated.mkdir()
            no_source = subprocess.run(
                [str(xdelta3.resolve()), "-d", "-f", str(patch.resolve()), str(no_source_output)],
                cwd=isolated,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if no_source.returncode == 0:
                raise ValueError(f"patch decoded without the required original source: {name}")
            decoded.append(
                {
                    "name": name,
                    "patch_sha256": item["patch_sha256"],
                    "patch_size": item["patch_size"],
                    "target_sha256": item["target_sha256"],
                }
            )

    validate_archive(package, archive)
    archive_sidecar = archive.with_suffix(archive.suffix + ".sha256")
    if not archive_sidecar.is_file():
        raise ValueError("missing archive checksum sidecar")
    expected_sidecar = f"{sha256(archive)}  {archive.name}\n"
    if archive_sidecar.read_text(encoding="ascii") != expected_sidecar:
        raise ValueError("archive checksum sidecar differs")
    return {
        "result": "PASS",
        "package": package.name,
        "archive_sha256": sha256(archive),
        "archive_size": archive.stat().st_size,
        "files": decoded,
        "full_original_or_target_files": 0,
        "private_tokens": 0,
        "standalone_decode_attempts_rejected": len(decoded),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("AeternaNoctis"))
    parser.add_argument(
        "--candidate-data", type=Path, default=Path("build/opening-ko/Aeterna Noctis_Data")
    )
    parser.add_argument("--xdelta3", type=Path, required=True)
    parser.add_argument("--forbidden-string", action="append", default=[])
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        report = audit_release(
            args.package,
            args.archive,
            args.source_root,
            args.candidate_data,
            args.xdelta3,
            tuple(args.forbidden_string),
        )
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            temp = args.report.with_suffix(args.report.suffix + ".tmp")
            temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
            temp.replace(args.report)
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        sys.exit(f"FAIL: {exc}")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
