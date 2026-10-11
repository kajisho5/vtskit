"""Subtitles: pictures shown over a title's video for a while, added to its
program stream as subpicture packs (what spumux does).

A cue is a picture of pixel codes (vtskit.spu.Overlay) placed at a point
of the screen, shown from `start` to `end` (seconds from the title's first
picture), each code drawn with an entry of the title's palette
(vtskit.titleset.Title.palette, 16 colours) and a contrast. `mux` turns
the cues of each subpicture stream into units and places their packs in
the stream a little before they are shown: the unit's packs go after the
last pack whose clock (SCR) is `lead` seconds before the cue's time, so the
player has the picture when it is due (spumux delivers about 0.15 s
early). The other packs stay as they are; the clock of an added pack is
the previous pack's plus one. Each stream's cues must not overlap.

The DVD-Video program stream from ffmpeg (-f dvd) carries no subpicture
streams; `mux` adds them before vtskit.titleset.write fills the
navigation packs, which then point at the units (DSI SP_SYNCA).
"""

import bisect
import struct
from dataclasses import dataclass

from . import nav, ps, spu
from .errors import VtsError

LEAD = 0.15


@dataclass
class Cue:
    start: float
    end: float
    picture: spu.Overlay
    x: int = 0
    y: int = 0
    colours: tuple = (0, 1, 2, 3)        # the palette entry drawing each pixel code
    contrasts: tuple = (0, 15, 15, 15)   # and its contrast, 0 (not drawn) to 15
    forced: bool = False                  # shown even with subtitles off


def unit(cue):
    """The subpicture unit of a cue."""
    if not cue.end > cue.start >= 0:
        raise VtsError(f"a cue from {cue.start} to {cue.end} s")
    return spu.encode(cue.picture, cue.forced, cue.colours, cue.contrasts, (cue.x, cue.y),
                      stop=(cue.end - cue.start) * 90000)


def _packets(unit_, pts, sub, scr, like):
    """The packs carrying a unit, the first stamped `pts`, clocks from
    `scr` + 1 on."""
    out = []
    remaining = unit_
    first = True
    while remaining:
        room = ps.SECTOR - 14 - 6 - (3 + 5 if first else 3) - 1
        chunk, remaining = remaining[:room], remaining[room:]
        header = bytes([0x81, 0x80, 0x05]) + spu._pts(pts) if first else bytes([0x81, 0x00, 0x00])
        pes = b"\x00\x00\x01\xbd" + struct.pack(">H", len(header) + 1 + len(chunk)) + header \
            + bytes([sub]) + chunk
        scr += 1
        pack = spu._pack_header(scr, like) + pes
        pad = ps.SECTOR - len(pack)
        if pad:
            pack += b"\x00\x00\x01\xbe" + struct.pack(">H", pad - 6) + b"\xff" * (pad - 6)
        out.append(pack)
        first = False
    return out, scr


def mux(data, streams, lead=LEAD):
    """The program stream `data` (bytes) with the cues of each subpicture
    stream added: `streams` is {stream number 0-31: [Cue]}. Returns a
    bytearray."""
    data = bytes(data)
    packs = ps.read_packs(data)
    if not packs or not packs[0].nav:
        raise VtsError("the stream does not start with a navigation pack")
    stream = ps.scan(data)
    s0 = nav.vobus(stream)[0].s_ptm
    scrs = [p.scr for p in packs]
    if any(b <= a for a, b in zip(scrs, scrs[1:])):
        raise VtsError("the packs' clocks are not in order")
    events = []                     # (pack to go before, pts, stream, unit)
    for number, cues in streams.items():
        if not 0 <= number <= 31:
            raise VtsError(f"subpicture stream {number}: 0 to 31")
        if 0x20 + number in stream.subpictures:
            raise VtsError(f"the stream already has subpicture stream {number}")
        last = None
        for cue in sorted(cues, key=lambda c: c.start):
            if last is not None and cue.start < last:
                raise VtsError(f"subpicture stream {number}: a cue at {cue.start} s overlaps the one before")
            last = cue.end
            pts = s0 + int(round(cue.start * 90000))
            k = bisect.bisect_right(scrs, pts - int(lead * 90000))     # after the last pack early enough
            events.append((max(k, 1), pts, 0x20 + number, unit(cue)))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    out = bytearray()
    e = 0
    last_scr = None
    for k, pack in enumerate(packs):
        while e < len(events) and events[e][0] == k:
            _, pts, sub, u = events[e]
            added, last_scr = _packets(u, pts, sub, last_scr, data[:14])
            if last_scr >= pack.scr:
                raise VtsError(f"no room in the clock before pack {k} for the subpicture packs")
            for p in added:
                out += p
            e += 1
        out += data[k * ps.SECTOR:(k + 1) * ps.SECTOR]
        last_scr = pack.scr
    return out
