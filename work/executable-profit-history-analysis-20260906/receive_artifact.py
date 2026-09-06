#!/usr/bin/env python3
"""Offline, fail-closed receipt of a separately authenticated source artifact ZIP.

Both digests must come from external trusted evidence, not from inside the ZIP.
Use physical absolute paths (on macOS, /private/tmp rather than /tmp). An output
directory may be absent or already empty; every file is created exclusively.
Integrity receipt is not source admission, label quality, or training approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import zipfile


MAX_FILES = 25_000  # Includes the manifest; explicit directory entries are forbidden.
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_BYTES = MAX_TOTAL_BYTES + MAX_FILES * 4096
CHUNK_BYTES = 1024 * 1024
MANIFEST = "artifact_manifest.json"
HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[1]
PROTECTED_ROOTS = (REPOSITORY, Path("/Users/moclh/Documents/ChatGPT/DC20"))
DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class ReceiptError(ValueError):
    """No integrity receipt was issued; do not admit the source."""


def _digest(value: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ReceiptError(f"{label}: expected an externally supplied lowercase SHA-256")
    return value


def _physical_path(value: str | Path) -> Path:
    raw = os.fspath(value)
    path = Path(raw)
    if not path.is_absolute() or raw.startswith("//") or raw != str(path) or any(x in (".", "..") for x in raw.split("/")[1:]):
        raise ReceiptError("path must be a normalized physical absolute path")
    if "\\" in raw or "\x00" in raw:
        raise ReceiptError("unsafe filesystem path")
    return path


def _open_directory(path: Path) -> int:
    """Walk by descriptors so no ancestor symlink is ever followed."""
    descriptor = os.open("/", DIRECTORY_FLAGS)
    try:
        for part in path.parts[1:]:
            next_descriptor = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _output_preflight(path: Path, zip_path: Path) -> None:
    if len(path.parts) < 4 or any(_under(path, root) or _under(root, path) for root in PROTECTED_ROOTS):
        raise ReceiptError("output must be a separate evidence directory, not a production/repository path")
    if _under(zip_path, path):
        raise ReceiptError("output cannot contain its input ZIP")
    # Inode comparison also catches a case-insensitive filesystem alias of a
    # protected root; lexical prefix checks alone do not suffice on macOS.
    protected_ids = {(s.st_dev, s.st_ino) for root in PROTECTED_ROOTS if root.exists() for s in [root.stat()]}
    for ancestor in path.parents:
        ancestor_fd = _open_directory(ancestor)
        try:
            s = os.fstat(ancestor_fd)
            if (s.st_dev, s.st_ino) in protected_ids:
                raise ReceiptError("output ancestor aliases a protected repository")
        finally:
            os.close(ancestor_fd)
    parent_fd = _open_directory(path.parent)
    try:
        try:
            output_fd = os.open(path.name, DIRECTORY_FLAGS, dir_fd=parent_fd)
        except FileNotFoundError:
            return
        try:
            if os.listdir(output_fd):
                raise ReceiptError("output directory is not empty; overwriting is forbidden")
        finally:
            os.close(output_fd)
    finally:
        os.close(parent_fd)


def _safe_member(name: str) -> str:
    # This artifact protocol uses ASCII POSIX file names. Refuse normalization,
    # Unicode/case aliases and Windows drive syntax instead of repairing them.
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,2048}", name):
        raise ReceiptError("unsafe archive member name")
    pieces = name.split("/")
    if len(pieces) > 32 or any(x in ("", ".", "..") for x in pieces) or str(PurePosixPath(name)) != name:
        raise ReceiptError("unsafe archive member path")
    return name


def _validate_names(names: list[str]) -> None:
    seen = set()
    aliases: dict[str, str] = {}
    for name in names:
        _safe_member(name)
        if name in seen:
            raise ReceiptError("duplicate archive member")
        seen.add(name)
        parts = name.split("/")
        for length in range(1, len(parts) + 1):
            prefix = "/".join(parts[:length])
            previous = aliases.setdefault(prefix.lower(), prefix)
            if previous != prefix:
                raise ReceiptError("case-alias archive paths")
    for name in names:
        parts = name.split("/")
        if any("/".join(parts[:n]) in seen for n in range(1, len(parts))):
            raise ReceiptError("archive file is also a parent directory")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReceiptError("duplicate manifest JSON key")
        result[key] = value
    return result


def _stream_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, destination=None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(info, "r") as source:
        while chunk := source.read(CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_FILE_BYTES or size > info.file_size:
                raise ReceiptError("archive member exceeded declared/allowed size")
            digest.update(chunk)
            if destination is not None:
                destination.write(chunk)
    if size != info.file_size:
        raise ReceiptError("archive member size differs from ZIP metadata")
    return digest.hexdigest(), size


def _source_identity(source) -> tuple:
    s = os.fstat(source.fileno())
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def _bounded_central_directory(source) -> None:
    """Bound entry allocation before ZipFile builds its in-memory file list.

