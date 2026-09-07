#!/usr/bin/env python3
"""Inject Localization.csv into a new sharedassets1.assets file.

The source container is opened read-only. UnityPy serializes the modified
container into --out; this command never overwrites its source path.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import UnityPy


EXPECTED_PATH_ID = 323
EXPECTED_NAME = "Localization"


def inject(source_assets: Path, localization_csv: Path, out_path: Path) -> None:
    if source_assets.resolve() == out_path.resolve():
        raise ValueError("source and output paths must differ")
    payload = localization_csv.read_bytes()
    payload.decode("utf-8")

    env = UnityPy.load(str(source_assets))
    matches = []
    for obj in env.objects:
        if obj.type.name != "TextAsset":
            continue
        data = obj.read()
        if getattr(data, "m_Name", "") == EXPECTED_NAME:
            matches.append((obj, data))
    if len(matches) != 1:
        raise ValueError(f"expected one TextAsset named {EXPECTED_NAME}, got {len(matches)}")
    obj, data = matches[0]
    if obj.path_id != EXPECTED_PATH_ID:
        raise ValueError(f"unexpected Localization path_id: {obj.path_id}")

    data.m_Script = payload.decode("utf-8")
    data.save()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(env.file.save())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-assets", type=Path, required=True)
    parser.add_argument("--localization-csv", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        inject(args.source_assets, args.localization_csv, args.out)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        sys.exit(f"ERROR: {exc}")
    payload = args.out.read_bytes()
    print(f"bytes  : {len(payload)}")
    print(f"sha256 : {hashlib.sha256(payload).hexdigest()}")
    print(f"out    : {args.out}")


if __name__ == "__main__":
    main()
