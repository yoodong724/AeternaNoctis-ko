#!/usr/bin/env python3
"""Build localized resources from a user's original game and the Korean-only TSV."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

import UnityPy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.integrate.inject_textasset import inject as inject_text
from scripts.integrate.inject_tmp_font import inject as inject_font
from scripts.release.build_patch import COMPATIBILITY, reject_overlap, require_hash, sha256

CSV_SHA256 = 'e57d07f3fdc3507a2f3f00ffc86db99e9d9618b925dc3bb4e3afa7866213f5c1'
HEADER = ['source_order', 'source_key', 'source_hash', 'target_text']


def unescape(value: str) -> str:
    mapping = {'\\': '\\', 't': '\t', 'r': '\r', 'n': '\n'}
    result = []
    i = 0
    while i < len(value):
        if value[i] != '\\':
            result.append(value[i])
            i += 1
        else:
            if i + 1 >= len(value) or value[i + 1] not in mapping:
                raise ValueError('Invalid TSV escape')
            result.append(mapping[value[i + 1]])
            i += 2
    return ''.join(result)


def apply_translations(source: bytes, translations: Path) -> bytes:
    if hashlib.sha256(source).hexdigest() != CSV_SHA256:
        raise ValueError('Original Localization CSV hash differs from supported build')
    rows = list(csv.reader(io.StringIO(source.decode('utf-8'), newline='')))
    if len(rows) != 4711 or len(rows[0]) != 12:
        raise ValueError('Unexpected Localization CSV structure')
    language = rows[0].index('Japanese')
    standard = rows[0].index('Standard')
    lines = translations.read_text(encoding='utf-8').splitlines()
    if not lines or lines[0].split('\t') != HEADER:
        raise ValueError('Unexpected Korean TSV header')
    seen = set()
    for line in lines[1:]:
        fields = line.split('\t')
        if len(fields) != len(HEADER):
            raise ValueError('Unexpected Korean TSV column count')
        order_text, key, expected_hash, target = map(unescape, fields)
        order = int(order_text)
        if order in seen or not 0 <= order < len(rows) - 1 or not target:
            raise ValueError('Duplicate/invalid source_order or empty translation')
        seen.add(order)
        row = rows[order + 1]
        if row[0] != key or hashlib.sha256(row[standard].encode('utf-8')).hexdigest()[:16] != expected_hash:
            raise ValueError(f'Source key/hash mismatch at order {order}')
        row[language] = target
    if not seen:
        raise ValueError('Translation table is empty')
    out = io.StringIO(newline='')
    csv.writer(out, lineterminator='\r\n').writerows(rows)
    return out.getvalue().encode('utf-8')


def build(source_root: Path, translations: Path, bundle: Path, output: Path) -> dict:
    source_root, output = source_root.resolve(), output.resolve()
    reject_overlap(output, (source_root, translations.resolve(), bundle.resolve()))
    if output.exists():
        raise ValueError('Output already exists; choose a new --output-root directory')
    for item in COMPATIBILITY:
        require_hash(source_root / item['relative_path'], item['sha256'], item['relative_path'])
    data = source_root / 'Aeterna Noctis_Data'
    env = UnityPy.load(str(data / 'sharedassets1.assets'))
    matches = [o for o in env.objects if o.type.name == 'TextAsset' and o.path_id == 323]
    if len(matches) != 1:
        raise ValueError('Localization TextAsset #323 is missing')
    asset = matches[0].read()
    if asset.m_Name != 'Localization':
        raise ValueError('Unexpected TextAsset name')
    localized = apply_translations(asset.m_Script.encode('utf-8'), translations)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.translation-', dir=output.parent) as temp:
        stage = Path(temp) / 'candidate'
        result_data = stage / 'Aeterna Noctis_Data'
        result_data.mkdir(parents=True)
        csv_path = result_data / 'Localization.csv'
        csv_path.write_bytes(localized)
        inject_text(data / 'sharedassets1.assets', csv_path, result_data / 'sharedassets1.assets')
        font = inject_font(data / 'resources.assets', data / 'resources.assets.resS', bundle,
                           result_data / 'resources.assets', result_data / 'resources.assets.resS')
        hashes = {name: sha256(result_data / name) for name in
                  ('sharedassets1.assets', 'resources.assets', 'resources.assets.resS')}
        report = {'candidate_hashes': hashes, 'font': font}
        (stage / 'MANIFEST.json').write_text(json.dumps(report, indent=2) + '\n')
        stage.replace(output)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, default=Path('AeternaNoctis'))
    parser.add_argument('--translations', type=Path, default=Path('translations/ko.tsv'))
    parser.add_argument('--generated-bundle', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, default=Path('build/opening-ko'))
    args = parser.parse_args()
    try:
        result = build(args.source_root, args.translations, args.generated_bundle, args.output_root)
    except (OSError, ValueError) as exc:
        sys.exit(f'ERROR: {exc}')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
