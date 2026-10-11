"""Write a titleset: the title VOBs with their navigation packs filled, and
VTS_nn_0.IFO with its BUP.

A titleset without menus holds (https://dvdnav.mplayerhq.hu/dvdinfo/
ifo.html, ifo_vts.html, pgc.html): the VTS_MAT header with the streams'
attributes; VTS_PTT_SRPT, each title's chapters as (program chain,
program); VTS_PGCI, one program chain a title, its programs and cells one a
chapter; VTS_TMAPT, a time map a program chain with an entry a second;
VTS_C_ADT, the cells' sectors; VTS_VOBU_ADMAP, every VOBU's first sector.
Each table starts at a sector; the BUP is the IFO again.

Where the descriptions leave a choice open, the choice is dvdauthor's, read
from its output (tests/test_titleset.py compares the files byte for byte):

* times are counted in frames at the nominal rate (30 or 25 a second): a
  chapter starts at the VOBU whose start is nearest its time, and the time
  map's entry for second t is the VOBU playing at frame 30t (25t), for every
  t that ends before the title does, the sound included; an entry's top bit
  is set when the next entry is in another cell, or there is none;
* the first cell of a VOB has the STC discontinuity flag, the others the
  seamless playback flag (dvdauthor leaves it off cells it was given one by
  one; vtskit leaves it off cells that end in a still); a program chain
  ends with the Exit command when it has no post commands of its own; a
  cell's still time is the third byte of its category (0xFF: until the
  user goes on), its command number the fourth;
* closed captions in the video are flagged in the video attributes' second
  byte: bit 7 for field 1, bit 6 for field 2;
* the audio attributes' second byte is 0xC0 for AC-3 and DTS, 0x40 for
  MPEG and 0x00 for 16-bit LPCM, plus the channels less one;
* a 4:3 title forbids automatic pan-scan and letterbox (both are moot).

With menus (vtskit.menu), the titleset also holds VTS_nn_0.VOB (every
menu's VOB with its subpicture overlay and highlight information), and the
IFO its VTSM_PGCI_UT (one language unit, a program chain a menu, the entry
menus marked), VTSM_C_ADT and VTSM_VOBU_ADMAP, and the menus' attributes.
As dvdauthor does, a titleset with a root menu and no chapter (PTT) menu
gets an empty chapter-menu program chain that links to the root.

A title may be several parts in a row, each a program stream (its own VOB)
or an Angles block of 2 to 9 streams the viewer switches between. An angle
block is interleaved (dvdinfo dsi_pkt.html, pgc.html): each angle's VOBUs
are cut into interleaved units (ILVUs) of the same number of VOBUs, and the
units take turns on the disc, angle 1's first, then angle 2's, ... then
angle 1's second; every VOBU's DSI says where its unit ends, where its
angle's next unit is, and where every angle's next unit is (SML_AGLI);
every PCI where every angle's VOBU of the same moment is (NSML_AGLI); the
three VOBUs before a block carry the PREU flags; the block's cells are one
an angle, marked first, middle and last of an angle block, interleaved,
and seamless when asked. dvdauthor writes no angles, so none of this is
checked against it: the structure is checked against the descriptions and
libdvdnav plays and switches the angles (tests/test_angles.py); a set-top
player is the check still owed.

Not checked against dvdauthor (it needs a title over 1 GiB): the VOBs are
cut into files of at most 524272 sectors, at VOBU starts.
"""

import bisect
import os
import struct
from dataclasses import dataclass, field

from . import menu as menus_, nav, ps, spu
from .errors import VtsError

SECTOR = ps.SECTOR
VOB_FILE_SECTORS = 524272          # one VOB file is under 1 GiB (Inside DVD-Video)
EXIT = bytes.fromhex("3001000000000000")    # the VM command Exit (dvdinfo vmi.html)
BLACK = (0x10, 0x80, 0x80)                  # Y, Cr, Cb

# private stream 1 sub-stream -> audio coding mode, and the second attribute byte
_CODING = {"ac3": (0, 0xC0), "dts": (6, 0xC0), "mpeg": (2, 0x40), "lpcm": (4, 0x00)}


@dataclass
class Audio:
    """A sound stream's attributes: language (ISO 639-1) and channels."""
    lang: str | None = None
    channels: int = 2


@dataclass
class Subpicture:
    lang: str | None = None


@dataclass
class Angles:
    """A stretch of a title the viewer may watch from 2 to 9 angles: that
    many program streams of the same length whose VOBUs line up in time (the
    same GOPs). `seamless`: the player switches without a break. `ilvu_vobus`:
    VOBUs an interleaved unit (default: as many as make a second)."""
    streams: tuple
    seamless: bool = True
    ilvu_vobus: int | None = None


@dataclass
class Title:
    """A title: its program stream (ffmpeg -f dvd, as oncemore encodes), or
    `parts` in play order (streams and Angles blocks); chapter times in
    seconds from its start (the first chapter is at 0 whether listed or
    not), an Audio for each sound stream in the order they first appear, a
    Subpicture for each subpicture stream, the 16 colours its subpictures
    choose from as (Y, Cr, Cb) (black where not given), and its program
    chain's commands: `pre` and `post` as lists of 8-byte commands
    (`vtskit.vm.assemble` makes them), `cell_commands` as {chapter number:
    one 8-byte command run when the chapter's last cell ends}."""
    stream: str | None = None
    chapters: tuple = (0.0,)
    audio: tuple = ()
    subpictures: tuple = ()
    palette: tuple = ()
    pre: tuple = ()
    post: tuple = (EXIT,)
    cell_commands: dict = field(default_factory=dict)
    stills: dict = field(default_factory=dict)      # {chapter: seconds 1-254, or None: until Play}
    uops: int = 0                                   # user operations the title forbids (bit n: vtskit.vm.UOP_*)
    parts: tuple = ()


