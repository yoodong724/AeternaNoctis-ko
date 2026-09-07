#!/usr/bin/env python3
"""Extract a Unity .unitypackage into a project without opening the GUI."""
from __future__ import annotations

import argparse
import sys
import tarfile
from pathlib import Path, PurePosixPath


def extract(package: Path, project: Path) -> int:
    count = 0
    with tarfile.open(package, mode="r:gz") as archive:
        members = {member.name.rstrip("/"): member for member in archive.getmembers()}
        guid_dirs = sorted({name.split("/", 1)[0] for name in members if "/" in name})
        for guid in guid_dirs:
            pathname_member = members.get(f"{guid}/pathname")
            if pathname_member is None:
                continue
            pathname_file = archive.extractfile(pathname_member)
            if pathname_file is None:
                continue
            relative_text = pathname_file.read().decode("utf-8").strip().replace("\\", "/")
            relative = PurePosixPath(relative_text)
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError(f"unsafe unitypackage path: {relative_text!r}")
            if relative.parts[0] != "Assets":
                raise ValueError(f"unitypackage entry is outside Assets: {relative_text!r}")

            asset_member = members.get(f"{guid}/asset")
            meta_member = members.get(f"{guid}/asset.meta")
            target = project.joinpath(*relative.parts)
            if asset_member is None:
                target.mkdir(parents=True, exist_ok=True)
            else:
                source = archive.extractfile(asset_member)
                if source is None:
                    raise ValueError(f"could not read asset: {relative_text}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                count += 1
            if meta_member is not None:
                source = archive.extractfile(meta_member)
                if source is None:
                    raise ValueError(f"could not read meta: {relative_text}")
                meta_target = target.with_name(target.name + ".meta")
                meta_target.parent.mkdir(parents=True, exist_ok=True)
                meta_target.write_bytes(source.read())
    return count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    try:
        count = extract(args.package, args.project)
    except (OSError, ValueError, tarfile.TarError, UnicodeDecodeError) as exc:
        sys.exit(f"ERROR: {exc}")
    print(f"assets: {count}")
    print(f"project: {args.project}")


if __name__ == "__main__":
    main()
