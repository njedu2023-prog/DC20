"""Offline adversarial fixtures; no source collection or production writes."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
import unittest
from unittest.mock import patch
import zipfile


spec = importlib.util.spec_from_file_location("receive_artifact", Path(__file__).with_name("receive_artifact.py"))
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ReceiveArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dc20-receive-test.")
        self.root = Path(self.temporary.name).resolve()
        self.zip = self.root / "source.zip"
        self.output = self.root / "received"

    def tearDown(self):
        self.temporary.cleanup()

    def make_zip(self, members=None, mapping=None, extra=None, manifest_bytes=None):
        members = members if members is not None else [("PLAN.json", b"{}"), ("candidate_sources/20260828/daily.csv", b"code,open\nA,1.25\n")]
        if mapping is None:
            mapping = {(name.filename if isinstance(name, zipfile.ZipInfo) else name): {"bytes": len(data), "sha256": sha(data)} for name, data in members}
        manifest_bytes = manifest_bytes or json.dumps({"schema_version": "dc20_isolated_source_artifact_manifest_v1", "files": mapping}, sort_keys=True).encode()
        with zipfile.ZipFile(self.zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr(receiver.MANIFEST, manifest_bytes)
            for name, data in members + (extra or []):
                z.writestr(name, data)
        self.zip_sha = sha(self.zip.read_bytes())
        self.manifest_sha = sha(manifest_bytes)

    def receive(self):
        return receiver.receive(self.zip, self.zip_sha, self.manifest_sha, self.output)

    def assert_blocked_without_output(self):
        with self.assertRaises((receiver.ReceiptError, OSError, ValueError, zipfile.BadZipFile)):
            self.receive()
        self.assertFalse(self.output.exists())

    def test_success_hashes_bytes_and_no_training_permission(self):
        self.make_zip()
        result = self.receive()
        self.assertEqual((self.output / "PLAN.json").read_bytes(), b"{}")
        self.assertEqual(result["verified_files_including_manifest"], 3)
        self.assertTrue(result["integrity_verified"])
        self.assertFalse(result["source_ready_for_label_rebuild"])
        self.assertFalse(result["training_authorized"])
        self.assertEqual(stat.S_IMODE((self.output / "PLAN.json").stat().st_mode), 0o600)
        with self.assertRaises(receiver.ReceiptError):
            self.receive()

    def test_existing_empty_output_is_supported(self):
        self.make_zip()
        self.output.mkdir()
        self.assertTrue(self.receive()["integrity_verified"])

    def test_external_zip_hash_is_first_content_gate(self):
        self.make_zip()
        self.zip_sha = "0" * 64
        with patch.object(receiver.zipfile, "ZipFile", side_effect=AssertionError("must not parse")):
            self.assert_blocked_without_output()

    def test_external_manifest_digest_mismatch(self):
        self.make_zip()
        self.manifest_sha = "0" * 64
        self.assert_blocked_without_output()

    def test_bad_expected_digest_format(self):
        self.make_zip()
        self.zip_sha = "sha256:untrusted"
        self.assert_blocked_without_output()

    def test_manifest_member_hash_mismatch_precedes_any_write(self):
        self.make_zip(members=[("a", b"x")], mapping={"a": {"bytes": 1, "sha256": "0" * 64}})
        self.assert_blocked_without_output()

    def test_manifest_member_byte_mismatch(self):
        self.make_zip(members=[("a", b"x")], mapping={"a": {"bytes": 2, "sha256": sha(b"x")}})
        self.assert_blocked_without_output()

    def test_manifest_boolean_byte_count_is_not_integer(self):
        self.make_zip(members=[("a", b"x")], mapping={"a": {"bytes": True, "sha256": sha(b"x")}})
        self.assert_blocked_without_output()

    def test_extra_zip_file(self):
        self.make_zip(extra=[("not-bound.txt", b"x")])
        self.assert_blocked_without_output()

    def test_missing_zip_file(self):
        self.make_zip(members=[], mapping={"missing": {"bytes": 1, "sha256": sha(b"x")}})
        self.assert_blocked_without_output()

    def test_duplicate_zip_file(self):
        self.make_zip(members=[("a", b"x"), ("a", b"x")])
        self.assert_blocked_without_output()

    def test_unsafe_paths(self):
        for path in ("../escape", "/absolute", "a/../escape", "a//b", "a/./b", "a\\b", "C:/x", "a\x00b", "é.txt"):
            with self.subTest(path=path):
                self.make_zip(members=[(path, b"x")])
                self.assert_blocked_without_output()

    def test_file_directory_conflict(self):
        self.make_zip(members=[("a", b"x"), ("a/b", b"x")])
        self.assert_blocked_without_output()

    def test_case_collisions_in_files_and_implicit_directories(self):
        for members in ([('a', b'x'), ('A', b'x')], [('dir/a', b'x'), ('DIR/b', b'x')]):
            self.make_zip(members=members)
            self.assert_blocked_without_output()

    def test_symlink_fifo_socket_and_explicit_directory(self):
        for mode in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFDIR):
            with self.subTest(mode=mode):
                member = zipfile.ZipInfo("special")
                member.create_system = 3
                member.external_attr = (mode | 0o644) << 16
                self.make_zip(members=[(member, b"x")])
                self.assert_blocked_without_output()
        self.make_zip(members=[("directory/", b"")])
        self.assert_blocked_without_output()

    def test_dos_directory_attribute(self):
        member = zipfile.ZipInfo("special")
        member.create_system = 0
        member.external_attr = 0x10
        self.make_zip(members=[(member, b"x")])
        self.assert_blocked_without_output()

    def test_limits_file_count_member_including_manifest_and_total(self):
        for setting, limit in (("MAX_FILES", 2), ("MAX_FILE_BYTES", 20), ("MAX_TOTAL_BYTES", 100), ("MAX_ZIP_BYTES", 1)):
            self.make_zip()
            with patch.object(receiver, setting, limit):
                self.assert_blocked_without_output()

    def test_duplicate_json_keys(self):
        self.make_zip(members=[], manifest_bytes=b'{"schema_version":"dc20_isolated_source_artifact_manifest_v1","files":{},"files":{}}')
        self.assert_blocked_without_output()

    def test_forged_central_directory_count_blocked_before_zipfile_allocation(self):
        self.make_zip()
        data = bytearray(self.zip.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        struct.pack_into("<2H", data, end + 8, 1, 1)
        self.zip.write_bytes(data)
        self.zip_sha = sha(data)
        with patch.object(receiver.zipfile, "ZipFile", side_effect=AssertionError("must not allocate")):
            self.assert_blocked_without_output()

    def test_split_archive_is_rejected(self):
        self.make_zip()
        data = bytearray(self.zip.read_bytes())
        end = data.rfind(b"PK\x05\x06")
        struct.pack_into("<H", data, end + 4, 1)
        self.zip.write_bytes(data)
        self.zip_sha = sha(data)
        self.assert_blocked_without_output()

    def test_manifest_cannot_bind_itself(self):
        self.make_zip(members=[], mapping={receiver.MANIFEST: {"bytes": 1, "sha256": sha(b"x")}})
        self.assert_blocked_without_output()

    def test_output_nonempty_preserved(self):
        self.make_zip()
        self.output.mkdir()
        sentinel = self.output / "keep"
        sentinel.write_bytes(b"unchanged")
        with self.assertRaises(receiver.ReceiptError):
            self.receive()
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertEqual(list(self.output.iterdir()), [sentinel])

    def test_output_symlink_and_symlink_ancestor(self):
        self.make_zip()
        real = self.root / "real"
        real.mkdir()
        self.output.symlink_to(real, target_is_directory=True)
        with self.assertRaises(OSError):
            self.receive()
        self.assertEqual(list(real.iterdir()), [])
        self.output = self.output / "nested"
        self.assert_blocked_without_output()

    def test_input_symlink(self):
        self.make_zip()
        link = self.root / "link.zip"
        link.symlink_to(self.zip)
        self.zip = link
        self.assert_blocked_without_output()

    def test_input_fifo_rejected_without_blocking(self):
        self.zip_sha = self.manifest_sha = "0" * 64
        os.mkfifo(self.zip)
        self.assert_blocked_without_output()

    def test_input_hardlink_rejected(self):
        self.make_zip()
        os.link(self.zip, self.root / "second-link.zip")
        self.assert_blocked_without_output()

    def test_output_production_repository_and_relative_rejected(self):
        self.make_zip()
        for output in (receiver.REPOSITORY / "new-evidence", Path("/Users/moclh/Documents/ChatGPT/DC20/new-evidence"), Path("relative"), Path("/")):
            with self.subTest(output=output), self.assertRaises(receiver.ReceiptError):
                receiver.receive(self.zip, self.zip_sha, self.manifest_sha, output)

    def test_unexpected_mutation_after_validation_does_not_extract(self):
        self.make_zip()
        initial = (1, 2, 3, 4, 5)
        with patch.object(receiver, "_source_identity", side_effect=[initial, (9, 2, 3, 4, 5)]):
            self.assert_blocked_without_output()

    def test_validation_failure_leaves_preexisting_empty_directory_empty(self):
        self.make_zip(members=[("a", b"x")], mapping={"a": {"bytes": 1, "sha256": "0" * 64}})
        self.output.mkdir()
        with self.assertRaises(receiver.ReceiptError):
            self.receive()
        self.assertEqual(list(self.output.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
