"""The MPEG-2 program stream of a DVD title, pack by pack.

vtskit's input is the stream ffmpeg's `dvd` muxer writes (oncemore's
encoder runs it with `-f dvd -muxrate 10080000 -packetsize 2048`): packs of
2048 bytes, each a pack header and PES packets, and a navigation pack at
the start of every VOBU whose PCI and DSI are left empty. This module reads
that: the pack header's clock, the PES packets (stream, PTS, payload),
which packs are navigation packs, and inside the video stream the pictures
(where each starts and ends, whether it is a reference picture, how long it
is shown).

Layouts: the pack and PES headers as described at
https://dvdnav.mplayerhq.hu/dvdinfo/packhdr.html and pes-hdr.html; the
video start codes and headers from the MPEG-2 video standard (ISO/IEC
13818-2: sequence header, sequence extension, picture header, picture
coding extension).
"""

import struct
from dataclasses import dataclass

from .errors import VtsError

SECTOR = 2048
PACK_START = b"\x00\x00\x01\xba"

SYSTEM_HEADER = 0xBB
PRIVATE_1 = 0xBD        # AC-3, DTS, LPCM audio and subpictures, by substream
PADDING = 0xBE
PRIVATE_2 = 0xBF        # navigation data: substream 0 PCI, 1 DSI
MPEG_AUDIO = 0xC0       # .. 0xC7
VIDEO = 0xE0            # the one video stream of a DVD

PCI, DSI = 0, 1         # substreams of private stream 2


@dataclass
class Pes:
    """A PES packet in a pack: `start`..`end` is its payload, after the
    substream id of a private stream."""
    stream: int
    sub: int | None
    pts: int | None
    start: int
    end: int

    def audio_stream(self):
        """The DVD audio stream number (0..7) this packet belongs to, or None."""
        if MPEG_AUDIO <= self.stream <= MPEG_AUDIO + 7:
            return self.stream - MPEG_AUDIO
        if self.stream == PRIVATE_1 and self.sub is not None:
            if 0x80 <= self.sub <= 0x8F or 0xA0 <= self.sub <= 0xA7:   # AC-3, DTS; LPCM
                return self.sub & 7
        return None

    def subpicture_stream(self):
        if self.stream == PRIVATE_1 and self.sub is not None and 0x20 <= self.sub <= 0x3F:
            return self.sub - 0x20
        return None


@dataclass
class Pack:
    scr: int            # system clock reference, 90 kHz
    scr_ext: int        # its 27 MHz remainder
    mux_rate: int       # in 50 bytes/s
    pes: list

    def nav(self):
        """(PCI, DSI) PES packets if this is a navigation pack, else None."""
        pci = dsi = None
        for p in self.pes:
            if p.stream == PRIVATE_2 and p.sub == PCI:
                pci = p
            elif p.stream == PRIVATE_2 and p.sub == DSI:
                dsi = p
        if pci and dsi:
            return pci, dsi
        return None


def _timestamp(b, o):
    """A 33-bit PTS/DTS/SCR base: 3 bits, marker, 15, marker, 15, marker."""
    return (((b[o] >> 1) & 7) << 30) | (b[o + 1] << 22) | ((b[o + 2] >> 1) << 15) \
        | (b[o + 3] << 7) | (b[o + 4] >> 1)


def parse_pack(data, where=""):
    """The pack in these 2048 bytes."""
    if len(data) != SECTOR or data[:4] != PACK_START:
        raise VtsError(f"not a pack{where}")
    if data[4] >> 6 != 1:
        raise VtsError(f"not an MPEG-2 pack header{where}")
    b = data
    scr = (((b[4] >> 3) & 7) << 30) | ((b[4] & 3) << 28) | (b[5] << 20) | ((b[6] >> 3) << 15) \
        | ((b[6] & 3) << 13) | (b[7] << 5) | (b[8] >> 3)
    scr_ext = ((b[8] & 3) << 7) | (b[9] >> 1)
    mux_rate = (b[10] << 14) | (b[11] << 6) | (b[12] >> 2)
    o = 14 + (b[13] & 7)
    pes = []
    while o + 6 <= SECTOR and data[o:o + 3] == b"\x00\x00\x01":
        sid, length = data[o + 3], struct.unpack_from(">H", data, o + 4)[0]
        end = o + 6 + length
        if end > SECTOR:
            raise VtsError(f"a PES packet runs past the pack{where}")
        if sid in (SYSTEM_HEADER, PADDING):
            pass
        elif sid == PRIVATE_2:
            pes.append(Pes(sid, data[o + 6], None, o + 7, end))
        else:
            if data[o + 6] >> 6 != 2:
                raise VtsError(f"not an MPEG-2 PES header (stream {sid:#x}){where}")
            flags, hdl = data[o + 7], data[o + 8]
            start = o + 9 + hdl
            pts = _timestamp(data, o + 9) if flags & 0x80 else None
            sub = None
            if sid == PRIVATE_1 and start < end:
                sub = data[start]
                start += 1
            pes.append(Pes(sid, sub, pts, start, end))
        o = end
    return Pack(scr, scr_ext, mux_rate, pes)