@dataclass
class Cell:
    number: int             # in the program chain
    vob_id: int
    cell_id: int            # in its VOB
    first_vobu: int         # indexes in its VOB
    last_vobu: int
    first_sector: int       # in the title VOBs
    first_ilvu_end: int     # the last sector of its first interleaved unit (angles), else 0
    last_vobu_sector: int
    last_sector: int
    s_ptm: int
    e_ptm: int              # the next cell's start in its VOB, or the VOB's video end
    program: int = 1        # the program (chapter) it belongs to
    angle: int = 0          # 0: not in an angle block; else the angle (1..9)
    angles: int = 0         # the angles of its block
    stc_discontinuity: bool = True   # the first cell of its VOB
    seamless: bool = False  # continues the previous cell without a break
    interleaved: bool = False
    last_of_program: bool = True
    segment: tuple = (0, 1)     # (part, stretch of it) the cell plays

    @property
    def category(self):
        """The first byte of the cell category."""
        kind = 0
        if self.angle:
            kind = 0x40 if self.angle == 1 else 0xC0 if self.angle == self.angles else 0x80
            kind |= 0x10
        return kind | (0x08 if self.seamless else 0) | (0x04 if self.interleaved else 0) \
            | (0x02 if self.stc_discontinuity else 0) | (0x01 if self.seamless and self.angle else 0)


@dataclass
class WrittenTitle:
    """A title's VOBs as written (a menu: its one VOB)."""
    number: int             # in the titleset (a menu: its program chain number)
    vob_id: int             # the first VOB's
    sectors: int            # of all its VOBs
    first_sector: int
    cells: list             # in program chain order
    vobus: list             # nav.Vobu of the first VOB (a menu's)
    stream: object          # the first VOB's stream (its attributes)
    uops: int = 0
    programs: list = field(default_factory=list)    # the entry cell of each program
    angles: int = 1
    chunks: list = field(default_factory=list)      # (sector, bytes) to lay on the disc
    timeline: list = field(default_factory=list)    # (tick from the start, sector, cell number) of each VOBU played
    end: int = 0                                    # ticks from the start to the end, the sound included
    vobu_sectors: list = field(default_factory=list)


@dataclass
class Titleset:
    """What was written: for the VMG, and to place the titleset on a disc."""
    vts: int
    folder: str
    ifo_sectors: int
    vob_sectors: int
    titles: list
    attributes: bytes = b""     # the VTS_MAT from 0x100 on, as VTS_ATRT copies it
    files: list = field(default_factory=list)
    menu_sectors: int = 0

    @property
    def sectors(self):
        return 2 * self.ifo_sectors + self.menu_sectors + self.vob_sectors


def _lang(code):
    if code is None:
        return b"\0\0"
    if len(code) != 2 or not code.isascii() or not code.isalpha():
        raise VtsError(f"{code!r} is not an ISO 639-1 language code")
    return code.lower().encode()


def _nominal(frame):
    """Frames a second as the time code counts them."""
    return 25 if frame == 3600 else 30


def cell_starts(vobus, frame, chapters):
    """The VOBUs (indexes) where cells start: for each chapter time, the
    VOBU whose start is nearest, in frames; the first always starts a cell."""
    starts = [v.s_ptm - vobus[0].s_ptm for v in vobus]
    fps = _nominal(frame)
    out = {0}
    for t in chapters:
        if t < 0:
            raise VtsError(f"a chapter at {t} s, before the start")
        want = t * fps * frame
        out.add(min(range(len(starts)), key=lambda i: (abs(starts[i] - want), i)))
    return sorted(out)


