"""Subpicture units: the button overlay of a menu, or a subtitle, as a DVD
subpicture.

A subpicture unit (https://dvdnav.mplayerhq.hu/dvdinfo/spu.html) is a
header, the run-length coded pixels of the top and bottom fields, and
display control sequences: the first shows the picture (its colours from
the program chain's palette, a contrast each), a second may take it off
the screen after a delay. Pixels have four codes: 0 background, 1 pattern,
2 and 3 emphasis. A menu's overlay here is drawn with code 1 where a button
lights up; the unit shows nothing by itself (every code transparent) and
the player colours a button's pixels from the PCI's colour table when it is
selected or activated (vtskit.menu). A subtitle (vtskit.subtitles) is a
picture placed at a point of the screen, shown for a while. The unit is
carried in one or more packs of private stream 1, sub-stream 0x20 + n.
"""

import struct

from . import ps
from .errors import VtsError

MAX_UNIT = 53220          # the subpicture buffer


class Overlay:
    """A picture of pixel codes (bytes, one a pixel, row by row)."""

    def __init__(self, width, height, pixels):
        if len(pixels) != width * height:
            raise VtsError(f"{len(pixels)} pixels for {width}x{height}")
        if any(p > 3 for p in pixels):
            raise VtsError("a subpicture pixel is 0 to 3")
        self.width, self.height, self.pixels = width, height, bytes(pixels)

    @classmethod
    def from_rects(cls, width, height, rects, code=1):
        """Code `code` inside each (x0, y0, x1, y1) (x1, y1 exclusive), 0 elsewhere."""
        rows = []
        for y in range(height):
            row = bytearray(width)
            for x0, y0, x1, y1 in rects:
                if y0 <= y < y1:
                    row[x0:x1] = bytes([code]) * (x1 - x0)
            rows.append(bytes(row))
        return cls(width, height, b"".join(rows))

    def bounds(self):
        """(x0, y0, x1, y1) around the non-zero pixels, inclusive; None if blank."""
        xs, ys = [], []
        for y in range(self.height):
            row = self.pixels[y * self.width:(y + 1) * self.width]
            if any(row):
                ys.append(y)
                xs.append(next(x for x, p in enumerate(row) if p))
                xs.append(self.width - 1 - next(x for x, p in enumerate(reversed(row)) if p))
        if not ys:
            return None
        return min(xs), ys[0], max(xs), ys[-1]


class _Bits:
    def __init__(self):
        self.out = bytearray()
        self.acc, self.n = 0, 0

    def put(self, value, bits):
        self.acc = (self.acc << bits) | value
        self.n += bits
        while self.n >= 8:
            self.n -= 8
            self.out.append((self.acc >> self.n) & 0xFF)
        self.acc &= (1 << self.n) - 1

    def align(self):
        if self.n:
            self.put(0, 8 - self.n)


def _encode_line(bits, line):
    """One line of pixel codes as runs: 4, 8, 12 or 16 bits a run, the
    shortest that holds the count; a run to the end of the line that needs
    16 bits (over 63) is count 0 in 16 bits (as spumux chooses)."""
    x = 0
    n = len(line)
    while x < n:
        code = line[x]
        run = 1
        while x + run < n and line[x + run] == code:
            run += 1
        if x + run == n and run > 63:
            bits.put(code, 16)                   # count 0, to the end of the line
        elif run <= 3:
            bits.put(run << 2 | code, 4)
        elif run <= 15:
            bits.put(run << 2 | code, 8)
        elif run <= 63:
            bits.put(run << 2 | code, 12)
        elif run <= 255:
            bits.put(run << 2 | code, 16)
        else:
            bits.put(255 << 2 | code, 16)
            run = 255
        x += run
    bits.align()


def encode(overlay, forced=True, colours=(0, 0, 0, 0), contrasts=(0, 0, 0, 0), at=None,
           stop=None):
    """The subpicture unit of an overlay: its pixels inside their bounds
    (or the whole picture with its top left corner `at` (x, y)), shown
    from its PTS on (forced: shown even with subtitles off), the four
    codes drawn with these palette entries and contrasts (0 transparent to
    15 opaque; all transparent by default, for a button overlay), taken off
    the screen `stop` ticks (90 kHz) after the PTS if given."""
    for name, values in (("colours", colours), ("contrasts", contrasts)):
        if len(values) != 4 or not all(0 <= v <= 15 for v in values):
            raise VtsError(f"the four {name} are 0 to 15")
    if at is None:
        box = overlay.bounds()
        if box is None:
            raise VtsError("the overlay is blank")
        lx0, ly0, lx1, ly1 = box                     # in the picture
        x0, y0, x1, y1 = box                         # on the screen
    else:
        lx0, ly0, lx1, ly1 = 0, 0, overlay.width - 1, overlay.height - 1
        x0, y0 = at
        x1, y1 = x0 + overlay.width - 1, y0 + overlay.height - 1
        if not (0 <= x0 and x1 < 1 << 10 and 0 <= y0 and y1 < 1 << 10):
            raise VtsError(f"a picture at {at} of {overlay.width}x{overlay.height} is off the screen")
    fields = []
    for first in (ly0, ly0 + 1):                 # the top field: lines y0, y0 + 2, ...
        bits = _Bits()
        for y in range(first, ly1 + 1, 2):
            _encode_line(bits, overlay.pixels[y * overlay.width + lx0:y * overlay.width + lx1 + 1])
        fields.append(bytes(bits.out))
    top = 4
    bottom = top + len(fields[0])
    dcsqt = bottom + len(fields[1])
    if dcsqt + 24 > MAX_UNIT:
        raise VtsError(f"the subpicture unit is over {dcsqt} bytes (at most {MAX_UNIT})")
    commands = bytes([0x00 if forced else 0x01])
    commands += bytes([0x03, colours[3] << 4 | colours[2], colours[1] << 4 | colours[0]])
    commands += bytes([0x04, contrasts[3] << 4 | contrasts[2], contrasts[1] << 4 | contrasts[0]])
    commands += bytes([0x05, x0 >> 4, (x0 & 0xF) << 4 | x1 >> 8, x1 & 0xFF,
                       y0 >> 4, (y0 & 0xF) << 4 | y1 >> 8, y1 & 0xFF])
    commands += struct.pack(">BHH", 0x06, top, bottom)
    commands += b"\xff"
    if stop is None:
        dcsq = struct.pack(">HH", 0, dcsqt) + commands   # one sequence, pointing at itself
    else:
        delay = int(round(stop / 1024))
        if not 0 < delay <= 0xFFFF:
            raise VtsError(f"a picture shown for {stop} ticks: up to {0xFFFF * 1024} and over 0")
        second = dcsqt + 4 + len(commands)
        dcsq = struct.pack(">HH", 0, second) + commands + struct.pack(">HH", delay, second) + b"\x02\xff"
    size = dcsqt + len(dcsq)
    if size > MAX_UNIT:
        raise VtsError(f"the subpicture unit is {size} bytes (at most {MAX_UNIT})")
    return struct.pack(">HH", size, dcsqt) + fields[0] + fields[1] + dcsq


