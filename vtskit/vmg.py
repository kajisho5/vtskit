"""Write the video manager of a disc: VIDEO_TS.IFO and its BUP, and
VIDEO_TS.VOB when the disc has a title menu.

The VMG IFO (https://dvdnav.mplayerhq.hu/dvdinfo/ifo.html, ifo_vmg.html)
holds the VMGI_MAT header with the first-play program chain right after
it, TT_SRPT (every title of the disc: its titleset, number there, chapters,
angles, and where the titleset starts on the disc), VTS_ATRT (a copy of
each titleset's stream attributes) and, with menus, VMGM_PGCI_UT (one
language unit), VMGM_C_ADT and VMGM_VOBU_ADMAP. The parental masks and
the text data are absent. Each table starts at a sector; the BUP is the
IFO again.

From dvdauthor's output: the VMG takes its IFO, its menu VOB and its BUP
on the disc, 32 sectors at least, and the first titleset starts right
after; the first-play chain's playback time is 0 with the disc's
frame-rate flag, and it jumps to the title menu when there is one, else to
title 1; a title's type is 0x3C (one sequential program chain, jumps
allowed everywhere).
"""

import os
import struct

from . import nav, vm
from .errors import VtsError
from .titleset import SECTOR, VOB_FILE_SECTORS, _pad, menu_domain

VMG_SECTORS = 32            # the least room dvdauthor gives the VMG


def write(folder, titlesets, menus=(), first_play=None, language="en", provider="vtskit",
          pal=None, pan_scan=True, letterbox=True):
    """Write VIDEO_TS.IFO and VIDEO_TS.BUP (and VIDEO_TS.VOB) for
    `titlesets` (the Titleset results of vtskit.titleset.write, numbered
    1, 2, ... in order) and the VMG's `menus` (vtskit.menu.Menu; the one
    with entry "title" answers the Title key). `first_play`: the first-play
    chain's commands (8 bytes each; default: jump to the title menu, else to
    title 1). `pal`: the disc's standard, else the first titleset's.
    Returns the sectors of the whole VIDEO_TS, VMG first."""
    if not titlesets:
        raise VtsError("a disc has at least one titleset")
    if [t.vts for t in titlesets] != list(range(1, len(titlesets) + 1)):
        raise VtsError("titlesets are numbered 1, 2, ... in order")
    if sum(len(t.titles) for t in titlesets) > 99:
        raise VtsError("a disc has at most 99 titles")
    for m in menus:
        if m.entry not in (None, "title"):
            raise VtsError(f"a VMG menu answers the Title key or none, not {m.entry!r}")
    if first_play is None:
        if any(m.entry == "title" for m in menus):
            first_play = vm.assemble([vm.jump_ss_vmgm_menu(vm.MENU_TITLE)])
        else:
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

    menu_written, menu_data, menu_tables, menu_attributes = [], [], None, bytes(0x100)
    if menus:
        # (a video manager whose menus are commands alone: dvdauthor marks
        # the picture format 0xFFF8)
        menu_written, menu_data, *menu_tables, menu_attributes = menu_domain(
            menus, language, 0, pan_scan, letterbox, 3600 if pal else 3003, b"\xff\xf8")
    menu_sectors = sum(w.sectors for w in menu_written)
    if menu_sectors > VOB_FILE_SECTORS:
        raise VtsError("the VMG menus are larger than one VOB file")

    # the tables, each at a sector
    order = [("tt_srpt", None)]
    if menus:
        order.append(("vmgm_pgci_ut", menu_tables[0]))
    order.append(("vts_atrt", None))
    if menus:
        order += [("vmgm_c_adt", menu_tables[1]), ("vmgm_admap", menu_tables[2])]
    head = _pad(bytes(0x400) + fp)
    ifo_sectors = len(head) // SECTOR
    for name, t in order:
        if t is not None:
            ifo_sectors += len(_pad(t)) // SECTOR
    ifo_sectors += 2         # TT_SRPT and VTS_ATRT below, a sector each (checked)
    vmg_sectors = max(2 * ifo_sectors + menu_sectors, VMG_SECTORS)

    # TT_SRPT: the titles of the disc
    entries = b""
    start = vmg_sectors
    count = 0
    for ts in titlesets:
        for t in ts.titles:
            count += 1
            # (the title's type: one sequential chain, jumps everywhere, and the
            # two search operations it forbids, as in its program chain)
            entries += struct.pack(">BBHHBBI", 0x3C | (t.uops & 3), t.angles, len(t.programs) or 1,
                                   0, ts.vts, t.number, start)
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

    tables = {"tt_srpt": _pad(tt_srpt), "vts_atrt": _pad(vts_atrt)}
    if menus:
        tables.update(vmgm_pgci_ut=_pad(menu_tables[0]), vmgm_c_adt=_pad(menu_tables[1]),
                      vmgm_admap=_pad(menu_tables[2]))
    sectors, pointers = len(head) // SECTOR, {}
    for name, _ in order:
        pointers[name] = sectors
        sectors += len(tables[name]) // SECTOR
    if sectors != ifo_sectors:
        raise VtsError("the title table or the attributes outgrew a sector")   # (99 titles fit)
    mat = bytearray(head)
    mat[:12] = b"DVDVIDEO-VMG"
    struct.pack_into(">I", mat, 0x0C, vmg_sectors - 1)
    struct.pack_into(">I", mat, 0x1C, sectors - 1)
    struct.pack_into(">H", mat, 0x20, 0x0011)
    struct.pack_into(">HHB", mat, 0x26, 1, 1, 1)          # one volume, this is it, side 1
    struct.pack_into(">H", mat, 0x3E, len(titlesets))
    mat[0x40:0x60] = provider
    struct.pack_into(">I", mat, 0x80, 0x400 + len(fp) - 1)
    struct.pack_into(">I", mat, 0x84, 0x400)
    struct.pack_into(">I", mat, 0xC0, sectors if menus else 0)
    struct.pack_into(">II", mat, 0xC4, pointers["tt_srpt"], pointers.get("vmgm_pgci_ut", 0))
    struct.pack_into(">II", mat, 0xCC, 0, pointers["vts_atrt"])
    struct.pack_into(">II", mat, 0xD8, pointers.get("vmgm_c_adt", 0), pointers.get("vmgm_admap", 0))
    mat[0x100:0x200] = menu_attributes
    ifo = bytes(mat) + b"".join(tables[name] for name, _ in order)

    os.makedirs(folder, exist_ok=True)
    for name in ("VIDEO_TS.IFO", "VIDEO_TS.BUP"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(ifo)
    if menu_sectors:
        with open(os.path.join(folder, "VIDEO_TS.VOB"), "wb") as f:
            f.write(b"".join(menu_data))
    return start
