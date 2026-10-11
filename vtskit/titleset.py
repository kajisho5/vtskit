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
  one; vtskit keeps it); a program chain ends with the Exit command when it
  has no post commands of its own; a cell's command number is in the
  fourth byte of its category;
* the audio attributes' second byte is 0xC0 for AC-3 and DTS, 0x40 for
  MPEG and 0x00 for 16-bit LPCM, plus the channels less one;
* a 4:3 title forbids automatic pan-scan and letterbox (both are moot).

With menus (vtskit.menu), the titleset also holds VTS_nn_0.VOB (every
menu's VOB with its subpicture overlay and highlight information), and the
IFO its VTSM_PGCI_UT (one language unit, a program chain a menu, the entry
menus marked), VTSM_C_ADT and VTSM_VOBU_ADMAP, and the menus' attributes.
As dvdauthor does, a titleset with a root menu and no chapter (PTT) menu
gets an empty chapter-menu program chain that links to the root.

Not checked against dvdauthor (it needs a title over 1 GiB): the VOBs are
cut into files of at most 524272 sectors, at VOBU starts.
"""

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
class Title:
    """A title: its program stream (ffmpeg -f dvd, as oncemore encodes),
    chapter times in seconds from its start (the first chapter is at 0
    whether listed or not), an Audio for each sound stream in the order they
    first appear, a Subpicture for each subpicture stream, the 16 colours
    its subpictures choose from as (Y, Cr, Cb) (black where not given), and
    its program chain's commands: `pre` and `post` as lists of 8-byte
    commands (`vtskit.vm.assemble` makes them), `cell_commands` as
    {chapter number: one 8-byte command run when that chapter's cell ends}."""
    stream: str
    chapters: tuple = (0.0,)
    audio: tuple = ()
    subpictures: tuple = ()
    palette: tuple = ()
    pre: tuple = ()
    post: tuple = (EXIT,)
    cell_commands: dict = field(default_factory=dict)


@dataclass
class Cell:
    number: int
    first_vobu: int
    last_vobu: int
    first_sector: int       # in the title VOBs
    last_vobu_sector: int
    last_sector: int
    s_ptm: int
    e_ptm: int              # the next cell's start, or the title's video end


@dataclass
class WrittenTitle:
    """A title's (or a menu's) VOB as written."""
    number: int             # in the titleset (a menu: its program chain number)
    vob_id: int
    sectors: int
    first_sector: int
    cells: list
    vobus: list             # nav.Vobu, sectors counted from this VOB's start
    stream: object


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


def _video_attributes(stream, pan_scan, letterbox):
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
    return bytes((byte0, sizes[(width, height)] << 3))


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


def _pgc(commands, written, frame, subs, palette, programs=None, title=True):
    """One program chain: `commands` = (pre, post, {cell number: command});
    `programs` the entry cell of each program (default: a program a cell)."""
    pre, post, cell_commands = commands
    cells = written.cells
    for n in cell_commands:
        if not 1 <= n <= len(cells):
            raise VtsError(f"a cell command for cell {n}; there are {len(cells)}")
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
    programs = list(programs) if programs is not None else list(range(1, len(cells) + 1))
    head = bytearray(0xEC)
    head[2], head[3] = len(programs), len(cells)
    head[4:8] = nav.elapsed(cells[-1].e_ptm - cells[0].s_ptm, frame)
    # streams: available, numbered as the VOB numbers them
    for n, key in enumerate(written.stream.audio):
        number = key[1] & 7 if key[0] == 0xBD else key[0] & 7
        struct.pack_into(">H", head, 0x0C + 2 * n, 0x8000 | number << 8)
    for n, sub in enumerate(subs):
        struct.pack_into(">I", head, 0x1C + 4 * n, (0x80 | (sub - 0x20)) << 24)
    head[0xA4:0xE4] = _palette(palette)
    table = struct.pack(">HHHH", len(pre), len(post), len(cell_list),
                        8 + 8 * (len(pre) + len(post) + len(cell_list)) - 1)
    table += b"".join((*pre, *post, *cell_list))
    command_of = {n: k for k, n in enumerate(sorted(cell_commands), 1)}
    program_map = bytes(programs)
    program_map += b"\0" * (len(program_map) % 2)
    playback = bytearray()
    for c in cells:
        category = 0x02 if c.number == 1 else 0x08      # STC discontinuity / seamless
        playback += struct.pack(">BBBB4sIIII", category, 0, 0, command_of.get(c.number, 0),
                                nav.elapsed(c.e_ptm - c.s_ptm, frame), c.first_sector, 0,
                                c.last_vobu_sector, c.last_sector)
    position = b"".join(struct.pack(">HBB", written.vob_id, 0, c.number) for c in cells)
    o = len(head)
    struct.pack_into(">HHHH", head, 0xE4, o, o + len(table), o + len(table) + len(program_map),
                     o + len(table) + len(program_map) + len(playback))
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
    vs = written.vobus
    start = vs[0].s_ptm
    fps = _nominal(frame)
    cell_of = {}
    for c in written.cells:
        for i in range(c.first_vobu, c.last_vobu + 1):
            cell_of[i] = c.number
    picks = []
    t = 1
    while t * fps * frame < vs[-1].e_ptm - start:
        tick = start + t * fps * frame
        i = max(i for i, v in enumerate(vs) if v.s_ptm <= tick)
        picks.append(i)
        t += 1
    entries = []
    for k, i in enumerate(picks):
        last = k + 1 == len(picks) or cell_of[picks[k + 1]] != cell_of[i]
        entries.append((0x80000000 if last else 0) | (written.first_sector + vs[i].start))
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
        cells.append(Cell(c, a, b - 1, offset + vs[a].start, offset + vs[b - 1].start,
                          offset + vs[b - 1].end, vs[a].s_ptm, end))
    nav.fill(vob, [(vob_id, c.number, c.first_vobu, c.last_vobu) for c in cells], offset,
             stream=stream, hli={vob_id: hli} if hli else None)
    return WrittenTitle(number, vob_id, stream.sectors, offset, cells, vs, stream), vob


def _attributes(written, pan_scan, letterbox, audio, subpictures, what):
    """(video attr, audio count, audio attrs, subpicture count, subpicture
    attrs, sub-streams) of the first VOB; every other must agree."""
    first = written[0].stream
    video = _video_attributes(first, pan_scan, letterbox)
    audio_count, audio_bytes = _audio_attributes(first, audio[0])
    sp_count, sp_bytes, subs = _subpicture_attributes(first, subpictures[0])
    for w, a, sp in zip(written[1:], audio[1:], subpictures[1:]):
        same = (w.stream.frame == first.frame
                and _video_attributes(w.stream, pan_scan, letterbox) == video
                and _audio_attributes(w.stream, a) == (audio_count, audio_bytes)
                and _subpicture_attributes(w.stream, sp)[:2] == (sp_count, sp_bytes))
        if not same:
            raise VtsError(f"{what} {w.number} differs in its streams from {what} 1 "
                           "(one titleset, one set of attributes)")
    return video, audio_count, audio_bytes, sp_count, sp_bytes, subs


def menu_domain(menus, language, offset=0, pan_scan=True, letterbox=True):
    """The VOBs and tables of a menu domain (a titleset's or the VMG's):
    (written menus, their bytes, VTSM/VMGM_PGCI_UT, C_ADT, VOBU_ADMAP,
    attributes (video, audio count, audio, subpicture count, subpictures))."""
    lang = _lang(language)
    written, data = [], []
    hlis = []
    for n, m in enumerate(menus, 1):
        if not isinstance(m, menus_.Menu):
            raise VtsError(f"menu {n} is not a Menu")
        probe = None
        if m.buttons:
            with open(m.stream, "rb") as f:
                probe = ps.scan(ps.drop_padding(f.read()))
            if probe.sequence is None:
                raise VtsError(f"menu {n}: the video has no sequence header")
            width, height = probe.sequence[0], probe.sequence[1]
            menus_.check(m, width, height)
            unit = spu.encode(menus_.overlay(m, width, height))
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
    frame = written[0].stream.frame
    video, audio_count, audio_bytes, sp_count, sp_bytes, subs = _attributes(
        written, pan_scan, letterbox, [()] * len(menus), [()] * len(menus), "menu")
    pgcs, cats = [], []
    for m, w in zip(menus, written):
        cat = (0x80 | menus_.ENTRY_TYPES[m.entry]) << 24 if m.entry else 0
        pgcs.append(_pgc((m.pre, m.post, {}), w, frame, subs, menus_.palette(m), programs=[1],
                         title=False))
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
    c_adt = struct.pack(">HHI", len(written), 0, 8 + 12 * sum(len(w.cells) for w in written) - 1)
    for w in written:
        for c in w.cells:
            c_adt += struct.pack(">HBBII", w.vob_id, c.number, 0, c.first_sector, c.last_sector)
    admap = [w.first_sector + v.start for w in written for v in w.vobus]
    admap = struct.pack(">I", 4 + 4 * len(admap) - 1) + struct.pack(f">{len(admap)}I", *admap)
    attributes = bytearray(0x100)
    attributes[0:2] = video
    struct.pack_into(">H", attributes, 2, audio_count)
    attributes[4:0x44] = audio_bytes
    struct.pack_into(">H", attributes, 0x54, sp_count)
    attributes[0x56:0x5C] = sp_bytes[:6]
    return written, data, pgci_ut, c_adt, admap, bytes(attributes)


def write(folder, vts, titles, menus=(), language="en", pan_scan=True, letterbox=True):
    """Write titleset `vts` (1..99) into `folder` (a VIDEO_TS folder): the
    title VOBs, the menu VOB if there are menus, VTS_nn_0.IFO and
    VTS_nn_0.BUP. `language` is the menus' language unit. `pan_scan` and
    `letterbox` say whether a player may show a 16:9 picture that way on a
    4:3 set."""
    if not 1 <= vts <= 99:
        raise VtsError(f"titleset {vts}: titlesets are numbered 1 to 99")
    if not titles or len(titles) > 99:
        raise VtsError("a titleset holds 1 to 99 titles")
    menu_written, menu_data, menu_tables, menu_attributes = [], [], None, bytes(0x100)
    if menus:
        menu_written, menu_data, *menu_tables, menu_attributes = menu_domain(
            menus, language, 0, pan_scan, letterbox)
    menu_sectors = sum(w.sectors for w in menu_written)

    written, data = [], []
    offset = 0
    for n, title in enumerate(titles, 1):
        w, vob = _prepare(n, n, title.stream, title.chapters, offset)
        written.append(w)
        data.append(vob)
        offset += w.sectors
    frame = written[0].stream.frame
    video, audio_count, audio, sp_count, subpictures, subs = _attributes(
        written, pan_scan, letterbox, [t.audio for t in titles], [t.subpictures for t in titles],
        "title")

    # the tables
    ptt = _table(lambda n, end: struct.pack(">HHI", n, 0, end), 8,
                 [b"".join(struct.pack(">HH", w.number, c.number) for c in w.cells) for w in written])
    pgcs = [_pgc((t.pre, t.post, t.cell_commands), w, frame, subs, t.palette)
            for t, w in zip(titles, written)]
    pgci = struct.pack(">HHI", len(pgcs), 0, 0)
    o = 8 + 8 * len(pgcs)
    for w, p in zip(written, pgcs):
        pgci += struct.pack(">II", 0x80000000 | w.number << 24, o)
        o += len(p)
    pgci = pgci[:4] + struct.pack(">I", o - 1) + pgci[8:] + b"".join(pgcs)
    tmapt = _table(lambda n, end: struct.pack(">HHI", n, 0, end), 8,
                   [_time_map(w, frame) for w in written])
    c_adt = struct.pack(">HHI", len(written), 0, 8 + 12 * sum(len(w.cells) for w in written) - 1)
    for w in written:
        for c in w.cells:
            c_adt += struct.pack(">HBBII", w.vob_id, c.number, 0, c.first_sector, c.last_sector)
    admap = [w.first_sector + v.start for w in written for v in w.vobus]
    admap = struct.pack(">I", 4 + 4 * len(admap) - 1) + struct.pack(f">{len(admap)}I", *admap)

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
    if menus:
        if menu_sectors > VOB_FILE_SECTORS:
            raise VtsError("the menus are larger than one VOB file")
        name = f"VTS_{vts:02d}_0.VOB"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(b"".join(menu_data))
        files.append(name)
    files += _write_vobs(folder, vts, data, written)
    return Titleset(vts, folder, sectors, vob_sectors, written, ifo[0x100:0x400], files, menu_sectors)


def _write_vobs(folder, vts, data, written):
    """The title VOBs as VTS_nn_1.VOB, VTS_nn_2.VOB, ... each at most
    VOB_FILE_SECTORS, cut at VOBU starts."""
    starts = [w.first_sector + v.start for w in written for v in w.vobus]
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
    whole = b"".join(data)       # (a copy; titles are read whole as it is)
    names = []
    for k, (a, b) in enumerate(zip(cuts, cuts[1:]), 1):
        name = f"VTS_{vts:02d}_{k}.VOB"
        with open(os.path.join(folder, name), "wb") as f:
            f.write(whole[a * SECTOR:b * SECTOR])
        names.append(name)
    return names
