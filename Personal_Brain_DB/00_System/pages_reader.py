#!/usr/bin/env python3
"""
Memosyne — Apple Pages (.pages) text extraction.

A modern .pages file is a zip; the document lives in Index/Document.iwa.
IWA is NOT a plain snappy stream: it is a sequence of chunks

    [1 byte type = 0x00][3-byte little-endian length][raw snappy block]

where each block is raw snappy (no stream identifier, no CRC framing) and is
decompressed independently. The decompressed bytes are a run of archives:

    [varint n][ArchiveInfo (n bytes)][payload 1][payload 2]...

ArchiveInfo.message_infos (field 2) gives each payload's type (field 1) and
length (field 3). The document body is the TSWP.StorageArchive (type 2001)
whose kind (field 1) is BODY (0); its text is field 3.

If that layout is not recognised, extraction falls back to the longest
length-delimited UTF-8 string with ≥4 CJK characters, and says so on stderr.

This module has no native dependency: the raw-snappy decoder is pure Python
(.pages bodies are a few KB, so speed is irrelevant).

Every failure raises PagesExtractionError — callers must surface it
("The Oracle faltered") rather than ingest a fragment of compressed bytes.

CLI (prints the extracted body; handy for spot checks):
    python3 00_System/pages_reader.py path/to/note.pages
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path
from typing import Iterator, Optional

DOCUMENT_IWA         = "Index/Document.iwa"
TSWP_STORAGE_ARCHIVE = 2001     # TSWP.StorageArchive message type id
STORAGE_KIND_BODY    = 0        # TSWP.StorageArchive.KindType.BODY
MIN_CJK              = 4        # fallback heuristic: minimum CJK chars

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# Pages-internal break / placeholder characters inside TSWP text:
#   U+2028 line break, U+2029 paragraph break, U+0004/U+0005 section break,
#   U+000C page break, U+000E column break → newline
#   U+FFFC inline attachment (image, shape) → dropped
_TSWP_TRANSLATE = str.maketrans({
    "\u2028": "\n", "\u2029": "\n",
    "\x04": "\n", "\x05": "\n", "\x0c": "\n", "\x0e": "\n",
    "\ufffc": None,
})
# C0 controls that never occur in TSWP text. In the fallback scan they mark
# a misaligned slice of protobuf framing (tag / length bytes), not prose.
_NON_TEXT_CONTROL = re.compile(r"[\x00-\x03\x06-\x08\x0b\x0d\x0f-\x1f\x7f]")


class PagesExtractionError(RuntimeError):
    """The .pages file could not be read, decompressed, or yielded no body."""


# ─── Raw snappy ──────────────────────────────────────────────

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a little-endian base-128 varint; return (value, next_pos)."""
    value = shift = 0
    while True:
        if pos >= len(buf):
            raise PagesExtractionError("truncated varint")
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        if b < 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise PagesExtractionError("varint too long")


def snappy_uncompress(src: bytes) -> bytes:
    """
    Decompress one raw snappy block (format_description.txt, no framing).

    Equivalent to python-snappy's snappy.uncompress / cramjam's
    snappy.decompress_raw.
    """
    expected, pos = _read_varint(src, 0)
    out = bytearray()
    n = len(src)
    while pos < n:
        tag = src[pos]
        pos += 1
        kind = tag & 0x03

        if kind == 0:                                     # literal
            length = tag >> 2
            if length >= 60:
                extra = length - 59
                if pos + extra > n:
                    raise PagesExtractionError("truncated literal length")
                length = int.from_bytes(src[pos:pos + extra], "little")
                pos += extra
            length += 1
            if pos + length > n:
                raise PagesExtractionError("truncated literal")
            out += src[pos:pos + length]
            pos += length
            continue

        if kind == 1:                                     # copy, 1-byte offset
            if pos + 1 > n:
                raise PagesExtractionError("truncated copy")
            length = 4 + ((tag >> 2) & 0x07)
            offset = ((tag >> 5) << 8) | src[pos]
            pos += 1
        elif kind == 2:                                   # copy, 2-byte offset
            if pos + 2 > n:
                raise PagesExtractionError("truncated copy")
            length = (tag >> 2) + 1
            offset = int.from_bytes(src[pos:pos + 2], "little")
            pos += 2
        else:                                             # copy, 4-byte offset
            if pos + 4 > n:
                raise PagesExtractionError("truncated copy")
            length = (tag >> 2) + 1
            offset = int.from_bytes(src[pos:pos + 4], "little")
            pos += 4

        if offset == 0 or offset > len(out):
            raise PagesExtractionError(f"invalid copy offset {offset}")
        start = len(out) - offset
        if offset >= length:
            out += out[start:start + length]
        else:                                             # overlapping run
            for i in range(length):
                out.append(out[start + i])

    if len(out) != expected:
        raise PagesExtractionError(
            f"snappy length mismatch: got {len(out)}, header says {expected}")
    return bytes(out)


# ─── IWA ─────────────────────────────────────────────────────

def iwa_decompress(data: bytes) -> bytes:
    """Split an .iwa stream into chunks and decompress each one."""
    out = bytearray()
    pos = 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise PagesExtractionError("truncated IWA chunk header")
        chunk_type = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 4], "little")
        pos += 4
        if chunk_type != 0:
            raise PagesExtractionError(f"unknown IWA chunk type 0x{chunk_type:02x}")
        if pos + length > len(data):
            raise PagesExtractionError("truncated IWA chunk")
        out += snappy_uncompress(data[pos:pos + length])
        pos += length
    if not out:
        raise PagesExtractionError("empty IWA stream")
    return bytes(out)