def _video_attributes(stream, pan_scan, letterbox, captions=(False, False)):
    if stream.sequence is None:
        raise VtsError("the video has no sequence header")
    width, height, aspect, rate = stream.sequence
    if rate not in (1, 3, 4):
        raise VtsError(f"frame rate code {rate} is not a DVD frame rate")
    pal = rate == 3
    lines = 576 if pal else 480
    sizes = {(720, lines): 0, (704, lines): 1, (352, lines): 2, (352, lines // 2): 3}
    if (width, height) not in sizes:
        raise VtsError(f"{width}x{height} is not a DVD picture size for {'PAL' if pal else 'NTSC'}")
    if aspect == 2:
        flags = 0x03                        # 4:3: pan-scan and letterbox forbidden
    elif aspect == 3:
        flags = (0 if pan_scan else 0x02) | (0 if letterbox else 0x01)
    else:
        raise VtsError(f"aspect ratio code {aspect} is neither 4:3 nor 16:9")
    byte0 = 0x40 | (0x10 if pal else 0) | (0x0C if aspect == 3 else 0) | flags
    cc = (0x80 if captions[0] else 0) | (0x40 if captions[1] else 0)
    if cc and pal:
        raise VtsError("line-21 closed captions are NTSC only")
    return bytes((byte0, cc | sizes[(width, height)] << 3))


def _audio_kind(key):
    sid, sub = key
    if sid != 0xBD:
        return "mpeg"
    if sub in ps.SUB_AC3:
        return "ac3"
    if sub in ps.SUB_DTS:
        return "dts"
    if sub in ps.SUB_LPCM:
        return "lpcm"
    raise VtsError(f"sub-stream {sub:#x} is not a sound stream")


def _audio_attributes(stream, audio):
    keys = list(stream.audio)
    if len(keys) > 8:
        raise VtsError(f"{len(keys)} sound streams (a title has at most 8)")
    if len(audio) > len(keys):
        raise VtsError(f"{len(audio)} sound streams described, {len(keys)} in the stream")
    audio = list(audio) + [Audio()] * (len(keys) - len(audio))
    out = bytearray()
    for key, a in zip(keys, audio):
        coding, second = _CODING[_audio_kind(key)]
        if not 1 <= a.channels <= 8:
            raise VtsError(f"{a.channels} channels")
        out += bytes((coding << 5 | (0x04 if a.lang else 0), second | (a.channels - 1)))
        out += _lang(a.lang) + bytes(4)
    return len(keys), bytes(out).ljust(64, b"\0")


def _subpicture_attributes(stream, subpictures):
    subs = sorted(stream.subpictures)
    if len(subs) > 32:
        raise VtsError(f"{len(subs)} subpicture streams (a title has at most 32)")
    if len(subpictures) > len(subs):
        raise VtsError(f"{len(subpictures)} subpicture streams described, {len(subs)} in the stream")
    subpictures = list(subpictures) + [Subpicture()] * (len(subs) - len(subpictures))
    out = bytearray()
    for s in subpictures:
        out += bytes((0x01 if s.lang else 0, 0)) + _lang(s.lang) + bytes(2)
    return len(subs), bytes(out).ljust(192, b"\0"), subs


def _palette(colours):
    if len(colours) > 16:
        raise VtsError("a program chain's palette has 16 colours")
    out = bytearray()
    for y, cr, cb in list(colours) + [BLACK] * (16 - len(colours)):
        if not (0 <= y <= 255 and 0 <= cr <= 255 and 0 <= cb <= 255):
            raise VtsError("a colour's Y, Cr and Cb are 0 to 255")
        out += bytes((0, y, cr, cb))
    return bytes(out)


def _still(seconds):
    if seconds is None:
        return 0xFF
    if not 1 <= seconds <= 254:
        raise VtsError(f"a still of {seconds} s: 1 to 254, or None for ever")
    return seconds


def _pgc(commands, written, frame, subs, palette, title=True, stills=None, uops=0, video=None):
    """One program chain: `commands` = (pre, post, {chapter: command});
    `stills` {chapter: seconds or None} holds the picture at the chapter's
    last cell's end. `video`: the domain's video attribute bytes, for the
    subpicture streams of a 16:9 picture."""
    pre, post, cell_commands = commands
    cells = written.cells
    programs = written.programs or [1]
    stills = stills or {}
    for n in stills:
        if not 1 <= n <= len(programs):
            raise VtsError(f"a still for chapter {n}; there are {len(programs)}")
    if not 0 <= uops < 1 << 25:
        raise VtsError("the user operations are a 25-bit mask")
    for n in cell_commands:
        if not 1 <= n <= len(programs):
            raise VtsError(f"a cell command for chapter {n}; there are {len(programs)}")
    cell_list = [cell_commands[n] for n in sorted(cell_commands)]
    for c in (*pre, *post, *cell_list):
        if len(c) != 8:
            raise VtsError("a VM command is 8 bytes")
        # (a title may jump within its titleset, call a menu or the first
        # play chain, or exit; JumpTT and JumpSS belong to menus, and a
        # player stops on them: libdvdnav asserts)
        if title and c[0] == 0x30 and c[1] & 0x0F in (2, 6):
            raise VtsError("a title cannot JumpTT or JumpSS: call a menu or the first-play chain")
    if len(cells) > 255:
        raise VtsError(f"{len(cells)} cells (a program chain has at most 255)")
    head = bytearray(0xEC)
    head[2], head[3] = len(programs) if cells else 0, len(cells)
    head[4:8] = nav.elapsed(sum(c.e_ptm - c.s_ptm for c in cells if c.angle <= 1), frame)
    struct.pack_into(">I", head, 8, uops)
    # streams: available, numbered as the VOB numbers them
    if written.stream is not None:
        for n, key in enumerate(written.stream.audio):
            number = key[1] & 7 if key[0] == 0xBD else key[0] & 7
            struct.pack_into(">H", head, 0x0C + 2 * n, 0x8000 | number << 8)
        # a subpicture stream's entry names the stream for a 4:3 picture and,
        # for a 16:9 one, the streams for a wide, a letterboxed and a
        # pan-scanned display. dvdauthor writes the 4:3 field as available,
        # stream 0, and the same stream for each 16:9 display the video
        # attributes permit: `80 nn nn nn`, `80 nn nn 00` without pan-scan,
        # `80 nn 00 nn` without letterbox; `8n 00 00 00` for a 4:3 picture
        wide = video is not None and video[0] & 0x0C == 0x0C
        for n, sub in enumerate(subs):
            k = sub - 0x20
            if wide:
                entry = 0x80 << 24 | k << 16 | (0 if video[0] & 0x01 else k) << 8 | (0 if video[0] & 0x02 else k)
            else:
                entry = (0x80 | k) << 24
            struct.pack_into(">I", head, 0x1C + 4 * n, entry)
    head[0xA4:0xE4] = _palette(palette)
    table = struct.pack(">HHHH", len(pre), len(post), len(cell_list),
                        8 + 8 * (len(pre) + len(post) + len(cell_list)) - 1)
    table += b"".join((*pre, *post, *cell_list))
    command_of = {n: k for k, n in enumerate(sorted(cell_commands), 1)}
    program_map = bytes(programs)
    program_map += b"\0" * (len(program_map) % 2)
    playback = bytearray()
    for c in cells:
        ends = c.last_of_program                     # the chapter's last cell(s)
        still = _still(stills[c.program]) if ends and c.program in stills else 0
        category = c.category & (~0x08 if still else 0xFF)     # (a stop is not seamless)
        playback += struct.pack(">BBBB4sIIII", category, 0, still,
                                command_of.get(c.program, 0) if ends else 0,
                                nav.elapsed(c.e_ptm - c.s_ptm, frame), c.first_sector, c.first_ilvu_end,
                                c.last_vobu_sector, c.last_sector)
    position = b"".join(struct.pack(">HBB", c.vob_id, 0, c.cell_id) for c in cells)
    o = len(head)
    if cells:
        struct.pack_into(">HHHH", head, 0xE4, o, o + len(table), o + len(table) + len(program_map),
                         o + len(table) + len(program_map) + len(playback))
    else:                       # commands alone: no program map, no cells
        struct.pack_into(">H", head, 0xE4, o)
        program_map = b""
    return bytes(head) + table + program_map + playback + position


def _empty_pgc(commands):
    """A program chain of commands alone (no cells): dvdauthor's chapter
    menu that links to the root."""
    head = bytearray(0xEC)
    struct.pack_into(">H", head, 0xE4, 0xEC)
    table = struct.pack(">HHHH", len(commands), 0, 0, 8 + 8 * len(commands) - 1) + b"".join(commands)
    return bytes(head) + table


def _time_map(written, frame):
    """A program chain's time map: for each whole second t before the title
    ends, the VOBU playing at frame t * 30 (25), its top bit set when the
    next entry is in another cell or there is none."""
    fps = _nominal(frame)
    ticks = [t for t, _, _ in written.timeline]
    picks = []
    t = 1
    while t * fps * frame < written.end:
        picks.append(bisect.bisect_right(ticks, t * fps * frame) - 1)
        t += 1
    entries = []
    for k, i in enumerate(picks):
        last = k + 1 == len(picks) or written.timeline[picks[k + 1]][2] != written.timeline[i][2]
        entries.append((0x80000000 if last else 0) | written.timeline[i][1])
    return struct.pack(">BBH", 1, 0, len(entries)) + struct.pack(f">{len(entries)}I", *entries)


def _table(head, offsets_from, parts):
    """A table of a count, an end address and offsets to its parts."""
    n = len(parts)
    offsets, o = [], offsets_from + 4 * n
    for p in parts:
        offsets.append(o)
        o += len(p)
    body = struct.pack(f">{n}I", *offsets) + b"".join(parts)
    return head(n, offsets_from + len(body) - 1) + body


def _pad(b):
    return b.ljust(-(-len(b) // SECTOR) * SECTOR, b"\0")


def _prepare(number, vob_id, path, cell_times, offset, extra=None, hli=None):
    """Read a title's or a menu's stream, place its cells and fill its
    navigation packs: (its WrittenTitle, its bytes). `extra`: a subpicture
    unit to add after the first navigation pack."""
    with open(path, "rb") as f:
        vob = ps.drop_padding(f.read())
    if extra is not None:
        stream = ps.scan(vob)
        vob = spu.insert(vob, extra, nav.vobus(stream)[0].s_ptm)
    stream = ps.scan(vob)
    vs = nav.vobus(stream)
    starts = cell_starts(vs, stream.frame, cell_times)
    bounds = starts + [len(vs)]
    cells = []
    for c, (a, b) in enumerate(zip(bounds, bounds[1:]), 1):
        end = vs[b].s_ptm if b < len(vs) else stream.video_end
        cells.append(Cell(c, vob_id, c, a, b - 1, offset + vs[a].start, 0, offset + vs[b - 1].start,
                          offset + vs[b - 1].end, vs[a].s_ptm, end, stc_discontinuity=c == 1,
                          seamless=c > 1, last_of_program=c == len(starts)))
    nav.fill(vob, [(vob_id, c.cell_id, c.first_vobu, c.last_vobu) for c in cells], offset,
             stream=stream, hli={vob_id: hli} if hli else None)
    s0 = vs[0].s_ptm
    timeline = [(v.s_ptm - s0, offset + v.start, c.number) for c in cells
                for v in vs[c.first_vobu:c.last_vobu + 1]]
    w = WrittenTitle(number, vob_id, stream.sectors, offset, cells, vs, stream, programs=[1],
                     chunks=[(offset, vob)], timeline=timeline, end=vs[-1].e_ptm - s0,
                     vobu_sectors=[offset + v.start for v in vs])
    return w, vob



def _load(path):
    with open(path, "rb") as f:
        vob = ps.drop_padding(f.read())
    stream = ps.scan(vob)
    vs = nav.vobus(stream)
    return stream, vs, vob


def _aligned(loaded, where):
    """Angles must have the same VOBUs in time (the same GOPs) and length."""
    first = loaded[0]
    for k, other in enumerate(loaded[1:], 2):
        same = len(other[1]) == len(first[1]) and all(
            a.s_ptm - first[1][0].s_ptm == b.s_ptm - other[1][0].s_ptm for a, b in zip(first[1], other[1])) \
            and first[0].video_end - first[1][0].s_ptm == other[0].video_end - other[1][0].s_ptm
        if not same:
            raise VtsError(f"{where}: angle {k} differs from angle 1 in length or GOP layout")


def _title(number, title, offset, vob_id):
    """Read a title's parts, place its chapters, lay its VOBUs on the disc
    (interleaving the angle blocks) and fill their navigation packs:
    (WrittenTitle, the next free sector, the next VOB id)."""
    parts = title.parts or (title.stream,)
    if not parts or title.stream and title.parts:
        raise VtsError(f"title {number}: give a stream or parts, not both")
    loaded = []                 # a part: [(stream, vobus, bytes)] one an angle
    for k, part in enumerate(parts, 1):
        if isinstance(part, Angles):
            if not 2 <= len(part.streams) <= 9:
                raise VtsError(f"title {number}, part {k}: 2 to 9 angles")
            angles = [_load(p) for p in part.streams]
            _aligned(angles, f"title {number}, part {k}")
            loaded.append(angles)
        elif isinstance(part, str):
            loaded.append([_load(part)])
        else:
            raise VtsError(f"title {number}, part {k}: a stream path or Angles")
    frame = loaded[0][0][0].frame
    if any(a[0].frame != frame for part in loaded for a in part):
        raise VtsError(f"title {number}: its parts differ in frame rate")
    fps = _nominal(frame)

    # the chapters, on the title's own time line, each at the nearest VOBU start
    part_start = []             # tick at which each part begins
    tick = 0
    for part in loaded:
        part_start.append(tick)
        st, vs, _ = part[0]
        tick += st.video_end - vs[0].s_ptm
    end = sum(a[0].end - a[1][0].s_ptm for a in (part[0] for part in loaded))
    starts = [(part_start[k] + v.s_ptm - part[0][1][0].s_ptm, k, i)
              for k, part in enumerate(loaded) for i, v in enumerate(part[0][1])]
    chapter_at = {(0, 0)}
    for t in title.chapters:
        if t < 0:
            raise VtsError(f"a chapter at {t} s, before the start")
        want = t * fps * frame
        _, k, i = min(starts, key=lambda s: (abs(s[0] - want), s[1], s[2]))
        chapter_at.add((k, i))
    programs = []               # entry cell of each chapter
    cells = []
    chunks, timeline, vobu_sectors = [], [], []
    ilvu_info = {}              # (part, angle) -> {vobu: ilvu tuple}
    nsml_info = {}
    layouts = {}                # (part, angle) -> [sector of each VOBU]
    first_vob = vob_id
    sector = offset
    jobs = []                   # (part, angle, VOB id) to fill
    preceding = []              # (part, angle, vobu index) of the VOBUs laid before, in order
    for k, part in enumerate(loaded):
        n_angles = len(part)
        vs0 = part[0][1]
        bounds = sorted(i for (kk, i) in chapter_at if kk == k) + [len(vs0)]
        if bounds[0] != 0:
            bounds.insert(0, 0)
        for a in range(n_angles):
            layouts[(k, a)] = [None] * len(vs0)
            ilvu_info[(k, a)] = {}
            nsml_info[(k, a)] = {}
        for seg, (i0, i1) in enumerate(zip(bounds, bounds[1:]), 1):
            is_chapter = (k, i0) in chapter_at
            if is_chapter:
                programs.append(len(cells) + 1)
            if n_angles == 1:
                st, vs, _ = part[0]
                for i in range(i0, i1):
                    layouts[(k, 0)][i] = sector
                    timeline.append((part_start[k] + vs[i].s_ptm - vs[0].s_ptm, sector, len(cells) + 1))
                    vobu_sectors.append(sector)
                    preceding.append((k, 0, i))
                    sector += vs[i].end - vs[i].start + 1
                e = vs[i1].s_ptm if i1 < len(vs) else st.video_end
                lay = layouts[(k, 0)]
                cells.append(Cell(len(cells) + 1, vob_id, seg, i0, i1 - 1, lay[i0], 0, lay[i1 - 1],
                                  lay[i1 - 1] + vs[i1 - 1].end - vs[i1 - 1].start, vs[i0].s_ptm, e,
                                  len(programs), stc_discontinuity=seg == 1, seamless=seg > 1,
                                  segment=(k, seg)))
            else:
                block = parts[k]
                unit = block.ilvu_vobus
                if unit is None:        # as many VOBUs as make a second
                    span = vs0[i0 + 1].s_ptm - vs0[i0].s_ptm if i1 - i0 > 1 else 90000
                    unit = max(1, -(-90000 // span))
                if unit < 1:
                    raise VtsError("an interleaved unit holds at least one VOBU")
                groups = [range(g, min(g + unit, i1)) for g in range(i0, i1, unit)]
                # the three VOBUs before the block: PREU (the first of them
                # Unit_Start, the last Unit_End), when they are not interleaved
                before = [p for p in preceding[-3:] if not isinstance(parts[p[0]], Angles)]
                for n, (pk, pa, pi) in enumerate(before):
                    flags = 0x8000 | (0x2000 if n == 0 else 0) | (0x1000 if n == len(before) - 1 else 0)
                    ilvu_info[(pk, pa)][pi] = (flags, 0, 0, 0, [(0, 0)] * 9)
                # lay the units out, angle by angle within each group
                unit_start = {}         # (angle, group) -> first sector
                unit_size = {}
                for g, rng in enumerate(groups):
                    for a in range(n_angles):
                        vs = part[a][1]
                        unit_start[(a, g)] = sector
                        for i in rng:
                            layouts[(k, a)][i] = sector
                            vobu_sectors.append(sector)
                            if a == 0:
                                timeline.append((part_start[k] + vs[i].s_ptm - vs[0].s_ptm, sector,
                                                 len(cells) + 1))
                            preceding.append((k, a, i))
                            sector += vs[i].end - vs[i].start + 1
                        unit_size[(a, g)] = sector - unit_start[(a, g)]
                for a in range(n_angles):
                    vs = part[a][1]
                    lay = layouts[(k, a)]
                    for g, rng in enumerate(groups):
                        unit_end = unit_start[(a, g)] + unit_size[(a, g)] - 1
                        last_group = g + 1 == len(groups)
                        for i in rng:
                            flags = 0x4000 | (0x2000 if i == rng[0] else 0) | (0x1000 if i == rng[-1] else 0)
                            if last_group:
                                nxt_sa, nxt_sz = 0xFFFFFFFF, 0xFFFF
                            else:
                                nxt_sa, nxt_sz = unit_start[(a, g + 1)] - lay[i], unit_size[(a, g + 1)]
                            agli = []
                            for b in range(9):
                                if b >= n_angles:
                                    agli.append((0, 0))
                                elif last_group:
                                    agli.append((0x7FFFFFFF, 0))
                                else:
                                    agli.append((unit_start[(b, g + 1)] - lay[i], unit_size[(b, g + 1)]))
                            ilvu_info[(k, a)][i] = (flags, unit_end - lay[i], nxt_sa, nxt_sz, agli)
                            nsml = []
                            for b in range(9):
                                if b >= n_angles:
                                    nsml.append(0)
                                else:
                                    d = layouts[(k, b)][i] - lay[i]
                                    nsml.append(d if d >= 0 else 0x80000000 | -d)
                            nsml_info[(k, a)][i] = nsml
                    st = part[a][0]
                    e = vs[i1].s_ptm if i1 < len(vs) else st.video_end
                    first_end = unit_start[(a, 0)] + unit_size[(a, 0)] - 1
                    cells.append(Cell(len(cells) + 1, vob_id + a, seg, i0, i1 - 1, lay[i0], first_end,
                                      lay[i1 - 1], lay[i1 - 1] + vs[i1 - 1].end - vs[i1 - 1].start,
                                      vs[i0].s_ptm, e, len(programs), a + 1, n_angles,
                                      stc_discontinuity=seg == 1, seamless=block.seamless and seg > 1,
                                      interleaved=True, segment=(k, seg)))
        for a in range(n_angles):
            jobs.append((k, a, vob_id + a))
        vob_id += n_angles
    # fill every VOB now that every sector, and every flag of the VOBUs
    # before an angle block, is known
    for k, a, vid in jobs:
        st, vs, vob = loaded[k][a]
        vob_cells = [(vid, c.cell_id, c.first_vobu, c.last_vobu) for c in cells if c.vob_id == vid]
        nav.fill(vob, vob_cells, stream=st, layout=layouts[(k, a)], ilvu=ilvu_info[(k, a)],
                 nsml=nsml_info[(k, a)])
        lay = layouts[(k, a)]
        if len(loaded[k]) == 1:
            chunks.append((lay[0], vob))
        else:
            for i, v in enumerate(vs):
                chunks.append((lay[i], vob[v.start * SECTOR:(v.end + 1) * SECTOR]))
    # a chapter's last cells: those of its last stretch (every angle of it)
    last = {}
    for c in cells:
        last[c.program] = max(last.get(c.program, (0, 0)), c.segment)
    for c in cells:
        c.last_of_program = c.segment == last[c.program]
    first = loaded[0][0]
    w = WrittenTitle(number, first_vob, sector - offset, offset, cells, first[1], first[0],
                     programs=programs, angles=max(len(p) for p in loaded), chunks=chunks,
                     timeline=timeline, end=end, vobu_sectors=vobu_sectors)
    return w, sector, vob_id


def _attributes(written, pan_scan, letterbox, audio, subpictures, what, captions=(False, False)):
    """(video attr, audio count, audio attrs, subpicture count, subpicture
    attrs, sub-streams) of the VOBs: the picture the same in every one, the
    sound and subpicture streams those of the VOB with the most, of which
    every other VOB has the first ones."""
    first = written[0].stream
    video = _video_attributes(first, pan_scan, letterbox, captions)
    audios = [_audio_attributes(w.stream, a) for w, a in zip(written, audio)]
    sps = [_subpicture_attributes(w.stream, sp) for w, sp in zip(written, subpictures)]
    audio_count, audio_bytes = max(audios, key=lambda x: x[0])
    sp_count, sp_bytes, subs = max(sps, key=lambda x: x[0])
    for w, (n, a), (m, sp, _) in zip(written[1:], audios[1:], sps[1:]):
        same = (w.stream.frame == first.frame
                and _video_attributes(w.stream, pan_scan, letterbox, captions) == video
                and a[:8 * n] == audio_bytes[:8 * n] and sp[:6 * m] == sp_bytes[:6 * m])
        if not same:
            raise VtsError(f"{what} {w.number} differs in its streams from {what} 1 "
                           "(one titleset, one set of attributes)")
    return video, audio_count, audio_bytes, sp_count, sp_bytes, subs


def menu_domain(menus, language, offset=0, pan_scan=True, letterbox=True, frame=None,
                video=None):
    """The VOBs and tables of a menu domain (a titleset's or the VMG's):
    (written menus, their bytes, VTSM/VMGM_PGCI_UT, C_ADT, VOBU_ADMAP,
    attributes (video, audio count, audio, subpicture count, subpictures)).
    `frame` (ticks a frame) and `video` (the 2 video attribute bytes) stand
    in when no menu has a stream."""
    lang = _lang(language)
    written, data = [], []
    for n, m in enumerate(menus, 1):
        if not isinstance(m, menus_.Menu):
            raise VtsError(f"menu {n} is not a Menu")
        if m.stream is None:
            menus_.check(m, 1 << 12, 1 << 12)
            written.append(WrittenTitle(n, 0, 0, offset, [], [], None, programs=[], chunks=[],
                                        timeline=[], end=0, vobu_sectors=[]))
            continue
        probe = None
        if m.buttons:
            with open(m.stream, "rb") as f:
                probe = ps.scan(ps.drop_padding(f.read()))
            if probe.sequence is None:
                raise VtsError(f"menu {n}: the video has no sequence header")
            width, height = probe.sequence[0], probe.sequence[1]
            menus_.check(m, width, height)
            unit = menus_.unit(m, width, height)
            s_ptm = nav.vobus(probe)[0].s_ptm
            hli = menus_.hli(m, s_ptm)
        else:
            menus_.check(m, 1 << 12, 1 << 12)
            unit, hli = None, None
        w, vob = _prepare(n, n, m.stream, m.cells, offset, unit, hli)
        written.append(w)
        data.append(vob)
        offset += w.sectors
    kinds = [m.entry for m in menus if m.entry]
    if len(kinds) != len(set(kinds)):
        raise VtsError("two menus answer the same key")
    with_vob = [w for w in written if w.stream is not None]
    if with_vob:
        frame = with_vob[0].stream.frame
        video_attr, audio_count, audio_bytes, sp_count, sp_bytes, subs = _attributes(
            with_vob, pan_scan, letterbox, [()] * len(with_vob), [()] * len(with_vob), "menu")
    else:
        if frame is None or video is None:
            raise VtsError("menus without streams: the frame rate and video attributes are needed")
        video_attr, audio_count, audio_bytes, sp_count, sp_bytes, subs = \
            video, 0, bytes(64), 0, bytes(192), []
    pgcs, cats = [], []
    for m, w in zip(menus, written):
        cat = (0x80 | menus_.ENTRY_TYPES[m.entry]) << 24 if m.entry else 0
        pgcs.append(_pgc((m.pre, m.post or (EXIT,), {}), w, frame, subs, menus_.palette(m), title=False,
                         uops=m.uops, video=video_attr))
        cats.append(cat)
    flags = 0
    for k in kinds:
        flags |= menus_.ENTRY_FLAGS[k]
    if "root" in kinds and "ptt" not in kinds:
        root = next(n for n, m in enumerate(menus, 1) if m.entry == "root")
        pgcs.append(_empty_pgc([struct.pack(">BBHHH", 0x20, 4, 0, 0, root)]))   # LinkPGCN root
        cats.append((0x80 | menus_.ENTRY_TYPES["ptt"]) << 24)
        flags |= menus_.ENTRY_FLAGS["ptt"]
    lu = struct.pack(">HHI", len(pgcs), 0, 0)
    o = 8 + 8 * len(pgcs)
    for cat, p in zip(cats, pgcs):
        lu += struct.pack(">II", cat, o)
        o += len(p)
    lu = lu[:4] + struct.pack(">I", o - 1) + lu[8:] + b"".join(pgcs)
    pgci_ut = struct.pack(">HHI", 1, 0, 8 + 8 + len(lu) - 1) + lang + bytes((0, flags)) \
        + struct.pack(">I", 16) + lu
    c_adt, admap = _address_tables(with_vob)
    attributes = bytearray(0x100)
    attributes[0:2] = video_attr
    struct.pack_into(">H", attributes, 2, audio_count)
    attributes[4:0x44] = audio_bytes
    struct.pack_into(">H", attributes, 0x54, sp_count)
    attributes[0x56:0x5C] = sp_bytes[:6]
    return written, data, pgci_ut, c_adt, admap, bytes(attributes)


def write(folder, vts, titles, menus=(), language="en", pan_scan=True, letterbox=True,
          captions=(False, False)):
    """Write titleset `vts` (1..99) into `folder` (a VIDEO_TS folder): the
    title VOBs, the menu VOB if there are menus, VTS_nn_0.IFO and
    VTS_nn_0.BUP. `language` is the menus' language unit. `pan_scan` and
    `letterbox` say whether a player may show a 16:9 picture that way on a
    4:3 set; `captions` whether the titles' video carries line-21 closed
    captions in field 1 and in field 2."""
    if not 1 <= vts <= 99:
        raise VtsError(f"titleset {vts}: titlesets are numbered 1 to 99")
    if not titles or len(titles) > 99:
        raise VtsError("a titleset holds 1 to 99 titles")
    written = []
    offset, vob_id = 0, 1
    for n, title in enumerate(titles, 1):
        w, offset, vob_id = _title(n, title, offset, vob_id)
        written.append(w)
    frame = written[0].stream.frame
    video, audio_count, audio, sp_count, subpictures, subs = _attributes(
        written, pan_scan, letterbox, [t.audio for t in titles], [t.subpictures for t in titles],
        "title", captions)
    menu_written, menu_data, menu_tables, menu_attributes = [], [], None, bytes(0x100)
    if menus:
        # (menus of commands alone take the titles' picture format)
        menu_written, menu_data, *menu_tables, menu_attributes = menu_domain(
            menus, language, 0, pan_scan, letterbox, frame, video)
    menu_sectors = sum(w.sectors for w in menu_written)

    # the tables
    ptt = _table(lambda n, end: struct.pack(">HHI", n, 0, end), 8,
                 [b"".join(struct.pack(">HH", w.number, k) for k in range(1, len(w.programs) + 1))
                  for w in written])
    pgcs = [_pgc((t.pre, t.post, t.cell_commands), w, frame, sorted(w.stream.subpictures), t.palette,
                 stills=t.stills, uops=t.uops, video=video) for t, w in zip(titles, written)]
    pgci = struct.pack(">HHI", len(pgcs), 0, 0)
    o = 8 + 8 * len(pgcs)
    for w, p in zip(written, pgcs):
        pgci += struct.pack(">II", 0x80000000 | w.number << 24, o)
        o += len(p)
    pgci = pgci[:4] + struct.pack(">I", o - 1) + pgci[8:] + b"".join(pgcs)
    tmapt = _table(lambda n, end: struct.pack(">HHI", n, 0, end), 8,
                   [_time_map(w, frame) for w in written])
    c_adt, admap = _address_tables(written)

    order = [("ptt", ptt), ("pgci", pgci)]
    if menus:
        order.append(("vtsm_pgci_ut", menu_tables[0]))
    order.append(("tmapt", tmapt))
    if menus:
        order += [("vtsm_c_adt", menu_tables[1]), ("vtsm_admap", menu_tables[2])]
    order += [("c_adt", c_adt), ("admap", admap)]
    tables = [_pad(t) for _, t in order]
    sectors, pointers = 1, {}
    for (name, _), t in zip(order, tables):
        pointers[name] = sectors
        sectors += len(t) // SECTOR
    vob_sectors = offset
    mat = bytearray(SECTOR)
    mat[:12] = b"DVDVIDEO-VTS"
    struct.pack_into(">I", mat, 0x0C, 2 * sectors + menu_sectors + vob_sectors - 1)
    struct.pack_into(">I", mat, 0x1C, sectors - 1)
    struct.pack_into(">H", mat, 0x20, 0x0011)
    struct.pack_into(">I", mat, 0x80, 0x7FF)
    struct.pack_into(">I", mat, 0xC0, sectors if menus else 0)
    struct.pack_into(">I", mat, 0xC4, sectors + menu_sectors)
    struct.pack_into(">IIIIIIII", mat, 0xC8, pointers["ptt"], pointers["pgci"],
                     pointers.get("vtsm_pgci_ut", 0), pointers["tmapt"],
                     pointers.get("vtsm_c_adt", 0), pointers.get("vtsm_admap", 0),
                     pointers["c_adt"], pointers["admap"])
    mat[0x100:0x200] = menu_attributes
    mat[0x200:0x202] = video
    struct.pack_into(">H", mat, 0x202, audio_count)
    mat[0x204:0x244] = audio
    struct.pack_into(">H", mat, 0x254, sp_count)
    mat[0x256:0x316] = subpictures
    ifo = bytes(mat) + b"".join(tables)

    os.makedirs(folder, exist_ok=True)
    files = []
    for name in (f"VTS_{vts:02d}_0.IFO", f"VTS_{vts:02d}_0.BUP"):
        with open(os.path.join(folder, name), "wb") as f:
            f.write(ifo)
        files.append(name)
    if menu_sectors:
        if menu_sectors > VOB_FILE_SECTORS:
            raise VtsError("the menus are larger than one VOB file")
        name = f"VTS_{vts:02d}_0.VOB"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"".join(menu_data))
        files.append(name)
    files += _write_vobs(folder, vts, written)
    for t, w in zip(titles, written):
        w.uops = t.uops
    return Titleset(vts, folder, sectors, vob_sectors, written, ifo[0x100:0x400], files, menu_sectors)


def _address_tables(written):
    """VTS(M)_C_ADT (the cells by VOB and cell number, the VOBs counted) and
    VTS(M)_VOBU_ADMAP (every VOBU's sector, in order)."""
    cells = sorted((c for w in written for c in w.cells), key=lambda c: (c.vob_id, c.cell_id))
    vobs = len({c.vob_id for c in cells})
    c_adt = struct.pack(">HHI", vobs, 0, 8 + 12 * len(cells) - 1)
    for c in cells:
        c_adt += struct.pack(">HBBII", c.vob_id, c.cell_id, 0, c.first_sector, c.last_sector)
    admap = sorted(s for w in written for s in w.vobu_sectors)
    admap = struct.pack(">I", 4 + 4 * len(admap) - 1) + struct.pack(f">{len(admap)}I", *admap)
    return c_adt, admap


def _write_vobs(folder, vts, written):
    """The title VOBs as VTS_nn_1.VOB, VTS_nn_2.VOB, ... each at most
    VOB_FILE_SECTORS, cut at VOBU starts."""
    starts = sorted(s for w in written for s in w.vobu_sectors)
    total = written[-1].first_sector + written[-1].sectors
    cuts = [0]
    while total - cuts[-1] > VOB_FILE_SECTORS:
        fit = [s for s in starts if cuts[-1] < s <= cuts[-1] + VOB_FILE_SECTORS]
        if not fit:
            raise VtsError("a VOBU is larger than a VOB file")
        cuts.append(fit[-1])
    cuts.append(total)
    if len(cuts) - 1 > 9:
        raise VtsError("the titles need more than 9 VOB files (over 9 GiB)")
    chunks = sorted((c for w in written for c in w.chunks), key=lambda c: c[0])
    whole = b"".join(bytes(c[1]) for c in chunks)       # (a copy; titles are read whole as it is)
    if len(whole) != total * SECTOR:
        raise VtsError("the VOBUs do not fill the title VOBs")
    names = []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:]), 1):
        name = f"VTS_{vts:02d}_{k}.VOB"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(whole[a * SECTOR:b * SECTOR])
        names.append(name)
    return names
