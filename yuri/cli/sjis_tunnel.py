"""Decode-only codec for text written by VNTextPatch: Shift-JIS tunnel and YU-RIS control codes.

VNTextPatch stores characters that Shift-JIS cannot represent as a "tunnel" pair:
a lead byte (0x81-0x9F, 0xE0-0xFC) followed by a trail byte below 0x40. The pair is
an index into the table saved next to the patched files (``sjis_ext.bin``): UTF-16LE
characters, in the order they were first used. Real Shift-JIS trail bytes are always
0x40 or above, so the two kinds of pairs never collide.

YU-RIS also embeds control codes in text as ``0xEF`` plus a second byte. They are not valid
Shift-JIS, so they are decoded to the same standard forms VNTextPatch uses: ``0xEF 0xF0`` is a
line break (``\\r\\n``), ``0xEF 0xF2`` a page break (``\\p``), ``0xEF 0xF3`` a wait for click
(``\\c``) and ``0xEF 0xF5`` an unknown code (``\\u``). Any other ``0xEF`` code is an error that
names it.

``register`` makes the codec available to ``bytes.decode`` / ``str(data, name)``, so the
core can use it through its normal ``ienc`` / ``word_enc`` parameters.
"""
from __future__ import annotations

import codecs

CODEC_NAME = 'sjis_tunnel'
_LOW_SKIPPED = (9, 10, 13, 32, 44)  # \t \n \r ' ' ',' are never used as tunnel trail bytes
_TABLE_SIZE = 0x3B * 0x3A           # same limit as SjisTunnelEncoding
_CONTROL_LEAD = 0xEF
_CONTROL = {0xF0: '\r\n', 0xF2: '\\p', 0xF3: '\\c', 0xF5: '\\u'}  # second byte -> standard form


def _is_lead(b: int) -> bool:
    return 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC


def _build_inverse() -> dict[tuple[int, int], int]:
    """(lead, trail) -> table index, the inverse of SjisTunnelEncoding.GetSjisTunnelChar."""
    inverse = {}
    for index in range(_TABLE_SIZE):
        hi_idx, lo_idx = divmod(index, 0x3A)
        hi = 0x81 + hi_idx if hi_idx < 0x1F else 0xE0 + (hi_idx - 0x1F)
        lo = 1 + lo_idx
        for skipped in _LOW_SKIPPED:
            if lo >= skipped:
                lo += 1
        inverse[(hi, lo)] = index
    return inverse


_INVERSE = _build_inverse()


def load_table(file_path: str) -> str:
    with open(file_path, 'rb') as fp:
        data = fp.read()
    if len(data) % 2:  # an empty file is valid: no character was tunneled
        raise ValueError(f'{file_path!r} is not a valid sjis_ext.bin (expected UTF-16LE characters)')
    return data.decode('utf-16-le')


def _decode_run(raw: bytes, start: int, end: int, errors: str) -> str:
    """Decode an ordinary Shift-JIS run, reporting error positions within the whole text."""
    try:
        return raw[start:end].decode('cp932', errors)
    except UnicodeDecodeError as e:
        raise UnicodeDecodeError(CODEC_NAME, raw, start + e.start, start + e.end, e.reason) from None


def _make_decoder(table: str):
    def decode(data, errors: str = 'strict'):
        raw = bytes(data)
        out: list[str] = []
        seg = i = 0  # [seg, i) is a run of ordinary Shift-JIS, decoded in one go
        while i < len(raw):
            if not _is_lead(raw[i]):
                i += 1
            elif raw[i] == _CONTROL_LEAD and i + 1 < len(raw) and raw[i + 1] >= 0x40:
                out.append(_decode_run(raw, seg, i, errors))
                if raw[i + 1] in _CONTROL:
                    out.append(_CONTROL[raw[i + 1]])
                elif errors == 'strict':
                    raise UnicodeDecodeError(CODEC_NAME, raw, i, i + 2,
                                             f'unknown YU-RIS control code {raw[i]:02X} {raw[i + 1]:02X}')
                else:
                    out.append('\ufffd')
                i += 2
                seg = i
            elif i + 1 < len(raw) and raw[i + 1] < 0x40:
                out.append(_decode_run(raw, seg, i, errors))
                index = _INVERSE.get((raw[i], raw[i + 1]))
                if index is None or index >= len(table):
                    if errors == 'strict':
                        raise UnicodeDecodeError(CODEC_NAME, raw, i, i + 2,
                                                 f'tunnel code {raw[i]:02X}{raw[i + 1]:02X} '
                                                 f'is not in the table ({len(table)} characters)')
                    out.append('\ufffd')
                else:
                    out.append(table[index])
                i += 2
                seg = i
            else:
                i += 2
        out.append(_decode_run(raw, seg, len(raw), errors))
        return ''.join(out), len(raw)
    return decode


def _no_encode(text, errors='strict'):
    raise NotImplementedError(f'{CODEC_NAME} is decode-only')


_search = None  # the currently registered search function


def register(table: str) -> str:
    """Register the codec for this table and return its name (usable as an encoding name).

    Calling it again replaces the previous table.
    """
    global _search
    info = codecs.CodecInfo(_no_encode, _make_decoder(table), name=CODEC_NAME)
    if _search is not None:
        codecs.unregister(_search)
    _search = lambda name: info if name == CODEC_NAME else None
    codecs.register(_search)
    return CODEC_NAME