def _pb_fields(buf: bytes) -> Iterator[tuple[int, int, object]]:
    """Yield (field_number, wire_type, value) for each field of a protobuf message."""
    pos, n = 0, len(buf)
    while pos < n:
        key, pos = _read_varint(buf, pos)
        field, wire = key >> 3, key & 0x07
        if wire == 0:
            value, pos = _read_varint(buf, pos)
        elif wire == 1:
            value, pos = buf[pos:pos + 8], pos + 8
        elif wire == 2:
            length, pos = _read_varint(buf, pos)
            value, pos = buf[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = buf[pos:pos + 4], pos + 4
        else:
            raise PagesExtractionError(f"unsupported protobuf wire type {wire}")
        if pos > n:
            raise PagesExtractionError("truncated protobuf field")
        yield field, wire, value


def iwa_messages(raw: bytes) -> Iterator[tuple[int, bytes]]:
    """Yield (type_id, payload) for every object in a decompressed IWA stream."""
    pos, n = 0, len(raw)
    while pos < n:
        info_len, pos = _read_varint(raw, pos)
        info, pos = raw[pos:pos + info_len], pos + info_len
        if pos > n:
            raise PagesExtractionError("truncated ArchiveInfo")
        for field, wire, value in _pb_fields(info):
            if field != 2 or wire != 2:                   # message_infos
                continue
            meta = {f: v for f, w, v in _pb_fields(value) if w == 0 and f in (1, 3)}
            if 1 not in meta or 3 not in meta:
                raise PagesExtractionError("MessageInfo lacks type or length")
            end = pos + meta[3]
            if end > n:
                raise PagesExtractionError("truncated IWA object")
            yield meta[1], raw[pos:end]
            pos = end


def body_storage_text(raw: bytes) -> Optional[str]:
    """
    Text of the BODY TSWP.StorageArchive, or None if the stream has none.

    Header / footer / text-box storages are ignored even when longer.
    """
    parts: list[str] = []
    for type_id, payload in iwa_messages(raw):
        if type_id != TSWP_STORAGE_ARCHIVE:
            continue
        kind, texts = None, []
        for field, wire, value in _pb_fields(payload):
            if field == 1 and wire == 0:
                kind = value
            elif field == 3 and wire == 2:
                try:
                    texts.append(value.decode("utf-8"))
                except UnicodeDecodeError as e:
                    raise PagesExtractionError(f"body text is not UTF-8: {e}") from None
        if kind == STORAGE_KIND_BODY:
            parts.append("".join(texts))
    return "\n".join(parts) if parts else None


def longest_cjk_string(raw: bytes, min_cjk: int = MIN_CJK) -> str:
    """
    Fallback: the longest protobuf length-delimited field that decodes as
    strict UTF-8, carries no protobuf framing bytes, and contains at least
    `min_cjk` CJK characters.

    Using the field's own length prefix (rather than a byte-class regex)
    gives exact boundaries: newlines, full-width punctuation (「」，。) and
    emoji are kept, and no stray varint bytes leak into the text.
    """
    best = ""
    n = len(raw)
    for i in range(n - 1):
        if raw[i] & 0x07 != 2:                 # wire type 2 = length-delimited
            continue
        try:
            length, start = _read_varint(raw, i + 1)
        except PagesExtractionError:
            continue
        if length <= len(best) or start + length > n:
            continue
        try:
            s = raw[start:start + length].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if (len(s) > len(best)
                and not _NON_TEXT_CONTROL.search(s)
                and len(_CJK.findall(s)) >= min_cjk):
            best = s
    return best


def _normalize(text: str) -> str:
    text = text.translate(_TSWP_TRANSLATE)
    text = _NON_TEXT_CONTROL.sub("", text)
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


def extract_pages_text(path: Path) -> str:
    """
    Extract the body text of a .pages document.

    Raises PagesExtractionError on any failure — never returns a partial
    or empty string silently.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            data = z.read(DOCUMENT_IWA)
    except KeyError:
        raise PagesExtractionError(f"{DOCUMENT_IWA} not found in archive") from None
    except (zipfile.BadZipFile, OSError) as e:
        if path.is_dir():
            raise PagesExtractionError(
                "legacy package-format .pages (directory) is not supported; "
                "re-save it in a recent version of Pages") from None
        raise PagesExtractionError(f"cannot open archive: {e}") from e

    raw = iwa_decompress(data)
    try:
        body = body_storage_text(raw)
        reason = "no BODY text storage"
    except PagesExtractionError as e:
        body, reason = None, str(e)

    if body is None:
        body = longest_cjk_string(raw)
        if not body:
            raise PagesExtractionError(
                f"no body text found ({reason}; no string with ≥{MIN_CJK} CJK characters)")
        print(f"  ⚠  {path.name}: unrecognised IWA layout ({reason}); "
              f"used the longest-CJK-string fallback", file=sys.stderr)

    text = _normalize(body)
    if not text:
        raise PagesExtractionError("document body is empty")
    return text


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: pages_reader.py <file.pages>", file=sys.stderr)
        sys.exit(2)
    try:
        print(extract_pages_text(Path(sys.argv[1])))
    except PagesExtractionError as e:
        print(f"The Oracle faltered for: {sys.argv[1]} — {e}", file=sys.stderr)
        sys.exit(1)
