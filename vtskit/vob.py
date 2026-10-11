"""A title's VOB: the program stream with its navigation packs filled in.

`scan` reads the stream oncemore's encoder made (ffmpeg -f dvd) and
describes its VOBUs; `cells_for` turns chapter times into cell starts the
way dvdauthor does; `write` copies the stream with every PCI and DSI filled
and returns what the IFO will need to know about the cells. No pack moves.
"""

import bisect
from dataclasses import dataclass, field

from . import nav, ps
from .errors import VtsError


@dataclass
class Vobu:
    sector: int                 # first sector, counted from the start of the stream
    sectors: int
    scr: int                    # the navigation pack's clock
    s_ptm: int = 0              # presentation time of the first picture shown
    e_ptm: int = 0              # ... just after the last
    pictures: int = 0
    ref_ends: list = field(default_factory=list)   # sectors ending the first 3 I/P pictures

    @property
    def has_video(self):
        return self.pictures > 0

    @property
    def end(self):
        return self.sector + self.sectors


@dataclass
class Stream:
    path: str
    vobus: list
    frame_rate_code: int
    audio: dict                 # stream number -> [(sector, pts)] in PTS order
    subpictures: set            # subpicture stream numbers seen

    @property
    def sectors(self):
        return self.vobus[-1].end if self.vobus else 0

    @property
    def frame_period(self):
        return ps.FRAME_PERIOD[self.frame_rate_code]


@dataclass
class Cell:
    """A cell of the VOB. Sectors are counted from the start of the title
    VOBs, as the IFO counts them."""
    number: int
    first_vobu: int
    last_vobu: int
    first_sector: int
    last_vobu_sector: int
    last_sector: int
    s_ptm: int
    e_ptm: int

    @property
    def ticks(self):
        return self.e_ptm - self.s_ptm


def scan(path):
    """Read the stream once: its VOBUs, pictures and audio packets."""
    vobus = []
    audio = {}
    subpictures = set()
    frame_rate_code = None
    progressive = False
    video = []                  # [(sector, payload bytes)] of the VOBU being read
    current = None

    def close(end):
        nonlocal frame_rate_code, progressive
        if current is None:
            return
        current.sectors = end - current.sector
        es = b"".join(chunk for _, chunk in video)
        pics, code, progressive = ps.pictures(es, progressive)
        if code is not None:
            frame_rate_code = code
        current.pictures = len(pics)
        if pics:
            if frame_rate_code not in ps.FRAME_PERIOD:
                raise VtsError(f"{path}: frame rate code {frame_rate_code} is not a DVD frame rate")
            period = ps.FRAME_PERIOD[frame_rate_code]
            current.s_ptm = min(current_pts)
            fields = sum(p.fields for p in pics)
            current.e_ptm = current.s_ptm + (fields * period + 1) // 2
            # where the elementary stream's byte at `offset` is on the disc
            starts = [0]
            for _, chunk in video[:-1]:
                starts.append(starts[-1] + len(chunk))
            refs = [p for p in pics if p.reference][:3]
            ends = [video[bisect.bisect_right(starts, p.end - 1) - 1][0] for p in refs]
            if not ends:
                ends = [current.end - 1]       # (no reference picture: not seen yet)
            while len(ends) < 3:
                ends.append(ends[-1])
            current.ref_ends = ends
        vobus.append(current)

    sector_count = 0
    current_pts = []
    for sector, data in ps.packs(path):
        sector_count = sector + 1
        pack = ps.parse_pack(data, f" at sector {sector} of {path}")
        if pack.nav():
            close(sector)
            current = Vobu(sector, 0, pack.scr)
            video, current_pts = [], []
        elif current is None:
            raise VtsError(f"{path} does not start with a navigation pack")
        for p in pack.pes:
            if p.stream == ps.VIDEO:
                video.append((sector, data[p.start:p.end]))
                if p.pts is not None:
                    current_pts.append(p.pts)
            elif p.stream == ps.PRIVATE_2:
                pass
            elif (a := p.audio_stream()) is not None:
                if p.pts is not None:
                    audio.setdefault(a, []).append((sector, p.pts))
            elif (s := p.subpicture_stream()) is not None:
                subpictures.add(s)
    close(sector_count)
    if not vobus:
        raise VtsError(f"{path} is empty")
    if frame_rate_code is None:
        raise VtsError(f"{path} has no video sequence header")
    for packets in audio.values():
        packets.sort(key=lambda t: t[1])
    return Stream(path, vobus, frame_rate_code, audio, subpictures)


def cells_for(stream, times):
    """Cell starts (VOBU indexes) for chapter times in seconds from the
    start of the title: each the VOBU whose start is nearest, as dvdauthor
    places them. The first cell always starts at the first VOBU."""
    starts = [v.s_ptm for v in stream.vobus]
    first = starts[0]
    out = {0}
    for t in times:
        want = first + round(t * 90000)
        k = bisect.bisect_right(starts, want)
        if k < len(starts) and (k == 0 or starts[k] - want < want - starts[k - 1]):
            out.add(k)
        else:
            out.add(k - 1)
    return sorted(out)


def _pointer(distance, skipped=False):
    return nav.SRI_VALID | distance | (nav.SRI_SKIPPED if skipped else 0)


