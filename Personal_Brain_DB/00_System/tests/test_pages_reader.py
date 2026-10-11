"""Unit tests for .pages extraction (pages_reader.py).

執行：
    cd Personal_Brain_DB/00_System
    python3 -m pytest tests/test_pages_reader.py -v

或不裝 pytest 直接跑：
    python3 -m unittest tests.test_pages_reader

All fixtures are synthetic: tiny .pages zips are built in a temp dir from
hand-encoded protobuf archives, raw-snappy blocks and IWA chunk framing.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pages_reader as pr  # noqa: E402
from pages_reader import PagesExtractionError  # noqa: E402

try:
    import snappy  # python-snappy, only used to cross-check the decoder
    HAS_SNAPPY = True
except ImportError:
    HAS_SNAPPY = False


# ─── protobuf / snappy / IWA fixture builders ────────────────

def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _pb_varint(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _pb_bytes(field: int, raw: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(raw)) + raw


def _pb_string(field: int, text: str) -> bytes:
    return _pb_bytes(field, text.encode("utf-8"))


def _archive(identifier: int, type_id: int, payload: bytes) -> bytes:
    """One IWA archive: varint(len(info)) + ArchiveInfo + payload."""
    msg_info = _pb_varint(1, type_id) + _pb_bytes(2, b"\x01\x00\x05") + _pb_varint(3, len(payload))
    info = _pb_varint(1, identifier) + _pb_bytes(2, msg_info)
    return _varint(len(info)) + info + payload


def _storage(kind: int, text: str | None) -> bytes:
    """TSWP.StorageArchive: kind=1, style_sheet ref=2, text=3."""
    out = _pb_varint(1, kind) + _pb_bytes(2, _pb_varint(1, 42))
    if text is not None:
        out += _pb_string(3, text)
    return out


def _literal(data: bytes) -> bytes:
    """Encode `data` as one snappy literal element."""
    n = len(data) - 1
    if n < 60:
        return bytes([n << 2]) + data
    if n < 1 << 8:
        return bytes([60 << 2]) + n.to_bytes(1, "little") + data
    if n < 1 << 16:
        return bytes([61 << 2]) + n.to_bytes(2, "little") + data
    return bytes([62 << 2]) + n.to_bytes(3, "little") + data


def _snappy_literal_block(data: bytes) -> bytes:
    """Valid raw-snappy block made only of literals (no compression)."""
    out = bytearray(_varint(len(data)))
    for i in range(0, len(data), 1000):
        out += _literal(data[i:i + 1000])
    return bytes(out)


def _iwa(*payloads: bytes) -> bytes:
    """Wrap each payload as its own IWA chunk: [0x00][len:3 LE][raw snappy]."""
    out = bytearray()
    for p in payloads:
        block = _snappy_literal_block(p)
        out += b"\x00" + len(block).to_bytes(3, "little") + block
    return bytes(out)


# Locale data every real document carries: each month is its own short
# string, so the enclosing message decodes as UTF-8 with many CJK chars
# interleaved with tag/length bytes — exactly what must never become a body.
_LOCALE = (
    _pb_string(2, "gregorian") + _pb_string(3, "latn")
    + b"".join(_pb_string(4, f"{m}月") for m in range(1, 13))
)

BODY = (
    "第一段：今天去了「範例咖啡館」，點了一杯拿鐵。\n"
    "第二段，天氣很好！還有 ASCII words 與數字 2026。 換行後繼續？\n"
    "￼圖片佔位符應被移除。\x05新的一節：結束。"
)
EXPECTED = (
    "第一段：今天去了「範例咖啡館」，點了一杯拿鐵。\n"
    "第二段，天氣很好！還有 ASCII words 與數字 2026。\n換行後繼續？\n"
    "圖片佔位符應被移除。\n新的一節：結束。"
)


def _document(body: str | None, header: str | None = None) -> bytes:
    """Decompressed Document.iwa: document root, locale, header + body storages."""
    return (
        _archive(1, 10000, _pb_varint(1, 7) + _pb_bytes(5, _LOCALE))
        + _archive(2, 2001, _storage(1, header))
        + _archive(3, 2001, _storage(0, body))
        + _archive(4, 2001, _storage(1, None))
    )


class _TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def make_pages(self, iwa: bytes, name: str = "note.pages",
                   member: str = "Index/Document.iwa") -> Path:
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            z.writestr(member, iwa)
            z.writestr("Metadata/Properties.plist", b"<plist/>")
        return p


# ─── snappy decoder ──────────────────────────────────────────

class TestSnappyUncompress(unittest.TestCase):

    def test_literal_only(self):
        data = "記憶女神 Mnemosyne。".encode() * 30
        self.assertEqual(pr.snappy_uncompress(_snappy_literal_block(data)), data)

    def test_long_literal_length_bytes(self):
        data = bytes(range(256)) * 3          # 768 B → 2-byte literal length
        block = _varint(len(data)) + _literal(data)
        self.assertEqual(pr.snappy_uncompress(block), data)

    def test_copy_1byte_offset_overlapping(self):
        # literal "ab" + copy(len=8, offset=2) → run-length expansion
        block = _varint(10) + _literal(b"ab") + bytes([0b000_100_01, 2])
        self.assertEqual(pr.snappy_uncompress(block), b"ab" * 5)

    def test_copy_2byte_offset(self):
        # literal "hello" + copy(len=5, offset=5)
        block = _varint(10) + _literal(b"hello") + bytes([(4 << 2) | 2]) + (5).to_bytes(2, "little")
        self.assertEqual(pr.snappy_uncompress(block), b"hellohello")

    def test_copy_4byte_offset(self):
        block = _varint(8) + _literal(b"wxyz") + bytes([(3 << 2) | 3]) + (4).to_bytes(4, "little")
        self.assertEqual(pr.snappy_uncompress(block), b"wxyzwxyz")

    def test_bad_offset_raises(self):
        block = _varint(8) + _literal(b"ab") + bytes([0b000_010_01, 9])
        with self.assertRaises(PagesExtractionError):
            pr.snappy_uncompress(block)

    def test_length_mismatch_raises(self):
        block = _varint(99) + _literal(b"short")
        with self.assertRaises(PagesExtractionError):
            pr.snappy_uncompress(block)

    def test_truncated_literal_raises(self):
        block = _varint(10) + bytes([9 << 2]) + b"abc"
        with self.assertRaises(PagesExtractionError):
            pr.snappy_uncompress(block)

    @unittest.skipUnless(HAS_SNAPPY, "python-snappy not installed")
    def test_matches_python_snappy(self):
        data = _document(BODY * 20) + bytes(range(256)) * 4
        self.assertEqual(pr.snappy_uncompress(snappy.compress(data)), data)


# ─── IWA framing + archive layout ────────────────────────────

class TestIwa(unittest.TestCase):

    def test_multiple_chunks_concatenate(self):
        self.assertEqual(pr.iwa_decompress(_iwa(b"first|", b"second")), b"first|second")

    def test_unknown_chunk_type_raises(self):
        bad = bytearray(_iwa(b"payload"))
        bad[0] = 0x01
        with self.assertRaises(PagesExtractionError):
            pr.iwa_decompress(bytes(bad))

    def test_truncated_chunk_raises(self):
        with self.assertRaises(PagesExtractionError):
            pr.iwa_decompress(_iwa(b"payload")[:-2])

    def test_whole_file_is_not_a_snappy_block(self):
        """Regression: the old code called snappy.decompress on the whole file."""
        with self.assertRaises(PagesExtractionError):
            pr.snappy_uncompress(_iwa(_document(BODY)))

    def test_messages_walk(self):
        types = [t for t, _ in pr.iwa_messages(_document(BODY))]
        self.assertEqual(types, [10000, 2001, 2001, 2001])

    def test_body_storage_ignores_longer_header(self):
        raw = _document("短短的正文內容。", header="頁首文字比正文還要長很多很多很多很多很多。")
        self.assertEqual(pr.body_storage_text(raw), "短短的正文內容。")

    def test_body_storage_none_without_body(self):
        raw = _archive(1, 2001, _storage(3, "文字方塊裡的內容。"))
        self.assertIsNone(pr.body_storage_text(raw))


# ─── fallback heuristic ──────────────────────────────────────

class TestLongestCjkString(unittest.TestCase):

    def test_picks_longest_cjk_string(self):
        raw = _pb_string(3, "四個中文") + _pb_string(3, BODY) + _pb_string(3, "五個中文字")
        self.assertEqual(pr.longest_cjk_string(raw), BODY)

    def test_no_varint_bytes_leak_into_text(self):
        # 8320 B → varint 0x80 0x41; 0x41 is 'A', which a byte-class regex
        # would glue onto the front of the body.
        body = ("記" * 2773) + "x"
        self.assertEqual(len(body.encode()), 8320)
        self.assertEqual(pr.longest_cjk_string(b"\x08\x01" + _pb_string(3, body)), body)

    def test_rejects_protobuf_framing(self):
        """A misaligned slice of locale data is not a body."""
        raw = _pb_bytes(7, b"\x08\x01" + _LOCALE)
        self.assertEqual(pr.longest_cjk_string(raw), "")

    def test_requires_min_cjk(self):
        raw = _pb_string(3, "only ascii text, quite long but no CJK at all") + _pb_string(4, "三個字")
        self.assertEqual(pr.longest_cjk_string(raw), "")


# ─── end to end ──────────────────────────────────────────────

class TestExtractPagesText(_TmpDirCase):

    def test_extracts_body_with_newlines_and_fullwidth_punctuation(self):
        p = self.make_pages(_iwa(_document(BODY)))
        text = pr.extract_pages_text(p)
        self.assertEqual(text, EXPECTED)
        for ch in "「」，。！？：":
            self.assertIn(ch, text)

    def test_english_only_body(self):
        p = self.make_pages(_iwa(_document("Plain English journal entry.\nSecond line.")))
        self.assertEqual(pr.extract_pages_text(p), "Plain English journal entry.\nSecond line.")

    def test_body_split_across_chunks(self):
        payload = _document(BODY)
        mid = len(payload) // 2
        p = self.make_pages(_iwa(payload[:mid], payload[mid:]))
        self.assertEqual(pr.extract_pages_text(p), EXPECTED)

    def test_empty_body_raises_instead_of_picking_locale(self):
        for body in (None, "", "\n￼\n"):
            with self.subTest(body=body):
                p = self.make_pages(_iwa(_document(body)))
                with self.assertRaisesRegex(PagesExtractionError, "empty"):
                    pr.extract_pages_text(p)

    def test_unrecognised_layout_falls_back_loudly(self):
        p = self.make_pages(_iwa(b"\xff\xff\xff\xff\x0f" + _pb_bytes(5, _LOCALE) + _pb_string(3, BODY)))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            text = pr.extract_pages_text(p)
        self.assertEqual(text, EXPECTED)
        self.assertIn("fallback", err.getvalue())

    def test_unrecognised_layout_without_body_raises(self):
        p = self.make_pages(_iwa(b"\xff\xff\xff\xff\x0f" + _pb_bytes(5, _LOCALE)))
        with self.assertRaisesRegex(PagesExtractionError, "no body"):
            pr.extract_pages_text(p)

    def test_missing_document_iwa_raises(self):
        p = self.make_pages(b"", member="Index/Other.iwa")
        with self.assertRaisesRegex(PagesExtractionError, "Document.iwa"):
            pr.extract_pages_text(p)

    def test_not_a_zip_raises(self):
        p = self.tmp / "broken.pages"
        p.write_bytes(b"not a zip")
        with self.assertRaises(PagesExtractionError):
            pr.extract_pages_text(p)

    def test_package_directory_raises(self):
        p = self.tmp / "legacy.pages"
        (p / "Index").mkdir(parents=True)
        with self.assertRaisesRegex(PagesExtractionError, "legacy"):
            pr.extract_pages_text(p)


# ─── callers ─────────────────────────────────────────────────

class TestIngestRoutePages(_TmpDirCase):

    def setUp(self):
        super().setUp()
        import ingest
        self.ingest = ingest
        self._orig = ingest.JOURNAL_DST
        ingest.JOURNAL_DST = self.tmp / "30_Journal"

    def tearDown(self):
        self.ingest.JOURNAL_DST = self._orig
        super().tearDown()

    def test_inserts_clean_body(self):
        p = self.make_pages(_iwa(_document(BODY)), name="260101.pages")
        with contextlib.redirect_stdout(io.StringIO()):
            r = self.ingest.route_pages(p, dry_run=False)
        self.assertEqual(r.action, "inserted")
        md = (self.tmp / "30_Journal" / "2026" / "260101.md").read_text(encoding="utf-8")
        self.assertIn(EXPECTED, md)
        self.assertIn('source: "pages"', md)

    def test_corrupt_file_falters_without_writing(self):
        p = self.make_pages(b"\x00\xff\xff\xffgarbage", name="260102.pages")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            r = self.ingest.route_pages(p, dry_run=False)
        self.assertEqual(r.action, "faltered")
        self.assertFalse(r.should_archive)
        self.assertFalse(r.needs_followup)
        self.assertIn("The Oracle faltered", err.getvalue())
        self.assertFalse((self.tmp / "30_Journal").exists())


class TestProcessFilesSharesReader(unittest.TestCase):

    def test_same_function(self):
        import process_files
        self.assertIs(process_files.extract_pages_text, pr.extract_pages_text)


if __name__ == "__main__":
    unittest.main()
