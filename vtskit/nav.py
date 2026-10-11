"""Fill the navigation packs of a VOB.

Every VOBU starts with a navigation pack: a PCI packet (presentation:
times, and a menu's buttons) and a DSI packet (search: where this VOBU
ends, where its reference pictures end, where the VOBUs 0.5 s to 120 s
away start, where its sound starts). ffmpeg's `-f dvd` leaves both empty;
`fill` writes them, and no pack moves.

The fields are those described at https://dvdnav.mplayerhq.hu/dvdinfo/
(pci_pkt.html, dsi_pkt.html). Where the description leaves a choice open,
the choice is the one dvdauthor makes, found by comparing its output
bytes (see tests/test_nav.py).
"""

import bisect
import struct

from . import ps
from .errors import VtsError

PCI = 0x2D                 # the PCI data, in the navigation pack
DSI = 0x407                # the DSI data

# the search spans of VOBU_SRI, in half seconds: forward from the farthest,
# backward from the nearest
SPANS = (240, 120, 60, 20, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)

COARSE = (20, 60, 120, 240)
NONE = 0x3FFFFFFF          # no VOBU for this span (or no next / previous VOBU)
NO_VIDEO = 0xBFFFFFFF      # no next / previous VOBU with video
VALID = 0x80000000
BETWEEN = 0x40000000       # VOBUs lie between this one and the nearer span's


class Vobu:
    """One VOBU of a stream: its sectors (nav pack first, last), times and
    what the DSI points at."""
    __slots__ = ("start", "end", "scr", "s_ptm", "e_ptm", "video", "refs", "cell")

    def __init__(self, start, end, scr):
        self.start, self.end, self.scr = start, end, scr
        self.s_ptm = self.e_ptm = None
        self.video = False
        self.refs = []
        self.cell = None           # (vob id, cell id, index of its first VOBU, of its last)


def vobus(stream):
    """The VOBUs of a scanned stream (`ps.scan`), with their times and
    reference pictures."""
    if not stream.navs or stream.navs[0][0] != 0:
        raise VtsError("the stream does not start with a navigation pack")
    starts = [s for s, _ in stream.navs]
    out = [Vobu(s, e - 1, scr) for (s, scr), e in zip(stream.navs, starts[1:] + [stream.sectors])]
    k = 0
    for p in stream.pictures:
        while k + 1 < len(out) and p.start >= out[k + 1].start:
            k += 1
        v = out[k]
        v.video = True
        if p.pts is not None and (v.s_ptm is None or p.pts < v.s_ptm):
            v.s_ptm = p.pts
        if p.type in "IP" and len(v.refs) < 3:
            v.refs.append(p.end - v.start)
    if any(v.s_ptm is None for v in out):
        raise VtsError("a VOBU without video (VOBUs of sound or stills alone are not written yet)")
    for a, b in zip(out, out[1:]):
        a.e_ptm = b.s_ptm
    out[-1].e_ptm = stream.end
    return out