def _search_pointers(stream, i, first, last):
    """The 19 forward and 19 backward span pointers of VOBU i in the cell
    of VOBUs first..last (far to near, near to far)."""
    vobus = stream.vobus
    starts = [v.s_ptm for v in vobus[first:last + 1]]
    me = vobus[i]
    end = vobus[last].e_ptm

    def at(t):
        """Which VOBU of the cell plays at tick t (index in the cell), or None."""
        if t < starts[0] or t >= end:
            return None
        return bisect.bisect_right(starts, t) - 1

    forward, backward = [], []
    for direction, out in ((1, forward), (-1, backward)):
        nearer_valid = True
        for k, span in enumerate(reversed(nav.SRI_SPANS)):            # near to far
            # dvdauthor marks "VOBUs in between" (bit 30, or 0x7FFFFFFF when
            # the pointer itself is missing) only across the wide gaps of
            # the table, from 7.5 to 10 s and beyond, never between spans
            # half a second apart, whatever the VOBUs' lengths
            wide = k > 0 and span - nav.SRI_SPANS[-k] > 45000
            j = at(me.s_ptm + direction * span)
            if j is None:
                out.append(nav.SRI_NONE_BEYOND if nearer_valid and wide else nav.SRI_NONE)
                nearer_valid = False
                continue
            v = vobus[first + j]
            out.append(_pointer(abs(v.sector - me.sector), wide))
            nearer_valid = True
    return forward[::-1], backward


def _sri(stream, i, first, last):
    vobus = stream.vobus
    me = vobus[i]

    def next_with_video():
        for v in vobus[i + 1:last + 1]:
            if v.has_video:
                return _pointer(v.sector - me.sector)
        return nav.SRI_NO_VIDEO

    def previous_with_video():
        for v in reversed(vobus[first:i]):
            if v.has_video:
                return _pointer(me.sector - v.sector)
        return nav.SRI_NO_VIDEO

    forward, backward = _search_pointers(stream, i, first, last)
    nxt = _pointer(vobus[i + 1].sector - me.sector) if i < last else nav.SRI_NONE
    prv = _pointer(me.sector - vobus[i - 1].sector) if i > first else nav.SRI_NONE
    return [next_with_video(), *forward, nxt, prv, *backward, previous_with_video()]


def _audio_sync(stream, vobu):
    """A_SYNCA: for each audio stream, the packet whose PTS is the last at
    or before the VOBU's start, as a sector distance with bit 15 set when it
    lies before the navigation pack. 0 for a stream the VOB has not."""
    out = []
    for n in range(8):
        packets = stream.audio.get(n)
        if not packets:
            out.append(0)
            continue
        k = bisect.bisect_right([pts for _, pts in packets], vobu.s_ptm) - 1
        sector = packets[max(k, 0)][0]      # (none before the start: the first one)
        distance = sector - vobu.sector
        if distance >= 0x8000 or distance <= -0x8000:
            raise VtsError("an audio packet lies too far from its VOBU")
        out.append(distance if distance >= 0 else 0x8000 | -distance)
    return out


def cells(stream, cell_starts, first_sector=0):
    """The cells of the VOB from the VOBU indexes where each starts."""
    if not cell_starts or cell_starts[0] != 0:
        raise VtsError("the first cell must start at the first VOBU")
    if sorted(set(cell_starts)) != list(cell_starts):
        raise VtsError("cell starts must increase")
    if cell_starts[-1] >= len(stream.vobus):
        raise VtsError("a cell starts after the last VOBU")
    out = []
    bounds = list(cell_starts) + [len(stream.vobus)]
    for n, (a, b) in enumerate(zip(bounds, bounds[1:]), 1):
        first, last = stream.vobus[a], stream.vobus[b - 1]
        out.append(Cell(n, a, b - 1, first_sector + first.sector, first_sector + last.sector,
                        first_sector + last.end - 1, first.s_ptm, last.e_ptm))
    return out


def nav_packs(stream, cell_starts, vob_id=1, first_sector=0, uop_ctl=0):
    """The filled navigation pack of every VOBU: {sector in the stream: (PCI
    data, DSI data)}. `first_sector` is where this VOB begins in the title
    VOBs; `vob_id` its number there."""
    if stream.subpictures:
        raise VtsError("subpicture streams are not written yet")
    out = {}
    for cell in cells(stream, cell_starts, first_sector):
        for i in range(cell.first_vobu, cell.last_vobu + 1):
            v = stream.vobus[i]
            frames = round((v.s_ptm - cell.s_ptm) / stream.frame_period)
            eltm = nav.time_code(frames, stream.frame_rate_code)
            lbn = first_sector + v.sector
            pci = nav.pci(lbn, v.s_ptm, v.e_ptm, eltm, uop_ctl)
            dsi = nav.dsi(v.scr, lbn, v.sectors - 1, [e - v.sector for e in v.ref_ends],
                          vob_id, cell.number, eltm, stream.vobus[0].s_ptm,
                          stream.vobus[-1].e_ptm, _sri(stream, i, cell.first_vobu, cell.last_vobu),
                          _audio_sync(stream, v))
            out[v.sector] = (pci, dsi)
    return out


def fill(data, pci, dsi, where=""):
    """This navigation pack (2048 bytes) with its PCI and DSI data replaced."""
    found = ps.parse_pack(data, where).nav()
    if not found:
        raise VtsError(f"not a navigation pack{where}")
    p, d = found
    if p.end - p.start != nav.PCI_SIZE or d.end - d.start != nav.DSI_SIZE:
        raise VtsError(f"a navigation pack of unexpected size{where}")
    out = bytearray(data)
    out[p.start:p.end] = pci
    out[d.start:d.end] = dsi
    return bytes(out)


def write(stream, path, cell_starts, vob_id=1, first_sector=0, uop_ctl=0):
    """Copy the stream to `path` with its navigation packs filled in, and
    return its cells."""
    packs = nav_packs(stream, cell_starts, vob_id, first_sector, uop_ctl)
    with open(path, "wb") as out:
        for sector, data in ps.packs(stream.path):
            if sector in packs:
                data = fill(data, *packs[sector], f" at sector {sector} of {stream.path}")
            out.write(data)
    return cells(stream, cell_starts, first_sector)