def decode(unit):
    """The pixel codes of a unit as (x0, y0, {(x, y): code}) and its
    commands, for tests."""
    size, dcsqt = struct.unpack_from(">HH", unit, 0)
    if size != len(unit):
        raise VtsError("the unit's size does not match")
    cmds = unit[dcsqt + 4:]
    area = top = bottom = None
    i = 0
    while i < len(cmds) and cmds[i] != 0xFF:
        c = cmds[i]
        if c in (0x00, 0x01, 0x02):
            i += 1
        elif c in (0x03, 0x04):
            i += 3
        elif c == 0x05:
            b = cmds[i + 1:i + 7]
            area = (b[0] << 4 | b[1] >> 4, (b[1] & 0xF) << 8 | b[2], b[3] << 4 | b[4] >> 4,
                    (b[4] & 0xF) << 8 | b[5])
            i += 7
        elif c == 0x06:
            top, bottom = struct.unpack_from(">HH", cmds, i + 1)
            i += 5
        else:
            raise VtsError(f"subpicture command {c:#x} is not decoded here")
    x0, x1, y0, y1 = area
    width = x1 - x0 + 1
    pixels = {}

    def read_field(offset, first):
        pos = offset * 8
        for y in range(first, y1 + 1, 2):
            x = 0
            while x < width:
                v, bits = 0, 0
                while True:
                    v = v << 4 | (unit[pos >> 3] >> (4 - (pos & 7))) & 0xF
                    pos += 4
                    bits += 4
                    if v >= (1 << (bits // 2)) or bits == 16:     # 4 bits: n >= 1; 8: n >= 4; 12: n >= 16
                        break
                n, code = v >> 2, v & 3
                if n == 0:
                    n = width - x
                for k in range(n):
                    pixels[(x0 + x + k, y)] = code
                x += n
            pos = (pos + 7) & ~7
    read_field(top, y0)
    read_field(bottom, y0 + 1)
    return area, pixels, cmds


def insert(data, unit, pts, sub=0x20):
    """The stream (bytes or bytearray) with the unit added right after its
    first navigation pack, in packs of private stream 1, sub-stream `sub`,
    stamped `pts`. Returns a new bytearray."""
    packs = list(ps.iter_packs(data))
    if not packs or not packs[0].nav:
        raise VtsError("the stream does not start with a navigation pack")
    nav = bytes(data[:ps.SECTOR])
    scr = packs[0].scr
    out = bytearray(nav)
    remaining = unit
    first = True
    while remaining:
        room = ps.SECTOR - 14 - 6 - (3 + 5 if first else 3) - 1   # pack, PES, header, sub id
        chunk, remaining = remaining[:room], remaining[room:]
        header = bytes([0x81, 0x80, 0x05]) + _pts(pts) if first else bytes([0x81, 0x00, 0x00])
        pes = b"\x00\x00\x01\xbd" + struct.pack(">H", len(header) + 1 + len(chunk)) + header \
            + bytes([sub]) + chunk
        scr += 1
        pack = _pack_header(scr, nav) + pes
        pad = ps.SECTOR - len(pack)
        if pad:
            if pad < 6:
                raise VtsError("no room for the padding packet")      # (cannot happen: room leaves 1)
            pack += b"\x00\x00\x01\xbe" + struct.pack(">H", pad - 6) + b"\xff" * (pad - 6)
        out += pack
        first = False
    out += data[ps.SECTOR:]
    return out


def _pts(t):
    return bytes([0x21 | (t >> 29) & 0x0E, (t >> 22) & 0xFF, 0x01 | (t >> 14) & 0xFE,
                  (t >> 7) & 0xFF, 0x01 | (t << 1) & 0xFE])


def _pack_header(scr, like):
    """A pack header with this SCR (90 kHz; extension 0) and the mux rate of
    the pack `like`."""
    b = bytearray(like[:14])
    b[4] = 0x44 | ((scr >> 30) & 7) << 3 | (scr >> 28) & 3
    b[5] = (scr >> 20) & 0xFF
    b[6] = 0x04 | ((scr >> 15) & 0x1F) << 3 | (scr >> 13) & 3
    b[7] = (scr >> 5) & 0xFF
    b[8] = 0x04 | ((scr & 0x1F) << 3)
    b[9] = 0x01
    b[13] = 0xF8                                 # no stuffing
    return bytes(b)
