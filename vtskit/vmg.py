"""Write the video manager of a disc without menus: VIDEO_TS.IFO and its BUP.

The VMG IFO (https://dvdnav.mplayerhq.hu/dvdinfo/ifo.html, ifo_vmg.html)
holds the VMGI_MAT header with the first-play program chain right after
it, TT_SRPT (every title of the disc: its titleset, number there, chapters,
angles, and where the titleset starts on the disc) and VTS_ATRT (a copy of
each titleset's stream attributes). Without menus the VMGM tables, the
parental masks and the text data are absent. Each table starts at a sector;
the BUP is the IFO again.

From dvdauthor's output: the VMG is given 32 sectors on the disc (its IFO,
BUP and nothing else here), the first titleset starts right after; the
first-play chain's playback time is 0 with the disc's frame-rate flag; a
title's type is 0x3C (one sequential program chain, jumps allowed
everywhere).
"""

import os
import struct

from . import nav, vm
from .errors import VtsError
from .titleset import SECTOR, _pad

VMG_SECTORS = 32            # the room dvdauthor gives the VMG


def write(folder, titlesets, first_play=None, provider="vtskit", pal=None):
    """Write VIDEO_TS.IFO and VIDEO_TS.BUP for `titlesets` (the Titleset
    results of vtskit.titleset.write, numbered 1, 2, ... in order).
    `first_play`: the first-play chain's commands (8 bytes each; default:
    jump to title 1). `pal`: the disc's standard, else the first titleset's.
    Returns the sectors of the whole VIDEO_TS, VMG first."""
    if not titlesets:
        raise VtsError("a disc has at least one titleset")
    if [t.vts for t in titlesets] != list(range(1, len(titlesets) + 1)):
        raise VtsError("titlesets are numbered 1, 2, ... in order")
    if sum(len(t.titles) for t in titlesets) > 99:
        raise VtsError("a disc has at most 99 titles")
    if first_play is None:
        first_play = vm.assemble([vm.jump_tt(1)])
    for c in first_play:
        if len(c) != 8:
            raise VtsError("a VM command is 8 bytes")
    if not 1 <= len(first_play) <= 128:
        raise VtsError("the first-play chain has 1 to 128 commands")
    provider = provider.encode("ascii", "replace")[:32].ljust(32, b"\0")
    if pal is None:
        pal = titlesets[0].titles[0].stream.frame == 3600

    # the first-play program chain: no cells, pre commands only
    fp = bytearray(0xEC)
    fp[4:8] = nav.elapsed(0, 3600 if pal else 3003)
    struct.pack_into(">H", fp, 0xE4, 0xEC)
    fp += struct.pack(">HHHH", len(first_play), 0, 0, 8 + 8 * len(first_play) - 1)
    fp += b"".join(first_play)

    # TT_SRPT: the titles of the disc
    entries = b""
    start = VMG_SECTORS
    count = 0
    for ts in titlesets:
        for t in ts.titles:
            count += 1
            entries += struct.pack(">BBHHBBI", 0x3C, 1, len(t.cells), 0, ts.vts, t.number, start)
        start += ts.sectors
    tt_srpt = struct.pack(">HHI", count, 0, 8 + len(entries) - 1) + entries

    # VTS_ATRT: each titleset's attributes
    atrs = []
    for ts in titlesets:
        if len(ts.attributes) != 0x300:
            raise VtsError(f"titleset {ts.vts}: attributes are 0x300 bytes")
        atrs.append(struct.pack(">II", 8 + 0x300 - 1, 0) + ts.attributes)
    offsets, o = [], 8 + 4 * len(atrs)
    for a in atrs:
        offsets.append(o)
        o += len(a)
    vts_atrt = struct.pack(">HHI", len(atrs), 0, o - 1) + \
        struct.pack(f">{len(atrs)}I", *offsets) + b"".join(atrs)

    tables = [_pad(tt_srpt), _pad(vts_atrt)]
    head = _pad(bytes(0x400) + fp)
    sectors = len(head) // SECTOR
    pointers = []
    for t in tables:
        pointers.append(sectors)
        sectors += len(t) // SECTOR
    if 2 * sectors > VMG_SECTORS:
        raise VtsError("the VMG does not fit its 32 sectors")
    mat = bytearray(head)
    mat[:12] = b"DVDVIDEO-VMG"
    struct.pack_into(">I", mat, 0x0C, VMG_SECTORS - 1)
    struct.pack_into(">I", mat, 0x1C, sectors - 1)
    struct.pack_into(">H", mat, 0x20, 0x0011)
    struct.pack_into(">HHB", mat, 0x26, 1, 1, 1)          # one volume, this is it, side 1
    struct.pack_into(">H", mat, 0x3E, len(titlesets))
    mat[0x40:0x60] = provider
    struct.pack_into(">I", mat, 0x80, 0x400 + len(fp) - 1)
    struct.pack_into(">I", mat, 0x84, 0x400)
    struct.pack_into(">II", mat, 0xC4, pointers[0], 0)
    struct.pack_into(">II", mat, 0xCC, 0, pointers[1])
    ifo = bytes(mat) + b"".join(tables)

    os.makedirs(folder, exist_ok=True)
    for name in ("VIDEO_TS.IFO", "VIDEO_TS.BUP"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(ifo)
    return start
