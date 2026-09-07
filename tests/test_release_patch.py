from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build_patch = load_module("build_patch", ROOT / "scripts/release/build_patch.py")
check_release = load_module(
    "check_release_patch", ROOT / "scripts/validate/check_release_patch.py"
)


class ReleaseBuilderSafetyTests(unittest.TestCase):
    def test_version_is_strict(self):
        self.assertEqual(build_patch.validate_version("1.0.0"), "1.0.0")
        for value in ("v1.0.0", "../1.0.0", "1.0", "1.0.0 bad"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_patch.validate_version(value)

    def test_output_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protected = root / "game"
            protected.mkdir()
            for output in (protected, protected / "dist", root):
                with self.subTest(output=output), self.assertRaisesRegex(ValueError, "overlaps"):
                    build_patch.reject_overlap(output, (protected,))

    def test_zip_is_reproducible_and_rooted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "AeternaNoctis-ko"
            package.mkdir()
            (package / "install.cmd").write_text("@echo off\n", encoding="ascii")
            first = root / "first.zip"
            second = root / "second.zip"
            build_patch.write_reproducible_zip(package, first)
            build_patch.write_reproducible_zip(package, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(archive.namelist(), ["AeternaNoctis-ko/install.cmd"])
            check_release.validate_archive(package, first)
            (package / "install.cmd").write_text("tampered\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "differs"):
                check_release.validate_archive(package, first)


class ReleaseAuditSafetyTests(unittest.TestCase):
    def test_unsafe_relative_paths_are_rejected(self):
        for value in ("", "/absolute", "../escape", "a/../../escape"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                check_release.safe_relative(value)

    def test_checksum_parser_requires_exact_rows_and_unique_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "SHA256SUMS.txt"
            path.write_text("0" * 64 + "  file.txt\n", encoding="ascii")
            self.assertEqual(check_release.parse_checksums(path), {"file.txt": "0" * 64})
            path.write_text("0" * 64 + "  file.txt\n" + "1" * 64 + "  file.txt\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                check_release.parse_checksums(path)

    def test_manifest_requires_exact_acceptance_and_compatibility(self):
        manifest = {
            "schema_version": 1,
            "package_name": build_patch.PACKAGE_NAME,
            "patch_version": build_patch.PATCH_VERSION,
            "target_build": {"build_guid": build_patch.BUILD_GUID},
            "human_acceptance_status": build_patch.HUMAN_ACCEPTANCE_STATUS,
            "packaging_authorization": build_patch.PACKAGING_AUTHORIZATION,
            "language_slot": "Japanese",
            "language_output": "ko-KR",
            "compatibility": list(build_patch.COMPATIBILITY),
            "tool": {
                "name": "xdelta3",
                "version": build_patch.XDELTA_VERSION,
                "license": "Apache-2.0",
                "binary_path": "bin/xdelta3.exe",
                "binary_sha256": build_patch.XDELTA_WINDOWS_SHA256,
            },
            "files": [
                {
                    "relative_path": f"Aeterna Noctis_Data/{spec['name']}",
                    "patch_path": f"patch/{spec['name']}.vcdiff",
                    "source_sha256": spec["source_sha256"],
                    "target_sha256": spec["target_sha256"],
                }
                for spec in build_patch.RESOURCE_SPECS
            ],
        }
        self.assertEqual(len(check_release.validate_manifest(manifest, manifest["package_name"])), 3)
        mutated = json.loads(json.dumps(manifest))
        mutated["human_acceptance_status"] = "ACCEPTED_WITH_KNOWN_ISSUES"
        with self.assertRaisesRegex(ValueError, "acceptance"):
            check_release.validate_manifest(mutated, manifest["package_name"])
        for field, value in (("patch_version", "1.0.0"), ("package_name", "WrongFolder"),
                             ("packaging_authorization", "")):
            mutated = json.loads(json.dumps(manifest))
            mutated[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                check_release.validate_manifest(mutated, manifest["package_name"])
        mutated = json.loads(json.dumps(manifest))
        mutated["files"][0]["target_sha256"] = "0ee7e3652cd15ca32e69eb817080b2c6cf3c644ebbd65e8f9fcde81b33609955"
        with self.assertRaisesRegex(ValueError, "target hash"):
            check_release.validate_manifest(mutated, manifest["package_name"])


if __name__ == "__main__":
    unittest.main()
