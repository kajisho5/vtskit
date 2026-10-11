"""Read the chapters of a DVD-Video disc from its IFO files.

Where each chapter of each title starts, as a sector of the disc: what a
titleset writer is checked against, and what a layer break is chosen from.
Read-only; nothing here writes an IFO.

Sector numbers in the IFO files are relative: the VMG's title table gives
where each titleset starts (counted from VIDEO_TS.IFO), a titleset IFO
gives where its title VOBs start (VTS_NN_0.IFO at 0xC4, counted from the
titleset), and a cell's first sector, in the program chain's cell playback
table, is counted from the title VOBs.

(From oncemore's layerbreak.py, by the same author, relicensed under
LGPL-3.0-or-later for vtskit.)
"""

import os
import struct

from .errors import VtsError

SECTOR = 2048


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _check(b, o, size, what):
    if o < 0 or o + size > len(b):
        raise VtsError(f"{what} is outside the IFO ({o} of {len(b)} bytes)")


def chapter_cells(ifo):
    """The chapters of a VTS_NN_0.IFO (its bytes): [(title number in the
    titleset, chapter, first sector)], the sector relative to the start of
    the title VOBs."""
    if ifo[:12] != b"DVDVIDEO-VTS":
        raise VtsError("not a titleset IFO")
    try:
        ptt = _u32(ifo, 0xC8) * SECTOR
        pgcit = _u32(ifo, 0xCC) * SECTOR
        _check(ifo, ptt, 8, "the chapter table")
        _check(ifo, pgcit, 8, "the program chain table")
        ntitles, end = _u16(ifo, ptt), ptt + _u32(ifo, ptt + 4) + 1
        offsets = [ptt + _u32(ifo, ptt + 8 + 4 * k) for k in range(ntitles)] + [end]
        out = []
        for ttn in range(1, ntitles + 1):
            a, b = offsets[ttn - 1], offsets[ttn]
            for ch, o in enumerate(range(a, b - 3, 4), 1):
                pgcn, pgn = _u16(ifo, o), _u16(ifo, o + 2)
                if pgcn < 1 or pgn < 1:
                    raise VtsError(f"title {ttn} chapter {ch} points at no program")
                pgc = pgcit + _u32(ifo, pgcit + 8 + 8 * (pgcn - 1) + 4)
                _check(ifo, pgc, 0xEC, f"program chain {pgcn}")
                entry = pgc + _u16(ifo, pgc + 0xE6) + pgn - 1
                _check(ifo, entry, 1, f"program {pgn} of program chain {pgcn}")
                cell = ifo[entry]                       # the program's entry cell
                cpb = pgc + _u16(ifo, pgc + 0xE8) + 24 * (cell - 1)
                _check(ifo, cpb, 24, f"cell {cell} of program chain {pgcn}")
                out.append((ttn, ch, _u32(ifo, cpb + 8)))
        return out
    except struct.error as e:
        raise VtsError(f"damaged titleset IFO: {e}") from None


def vts_bases(ifo_bytes):
    """{titleset: sector of its title VOBs, relative to VIDEO_TS.IFO} from
    the VMG and VTS IFOs ({"VIDEO_TS.IFO": bytes, "VTS_01_0.IFO": bytes, ...})."""
    vmg = ifo_bytes.get("VIDEO_TS.IFO", b"")
    if vmg[:12] != b"DVDVIDEO-VMG":
        raise VtsError("VIDEO_TS.IFO is not a DVD-Video IFO")
    try:
        tt = _u32(vmg, 0xC4) * SECTOR
        _check(vmg, tt, 8, "the title table")
        starts = {}
        for k in range(_u16(vmg, tt)):
            o = tt + 8 + 12 * k
            _check(vmg, o, 12, f"title {k + 1} of the title table")
            starts[vmg[o + 6]] = _u32(vmg, o + 8)
    except struct.error as e:
        raise VtsError(f"damaged VIDEO_TS.IFO: {e}") from None
    out = {}
    for vts, start in starts.items():
        name = f"VTS_{vts:02d}_0.IFO"
        if name not in ifo_bytes:
            raise VtsError(f"the title table names titleset {vts}, and {name} is missing")
        out[vts] = start + _u32(ifo_bytes[name], 0xC4)
    return out


def chapters(ifo_bytes):
    """Every chapter on the disc: [(titleset, title number, chapter, sector
    relative to VIDEO_TS.IFO)], in disc order."""
    out = []
    for vts, base in sorted(vts_bases(ifo_bytes).items()):
        for ttn, ch, s in chapter_cells(ifo_bytes[f"VTS_{vts:02d}_0.IFO"]):
            out.append((vts, ttn, ch, base + s))
    return sorted(out, key=lambda c: c[3])


def read_folder(video_ts):
    """The IFO files of a VIDEO_TS folder: {name: bytes}."""
    out = {}
    for n in os.listdir(video_ts):
        if n == "VIDEO_TS.IFO" or (n.startswith("VTS_") and n.endswith("_0.IFO")):
            with open(os.path.join(video_ts, n), "rb") as f:
                out[n] = f.read()
    return out
