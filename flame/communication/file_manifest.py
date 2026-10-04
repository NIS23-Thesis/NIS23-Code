"""Application-level metadata for SENSOR_DATA file transfers.

FLAME's four-byte transport header is intentionally unchanged.  A file
transfer is identified by the existing ``source_address``, ``SENSOR_DATA``
packet type, ``data_id``, and sequence number.  This module adds a small
application envelope inside the SENSOR_DATA payload:

    magic | version | manifest length | UTF-8 JSON manifest | file bytes

The same batch manifest is carried with each file transfer.  Therefore a
Cluster Head can receive the files independently, map every ``data_id`` to a
file, and validate the exact byte length after reassembly without treating a
FLAME block as an image boundary.  The Cluster Head accepts encoded PNG and
JPEG files only.  Raw image-array metadata is represented for completeness,
but the receiver rejects raw transfers under the thesis' PNG/JPEG policy.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional


FILE_PAYLOAD_MAGIC = b"FLMF"
FILE_PAYLOAD_VERSION = 1
SUPPORTED_ENCODINGS = frozenset({"png", "jpeg", "raw"})
ACCEPTED_ENCODINGS = frozenset({"png", "jpeg"})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
_PAYLOAD_HEADER = struct.Struct("!4sBI")


@dataclass(frozen=True)
class FileMetadata:
    """Describe one file in a batch manifest."""

    data_id: int
    logical_name: str
    file_type: str
    encoding: str
    byte_length: int
    width: Optional[int] = None
    height: Optional[int] = None
    dtype: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.data_id, int) or not 0 <= self.data_id <= 0xFF:
            raise ValueError("File data_id must fit within 8 bits.")
        if (
            not isinstance(self.logical_name, str)
            or not self.logical_name
            or "\x00" in self.logical_name
        ):
            raise ValueError("File logical_name must be a non-empty string without NUL bytes.")
        if not isinstance(self.file_type, str) or not self.file_type:
            raise ValueError("File type must be a non-empty string.")
        object.__setattr__(self, "file_type", self.file_type.lower())
        if not isinstance(self.encoding, str):
            raise ValueError("File encoding must be a string.")

        normalized_encoding = self.encoding.lower()
        if normalized_encoding not in SUPPORTED_ENCODINGS:
            raise ValueError(
                f"Unsupported file encoding: {self.encoding!r}. "
                f"Expected one of {sorted(SUPPORTED_ENCODINGS)}."
            )
        object.__setattr__(self, "encoding", normalized_encoding)

        if not isinstance(self.byte_length, int) or self.byte_length < 0:
            raise ValueError("File byte_length must be a non-negative integer.")

        for field_name, value in (("width", self.width), ("height", self.height)):
            if value is not None and (not isinstance(value, int) or value <= 0):
                raise ValueError(f"File {field_name} must be a positive integer when provided.")

        if normalized_encoding == "raw":
            if self.width is None or self.height is None:
                raise ValueError("Raw image metadata requires width and height.")
            if not isinstance(self.dtype, str) or not self.dtype:
                raise ValueError("Raw image metadata requires dtype.")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-compatible manifest representation."""
        return {
            "data_id": self.data_id,
            "logical_name": self.logical_name,
            "file_type": self.file_type,
            "encoding": self.encoding,
            "byte_length": self.byte_length,
            "width": self.width,
            "height": self.height,
            "dtype": self.dtype,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FileMetadata":
        """Parse and validate one manifest record."""
        if not isinstance(value, Mapping):
            raise ValueError("Manifest entry must be a JSON object.")
        dimensions = value.get("dimensions")
        width = value.get("width")
        height = value.get("height")
        if isinstance(dimensions, Mapping):
            width = dimensions.get("width", width)
            height = dimensions.get("height", height)

        try:
            return cls(
                data_id=value["data_id"],
                logical_name=value["logical_name"],
                file_type=value["file_type"],
                encoding=value["encoding"],
                byte_length=value["byte_length"],
                width=width,
                height=height,
                dtype=value.get("dtype"),
            )
        except KeyError as exc:
            raise ValueError(f"Manifest entry is missing {exc.args[0]!r}.") from exc
        except TypeError as exc:
            raise ValueError("Manifest entry fields have invalid types.") from exc


@dataclass(frozen=True)
class FileBatchManifest:
    """Describe all files that belong to one sensing batch."""

    files: tuple[FileMetadata, ...]
    batch_id: str = "batch-1"

    def __post_init__(self) -> None:
        if not isinstance(self.batch_id, str) or not self.batch_id:
            raise ValueError("Batch ID must be a non-empty string.")
        normalized_files = tuple(self.files)
        if not normalized_files:
            raise ValueError("A file batch manifest requires at least one file.")
        if any(not isinstance(item, FileMetadata) for item in normalized_files):
            raise TypeError("Manifest files must be FileMetadata objects.")
        data_ids = [item.data_id for item in normalized_files]
        if len(data_ids) != len(set(data_ids)):
            raise ValueError("Each file in a batch must have a unique data_id.")
        object.__setattr__(self, "files", normalized_files)

    @property
    def file_count(self) -> int:
        return len(self.files)

    def entry_for(self, data_id: int) -> FileMetadata:
        """Return the entry mapped to a transport data_id."""
        for entry in self.files:
            if entry.data_id == data_id:
                return entry
        raise ValueError(f"Manifest does not contain data_id {data_id}.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": FILE_PAYLOAD_VERSION,
            "batch_id": self.batch_id,
            "file_count": self.file_count,
            "files": [entry.to_dict() for entry in self.files],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FileBatchManifest":
        """Parse and validate a complete batch manifest."""
        if value.get("manifest_version") != FILE_PAYLOAD_VERSION:
            raise ValueError("Unsupported file manifest version.")
        files = value.get("files")
        if not isinstance(files, list):
            raise ValueError("Manifest files must be a list.")
        file_count = value.get("file_count")
        if file_count != len(files):
            raise ValueError("Manifest file_count does not match the file list.")
        try:
            return cls(
                files=tuple(FileMetadata.from_dict(item) for item in files),
                batch_id=value["batch_id"],
            )
        except KeyError as exc:
            raise ValueError(f"Manifest is missing {exc.args[0]!r}.") from exc


def _infer_encoding(file_bytes: bytes) -> str:
    if file_bytes.startswith(PNG_SIGNATURE):
        return "png"
    if file_bytes.startswith(JPEG_START):
        return "jpeg"
    return "raw"


def build_file_metadata(
    *,
    data_id: int,
    logical_name: str,
    file_bytes: bytes,
    file_type: str = "image",
    encoding: Optional[str] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    dtype: Optional[str] = None,
) -> FileMetadata:
    """Build metadata using the actual byte length of ``file_bytes``."""
    if not isinstance(file_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("file_bytes must be bytes-like.")
    data = bytes(file_bytes)
    return FileMetadata(
        data_id=data_id,
        logical_name=logical_name,
        file_type=file_type,
        encoding=_infer_encoding(data) if encoding is None else encoding,
        byte_length=len(data),
        width=width,
        height=height,
        dtype=dtype,
    )


def serialize_manifest(manifest: FileBatchManifest) -> bytes:
    """Serialize a manifest as UTF-8 JSON."""
    if not isinstance(manifest, FileBatchManifest):
        raise TypeError("manifest must be a FileBatchManifest.")
    return json.dumps(
        manifest.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def parse_manifest(raw_manifest: bytes) -> FileBatchManifest:
    """Parse a UTF-8 JSON manifest."""
    try:
        value = json.loads(raw_manifest.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("File manifest is not valid UTF-8 JSON.") from exc
    if not isinstance(value, Mapping):
        raise ValueError("File manifest must be a JSON object.")
    return FileBatchManifest.from_dict(value)


def serialize_file_payload(
    manifest: FileBatchManifest,
    data_id: int,
    file_bytes: bytes,
) -> bytes:
    """Wrap one file's bytes with its application-level batch manifest."""
    if not isinstance(file_bytes, (bytes, bytearray, memoryview)):
        raise TypeError("file_bytes must be bytes-like.")
    data = bytes(file_bytes)
    entry = manifest.entry_for(data_id)
    if len(data) != entry.byte_length:
        raise ValueError(
            f"File length for data_id {data_id} is {len(data)}, "
            f"but the manifest declares {entry.byte_length}."
        )

    raw_manifest = serialize_manifest(manifest)
    try:
        header = _PAYLOAD_HEADER.pack(
            FILE_PAYLOAD_MAGIC,
            FILE_PAYLOAD_VERSION,
            len(raw_manifest),
        )
    except struct.error as exc:
        raise ValueError("File manifest is too large for the application envelope.") from exc
    return header + raw_manifest + data


def is_file_payload(payload: bytes) -> bool:
    """Return whether a SENSOR_DATA payload uses the file envelope."""
    return isinstance(payload, (bytes, bytearray, memoryview)) and bytes(payload).startswith(
        FILE_PAYLOAD_MAGIC
    )


def _validate_accepted_file(metadata: FileMetadata, file_bytes: bytes) -> None:
    if len(file_bytes) != metadata.byte_length:
        raise ValueError(
            f"File length mismatch for data_id {metadata.data_id}: "
            f"expected {metadata.byte_length}, got {len(file_bytes)}."
        )
    if metadata.encoding not in ACCEPTED_ENCODINGS:
        raise ValueError(
            "Cluster Head accepts encoded PNG and JPEG files only; "
            f"received {metadata.encoding!r}."
        )
    if metadata.file_type != "image":
        raise ValueError(
            "Cluster Head accepts image files only; "
            f"received file_type {metadata.file_type!r}."
        )
    if metadata.encoding == "png" and not file_bytes.startswith(PNG_SIGNATURE):
        raise ValueError("File is declared as PNG but has no PNG signature.")
    if metadata.encoding == "jpeg" and not (
        file_bytes.startswith(JPEG_START) and file_bytes.endswith(JPEG_END)
    ):
        raise ValueError("File is declared as JPEG but has invalid JPEG boundaries.")


def parse_file_payload(
    payload: bytes,
    *,
    expected_data_id: Optional[int] = None,
) -> dict[str, Any]:
    """Parse, validate, and unwrap a completed file application payload.

    ``expected_data_id`` is the FLAME transfer identifier from the transport
    fields.  It is checked against the manifest so a file cannot be associated
    with a different transfer merely by changing application metadata.
    """
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("File payload must be bytes-like.")
    raw_payload = bytes(payload)
    if len(raw_payload) < _PAYLOAD_HEADER.size:
        raise ValueError("File payload is shorter than its application header.")

    magic, version, manifest_length = _PAYLOAD_HEADER.unpack_from(raw_payload)
    if magic != FILE_PAYLOAD_MAGIC:
        raise ValueError("Payload is not a FLAME file envelope.")
    if version != FILE_PAYLOAD_VERSION:
        raise ValueError("Unsupported file payload version.")

    manifest_start = _PAYLOAD_HEADER.size
    data_start = manifest_start + manifest_length
    if data_start > len(raw_payload):
        raise ValueError("File payload manifest length exceeds the payload size.")

    manifest = parse_manifest(raw_payload[manifest_start:data_start])
    if expected_data_id is None:
        if manifest.file_count != 1:
            raise ValueError("A file payload without data_id must contain exactly one file.")
        data_id = manifest.files[0].data_id
    else:
        data_id = expected_data_id
    metadata = manifest.entry_for(data_id)
    file_bytes = raw_payload[data_start:]
    _validate_accepted_file(metadata, file_bytes)

    return {
        "manifest": manifest,
        "metadata": metadata,
        "file_bytes": file_bytes,
    }