The <=25k members / <=2GiB protocol needs neither ZIP64 nor split archives.
Do not permit self-extracting prefixes, appendages or a forged entry count.
"""
    size = os.fstat(source.fileno()).st_size
    start = max(0, size - (65535 + 22))
    source.seek(start)
    tail = source.read(65535 + 22)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        raise ReceiptError("ZIP end record is absent")
    _, disk, directory_disk, disk_count, count, directory_bytes, directory_start, comment_bytes = struct.unpack(
        "<4s4H2IH", tail[offset:offset + 22])
    if (disk or directory_disk or disk_count != count or not 1 <= count <= MAX_FILES
            or count == 65535 or directory_bytes == 0xFFFFFFFF or directory_start == 0xFFFFFFFF
            or offset + 22 + comment_bytes != len(tail)
            or directory_start + directory_bytes != start + offset
            or directory_bytes > MAX_FILES * 8192):
        raise ReceiptError("unsupported or oversized ZIP directory")
    source.seek(directory_start)
    actual_count = 0
    while source.tell() < directory_start + directory_bytes:
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ReceiptError("malformed ZIP directory entry")
        name_bytes, extra_bytes, entry_comment_bytes = struct.unpack_from("<3H", header, 28)
        extra_length = name_bytes + extra_bytes + entry_comment_bytes
        actual_count += 1
        if actual_count > MAX_FILES or extra_length > 8192 or source.tell() + extra_length > directory_start + directory_bytes:
            raise ReceiptError("ZIP directory allocation exceeds protocol limits")
        source.seek(extra_length, os.SEEK_CUR)
    if actual_count != count:
        raise ReceiptError("ZIP directory entry count mismatch")
    source.seek(0)


def _exclusive_member(root_fd: int, name: str):
    descriptor = os.dup(root_fd)
    try:
        parts = name.split("/")
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            next_descriptor = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=descriptor)
        return os.fdopen(file_fd, "wb")
    finally:
        os.close(descriptor)


def receive(zip_path: str | Path, expected_zip_sha256: str, expected_manifest_sha256: str,
            output_directory: str | Path) -> dict:
    expected_zip_sha256 = _digest(expected_zip_sha256, "ZIP")
    expected_manifest_sha256 = _digest(expected_manifest_sha256, "manifest")
    zip_path = _physical_path(zip_path)
    output = _physical_path(output_directory)
    _output_preflight(output, zip_path)
    parent_fd = _open_directory(zip_path.parent)
    try:
        source_fd = os.open(zip_path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    with os.fdopen(source_fd, "rb") as source:
        file_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1 or file_stat.st_size > MAX_ZIP_BYTES:
            raise ReceiptError("ZIP must be a bounded regular file, not a link or special file")
        identity = _source_identity(source)
        actual_zip_hash = hashlib.file_digest(source, "sha256").hexdigest()
        if actual_zip_hash != expected_zip_sha256:
            raise ReceiptError("external ZIP SHA-256 mismatch")
        _bounded_central_directory(source)
        with zipfile.ZipFile(source, "r") as archive:
            infos = archive.infolist()
            if not 1 <= len(infos) <= MAX_FILES:
                raise ReceiptError("archive file count exceeds limit or is empty")
            names = [x.filename for x in infos]
            _validate_names(names)
            total = 0
            for info in infos:
                mode = info.external_attr >> 16
                if (info.orig_filename != info.filename or info.is_dir() or info.external_attr & 0x10
                        or stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise ReceiptError("archive contains a directory, link or special file")
                if info.flag_bits & 1 or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ReceiptError("encrypted or unsupported compression member")
                if not 0 <= info.file_size <= MAX_FILE_BYTES:
                    raise ReceiptError("archive member exceeds size limit")
                total += info.file_size
            if total > MAX_TOTAL_BYTES:
                raise ReceiptError("archive exceeds total uncompressed size limit")
            by_name = {x.filename: x for x in infos}
            if MANIFEST not in by_name:
                raise ReceiptError("artifact manifest is absent")
            manifest_bytes = archive.read(by_name[MANIFEST])
            if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest_sha256:
                raise ReceiptError("external manifest SHA-256 mismatch")
            manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_object,
                                  parse_constant=lambda _: (_ for _ in ()).throw(ReceiptError("nonfinite manifest JSON")))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != "dc20_isolated_source_artifact_manifest_v1":
                raise ReceiptError("unsupported source manifest schema")
            files = manifest.get("files")
            if not isinstance(files, dict) or MANIFEST in files:
                raise ReceiptError("invalid manifest file mapping")
            _validate_names(list(files) + [MANIFEST])
            if set(names) != set(files) | {MANIFEST}:
                raise ReceiptError("ZIP and manifest file sets differ")
            expected = {MANIFEST: {"bytes": len(manifest_bytes), "sha256": expected_manifest_sha256}}
            for name, evidence in files.items():
                if not isinstance(evidence, dict) or type(evidence.get("bytes")) is not int:
                    raise ReceiptError("invalid manifest byte count")
                if not 0 <= evidence["bytes"] <= MAX_FILE_BYTES or evidence["bytes"] != by_name[name].file_size:
                    raise ReceiptError("manifest and ZIP byte counts differ")
                _digest(evidence.get("sha256"), "member")
                expected[name] = evidence
            # No output is created before every member, including CRC, is checked.
            for info in infos:
                digest, size = _stream_member(archive, info)
                if (digest, size) != (expected[info.filename]["sha256"], expected[info.filename]["bytes"]):
                    raise ReceiptError(f"manifest content mismatch: {info.filename}")
            if _source_identity(source) != identity:
                raise ReceiptError("input ZIP changed during verification")
            _output_preflight(output, zip_path)
            parent_fd = _open_directory(output.parent)
            try:
                try:
                    os.mkdir(output.name, mode=0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
                root_fd = os.open(output.name, DIRECTORY_FLAGS, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
            try:
                if os.listdir(root_fd):
                    raise ReceiptError("output was populated before extraction; refusing overwrite")
                for info in infos:
                    with _exclusive_member(root_fd, info.filename) as destination:
                        digest, size = _stream_member(archive, info, destination)
                    if (digest, size) != (expected[info.filename]["sha256"], expected[info.filename]["bytes"]):
                        raise ReceiptError("input changed during extraction; partial output is not admitted")
                if _source_identity(source) != identity:
                    raise ReceiptError("input ZIP changed during extraction; partial output is not admitted")
            finally:
                os.close(root_fd)
    return {"schema_version": "dc20_offline_source_zip_receipt_v1", "output_directory": str(output),
            "zip_sha256": actual_zip_hash, "manifest_sha256": expected_manifest_sha256,
            "verified_files_including_manifest": len(infos), "verified_bytes_including_manifest": total,
            "integrity_verified": True, "source_ready_for_label_rebuild": False,
            "training_authorized": False, "production_release_authorized": False,
            "next_step": "Independent source audit is still required; acquisition is not historical-as-of evidence."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, dest="zip_path")
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--output-directory", required=True)
    args = parser.parse_args()
    try:
        result = receive(**vars(args))
    except (ReceiptError, OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        parser.exit(2, f"BLOCK_RECEIPT: {exc}. No admission issued; any partial output must not be used.\n")
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