def packs(path):
    """Every pack of the file: (sector index, 2048 bytes)."""
    with open(path, "rb") as f:
        n = 0
        while True:
            chunk = f.read(SECTOR * 512)
            if not chunk:
                return
            if len(chunk) % SECTOR:
                raise VtsError(f"{path}: the size is not a whole number of sectors")
            for o in range(0, len(chunk), SECTOR):
                yield n, chunk[o:o + SECTOR]
                n += 1


# --- the video elementary stream ---------------------------------------

PICTURE_START, SEQUENCE_HEADER, EXTENSION, SEQUENCE_END, GOP_HEADER = 0x00, 0xB3, 0xB5, 0xB7, 0xB8
SEQUENCE_EXTENSION, PICTURE_CODING_EXTENSION = 1, 8
I_PICTURE, P_PICTURE, B_PICTURE = 1, 2, 3

# frame_rate_code of the sequence header -> frame period in 90 kHz ticks;
# the three rates DVD-Video allows (23.976 is shown at 29.97 with pulldown)
FRAME_PERIOD = {1: 90000 * 1001 // 24000, 3: 90000 // 25, 4: 90000 * 1001 // 30000}


@dataclass
class Picture:
    start: int      # offset in the elementary stream
    end: int        # just past its last byte
    coding: int     # I_PICTURE, P_PICTURE or B_PICTURE
    fields: int     # how long it is shown, in fields (2 = one frame)

    @property
    def reference(self):
        return self.coding in (I_PICTURE, P_PICTURE)


def pictures(es, progressive_sequence=False):
    """The pictures in this piece of video elementary stream, in coding
    order, and the frame_rate_code of its last sequence header (None if it
    has none), and the progressive_sequence flag to carry to the next piece.
    A picture runs to the next picture, GOP or sequence start code."""
    out = []
    frame_rate_code = None
    current = None
    p = 0
    n = len(es)
    while True:
        p = es.find(b"\x00\x00\x01", p)
        if p < 0 or p + 4 > n:
            break
        code = es[p + 3]
        if code == PICTURE_START or code in (SEQUENCE_HEADER, GOP_HEADER, SEQUENCE_END):
            if current:
                current.end = p
                out.append(current)
                current = None
        if code == PICTURE_START:
            if p + 6 > n:
                raise VtsError("a picture header is cut short")
            current = Picture(p, n, (es[p + 5] >> 3) & 7, 2)
            if current.coding not in (I_PICTURE, P_PICTURE, B_PICTURE):
                raise VtsError(f"picture coding type {current.coding} is not I, P or B")
        elif code == SEQUENCE_HEADER:
            if p + 8 > n:
                raise VtsError("a sequence header is cut short")
            frame_rate_code = es[p + 7] & 0x0F
        elif code == EXTENSION and p + 8 <= n:
            kind = es[p + 4] >> 4
            if kind == SEQUENCE_EXTENSION:
                progressive_sequence = bool(es[p + 5] & 0x08)
            elif kind == PICTURE_CODING_EXTENSION and current:
                structure = es[p + 6] & 3                 # 1, 2: a field; 3: a frame
                top_field_first = es[p + 7] >> 7
                repeat_first_field = (es[p + 7] >> 1) & 1
                if structure != 3:
                    current.fields = 1
                elif progressive_sequence:
                    current.fields = 2 * (1 + repeat_first_field * (1 + top_field_first))
                else:
                    current.fields = 2 + repeat_first_field
        p += 3
    if current:
        out.append(current)
    return out, frame_rate_code, progressive_sequence