def _bcd(n):
    return (n // 10) << 4 | n % 10


def elapsed(ticks, frame):
    """A time as the PGC and navigation packs give it: BCD hh:mm:ss:ff, the
    top two bits of ff saying 30 (11) or 25 (01) frames a second."""
    fps, flag = (25, 0x40) if frame == 3600 else (30, 0xC0)
    frames = int(round(ticks / frame))
    s, f = divmod(frames, fps)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    return bytes((_bcd(h), _bcd(m), _bcd(s), _bcd(f) | flag))


def _search(vs, times, i, first, last):
    """VOBU_SRI of VOBU i, its cell being VOBUs first..last (`times`: the
    start of each VOBU)."""
    v = vs[i]
    end = vs[last].e_ptm

    def find(t):
        if t < times[first] or t >= end:
            return None
        return bisect.bisect_right(times, t, first, last + 1) - 1

    def entries(sign):
        # (the coarse spans, 10 s and up, also say whether VOBUs lie between
        # their target and the nearer span's, or past it when they have none)
        out, nearer = [], i
        for span in sorted(SPANS):
            j = find(v.s_ptm + sign * span * 45000)
            coarse = span in COARSE and nearer is not None
            if j is None:
                beyond = first <= nearer + sign <= last if coarse else False
                out.append(NONE | (BETWEEN if beyond else 0))
            else:
                val = VALID | abs(vs[j].start - v.start)
                if coarse and abs(j - nearer) > 1:
                    val |= BETWEEN
                out.append(val)
            nearer = j
        return out                           # nearest first

    fwd = entries(1)[::-1]
    bwd = entries(-1)
    nxt = VALID | (vs[i + 1].start - v.start) if i < last else NONE
    prv = VALID | (v.start - vs[i - 1].start) if i > first else NONE
    nvwv = next((VALID | (vs[j].start - v.start) for j in range(i + 1, last + 1) if vs[j].video),
                NO_VIDEO)
    pvwv = next((VALID | (v.start - vs[j].start) for j in range(i - 1, first - 1, -1)
                 if vs[j].video), NO_VIDEO)
    return [nvwv] + fwd + [nxt, prv] + bwd + [pvwv]


def _audio_sync(stream, vs):
    """a_synca of each VOBU: per sound stream (numbered in the order the
    streams first appear), the offset to the stream's last packet stamped
    at or before the VOBU's start (or its first, if none is)."""
    if len(stream.audio) > 8:
        raise VtsError(f"{len(stream.audio)} sound streams (a DVD title has at most 8)")
    out = [[0] * 8 for _ in vs]
    for n, packets in enumerate(stream.audio.values()):
        stamped = [(s, p) for s, p, _ in packets if p is not None]
        if not stamped:
            continue
        k = 0
        for row, v in zip(out, vs):
            while k + 1 < len(stamped) and stamped[k + 1][1] <= v.s_ptm:
                k += 1
            off = stamped[k][0] - v.start
            row[n] = off if off >= 0 else 0x8000 | -off
    return out


def _subpicture_sync(stream, vs):
    """sp_synca of each VOBU: per subpicture stream (0-31), where the unit
    on screen during the VOBU is: in this VOBU, or nothing on screen
    (7fffffff), or the offset to the VOBU it starts in."""
    starts = [v.start for v in vs]
    out = [[0] * 32 for _ in vs]
    for sub, units in stream.subpictures.items():
        n = sub - 0x20
        k = 0
        for row, v in zip(out, vs):
            # (units are in order; skip those gone before this VOBU)
            while k < len(units) and units[k][2] is not None and units[k][2] <= v.s_ptm:
                k += 1
            val = 0x7FFFFFFF
            for sector, shown, until in units[k:k + 2]:
                if shown < v.e_ptm and (until is None or until > v.s_ptm):
                    home = starts[bisect.bisect_right(starts, sector) - 1]
                    if home > v.start:
                        val = home - v.start
                    elif home < v.start:
                        val = 0x80000000 | (v.start - home)
                    break
            row[n] = val
    return out


def fill(data, cells, lbn=0, uops=0, stream=None):
    """Fill the navigation packs of a VOB in place (a bytearray or a
    writable mmap).

    `cells`: [(vob id, cell id, first VOBU index, last VOBU index)], every
    VOBU in one cell. `lbn`: the sector of the stream's first pack in the
    titleset's VOBs. `stream`: the stream already scanned, if it is.
    Returns the VOBUs."""
    stream = stream or ps.scan(data)
    vs = vobus(stream)
    covered = [None] * len(vs)
    for cell in cells:
        vob_id, cell_id, first, last = cell
        for j in range(first, last + 1):
            covered[j] = cell
    if None in covered:
        raise VtsError("a VOBU is in no cell")
    vob_s, vob_e = {}, {}
    for v, cell in zip(vs, covered):
        v.cell = cell
        vob_s.setdefault(cell[0], v.s_ptm)
        vob_e[cell[0]] = v.e_ptm
    times = [v.s_ptm for v in vs]
    sync = _audio_sync(stream, vs)
    spu = _subpicture_sync(stream, vs)
    for i, v in enumerate(vs):
        vob_id, cell_id, first, last = v.cell
        base = v.start * ps.SECTOR
        if data[base + 0x26:base + 0x2D] != b"\x00\x00\x01\xbf\x03\xd4\x00" or \
                data[base + 0x400:base + 0x407] != b"\x00\x00\x01\xbf\x03\xfa\x01":
            raise VtsError(f"sector {v.start}: not a navigation pack of PCI and DSI")
        eltm = elapsed(v.s_ptm - vs[first].s_ptm, stream.frame)
        pci = bytearray(0x3D4 - 1)
        struct.pack_into(">IIIIIII", pci, 0, lbn + v.start, 0, uops,
                         v.s_ptm, v.e_ptm, 0, int.from_bytes(eltm, "big"))
        dsi = bytearray(0x3FA - 1)
        # (fewer than three reference pictures: the last one's end again,
        # as dvdauthor writes it)
        refs = v.refs + [v.refs[-1] if v.refs else 0] * (3 - len(v.refs))
        struct.pack_into(">IIIIIIHxB4s", dsi, 0, v.scr & 0xFFFFFFFF, lbn + v.start,
                         v.end - v.start, *refs, vob_id, cell_id, eltm)
        struct.pack_into(">II", dsi, 0x2C, vob_s[vob_id], vob_e[vob_id])
        struct.pack_into(">42I", dsi, 0xEA, *_search(vs, times, i, first, last))
        struct.pack_into(">8H", dsi, 0x192, *sync[i])
        struct.pack_into(">32I", dsi, 0x1A2, *spu[i])
        data[base + PCI:base + PCI + len(pci)] = pci
        data[base + DSI:base + DSI + len(dsi)] = dsi
    return vs
