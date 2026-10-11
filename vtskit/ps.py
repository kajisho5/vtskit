"""Read a DVD program stream: its packs, VOBUs, pictures and sound.

The input is what oncemore's encoder makes (ffmpeg `-f dvd`): 2048-byte
packs, each VOBU starting with a navigation pack whose PCI and DSI are
still empty. This module only reads; `vtskit.nav` fills the navigation
packs from what is read here.

`scan` reads a stream in one pass, keeping only what the navigation packs
need, so a whole title (gigabytes, given as an mmap) is never copied.

The MPEG-2 headers are those of ISO/IEC 13818-1 (pack, system and PES
headers) and 13818-2 (sequence, GOP and picture headers); the DVD's own
use of private streams is described at https://dvdnav.mplayerhq.hu/dvdinfo/.
"""

from .errors import VtsError

SECTOR = 2048

# private stream 1 sub-streams
SUB_SPU = range(0x20, 0x40)
SUB_AC3 = range(0x80, 0x88)
SUB_DTS = range(0x88, 0x90)
SUB_LPCM = range(0xA0, 0xA8)

# the start codes that end a picture's data
_PICTURE, _SEQ, _EXT, _GOP, _SEQ_END = 0x00, 0xB3, 0xB5, 0xB8, 0xB7

# frame_rate_code: 90 kHz ticks per frame
_FRAME = {1: 3753.75, 2: 3750, 3: 3600, 4: 3003, 5: 3000, 6: 1800, 7: 1501.5, 8: 1500}


def _pts(b, o):
    return ((b[o] >> 1) & 7) << 30 | b[o + 1] << 22 | (b[o + 2] >> 1) << 15 | \
        b[o + 3] << 7 | b[o + 4] >> 1


class Pes:
    """One PES packet in a pack."""
    __slots__ = ("sid", "sub", "pts", "start", "end", "frames")

    def __init__(self, sid, sub, pts, start, end, frames):
        self.sid, self.sub, self.pts = sid, sub, pts
        self.start, self.end = start, end          # the payload, as byte offsets in the stream
        self.frames = frames                       # (audio) frames that begin in this packet


class Pack:
    __slots__ = ("sector", "scr", "nav", "pes")

    def __init__(self, sector, scr, nav, pes):
        self.sector, self.scr, self.nav, self.pes = sector, scr, nav, pes


def iter_packs(data):
    """The packs of a stream (bytes, a bytearray or an mmap), one by one."""
    if len(data) % SECTOR:
        raise VtsError(f"the stream is not whole 2048-byte packs ({len(data)} bytes)")
    for i in range(len(data) // SECTOR):
        base = i * SECTOR
        p = data[base:base + SECTOR]
        if p[:4] != b"\x00\x00\x01\xba" or (p[4] >> 6) != 1:
            raise VtsError(f"sector {i} is not an MPEG-2 pack")
        scr = ((p[4] >> 3) & 7) << 30 | (p[4] & 3) << 28 | p[5] << 20 | \
            (p[6] >> 3) << 15 | (p[6] & 3) << 13 | p[7] << 5 | p[8] >> 3
        o = 14 + (p[13] & 7)
        pes, nav = [], False
        while o + 6 <= SECTOR and p[o:o + 3] == b"\x00\x00\x01":
            sid = p[o + 3]
            n = int.from_bytes(p[o + 4:o + 6], "big")
            end = o + 6 + n
            if end > SECTOR:
                raise VtsError(f"sector {i}: a packet runs past the pack")
            if sid == 0xBF:
                nav = True
            elif sid == 0xE0 or sid == 0xBD or 0xC0 <= sid <= 0xDF:
                pts = _pts(p, o + 9) if p[o + 7] & 0x80 else None
                start = o + 9 + p[o + 8]
                sub, frames = None, None
                if sid == 0xBD:
                    sub = p[start]
                    if sub in SUB_AC3 or sub in SUB_DTS or sub in SUB_LPCM:
                        frames = p[start + 1]
                    start += 1
                pes.append(Pes(sid, sub, pts, base + start, base + end, frames))
            o = end
        yield Pack(i, scr, nav, pes)


def read_packs(data):
    """Every pack of the stream, as a list (for short streams)."""
    return list(iter_packs(data))


def drop_padding(data):
    """The stream without its packs of padding alone (spumux leaves some;
    a VOB has none). Every other pack keeps its order."""
    out = bytearray()
    for i in range(len(data) // SECTOR):
        p = data[i * SECTOR:(i + 1) * SECTOR]
        o = 14 + (p[13] & 7)
        only_padding = True
        while o + 6 <= SECTOR and p[o:o + 3] == b"\x00\x00\x01":
            if p[o + 3] != 0xBE:
                only_padding = False
                break
            o += 6 + int.from_bytes(p[o + 4:o + 6], "big")
        if not only_padding:
            out += p
    return out


class Picture:
    __slots__ = ("type", "start", "end", "pts", "fields", "order")

    def __init__(self, type_, start, pts, order):
        self.type, self.start, self.pts = type_, start, pts
        self.end = None          # sector of the picture's last byte
        self.fields = 2
        self.order = order       # temporal_reference: its place in its group's display order


class _Video:
    """Find the pictures in the video packets as they come, start codes
    split between packets included."""

    def __init__(self):
        self.pictures = []
        self.groups = []             # the pictures of each group of pictures
        self.frame = None
        self.sequence = None         # (width, height, aspect_ratio_information, frame_rate_code)
        self.tail = b""              # unread bytes of the packets before
        self.tail_sectors = []       # the sector of each
        self.before = None           # the sector of the byte before the tail
        self.pending_pts = None      # the PTS of the packet the tail's first start code is in
        self.last_sector = None

    def feed(self, chunk, sector, pts):
        buf = self.tail + chunk
        n_tail = len(self.tail)
        secs = self.tail_sectors

        def sector_of(pos):
            if pos < 0:
                return self.before
            return secs[pos] if pos < n_tail else sector

        used = False
        k = 0
        while True:
            k = buf.find(b"\x00\x00\x01", k)
            if k < 0:
                resume = max(len(buf) - 2, 0)        # a start code may begin in the last bytes
                break
            if k + 9 > len(buf):                     # read it when the rest has come
                resume = k
                break
            code = buf[k + 3]
            if code in (_PICTURE, _SEQ, _GOP, _SEQ_END) and self.pictures and \
                    self.pictures[-1].end is None:
                self.pictures[-1].end = sector_of(k - 1)
            if code == _SEQ and self.frame is None:
                self.frame = _FRAME.get(buf[k + 7] & 0x0F)
                self.sequence = (buf[k + 4] << 4 | buf[k + 5] >> 4,
                                 (buf[k + 5] & 0x0F) << 8 | buf[k + 6],
                                 buf[k + 7] >> 4, buf[k + 7] & 0x0F)
            elif code == _GOP:
                self.groups.append([])
            elif code == _PICTURE:
                t = "?IPB"[(buf[k + 5] >> 3) & 7]
                # a packet's PTS belongs to the first picture that starts in it
                if k < n_tail:
                    own, self.pending_pts = self.pending_pts, None
                elif pts is not None and not used:
                    own, used = pts, True
                else:
                    own = None
                picture = Picture(t, sector_of(k), own, buf[k + 4] << 2 | buf[k + 5] >> 6)
                if not self.groups or t == "I" and self.groups[-1]:
                    self.groups.append([])           # (a group without its header)
                self.groups[-1].append(picture)
                self.pictures.append(picture)
            elif code == _EXT and self.pictures and (buf[k + 4] >> 4) == 8:
                self.pictures[-1].fields = 3 if buf[k + 7] & 0x02 else 2
            k += 3
        self.before = sector_of(resume - 1)
        if resume >= n_tail:
            self.pending_pts = pts if not used else None
        self.tail = buf[resume:]
        self.tail_sectors = [sector_of(j) for j in range(resume, len(buf))]
        self.last_sector = sector

    def close(self):
        if self.pictures and self.pictures[-1].end is None:
            self.pictures[-1].end = self.last_sector
        if self.pictures and self.frame is None:
            raise VtsError("the video has no sequence header")
        self._time_pictures()

    def _time_pictures(self):
        """A picture whose packet carries no PTS (ffmpeg's muxer may give one
        to the first picture of a packet only) is shown at its place in its
        group's display order: temporal_reference counts the pictures of
        the group from the first shown, each lasting its fields."""
        for group in self.groups:
            shown = sorted(group, key=lambda p: p.order)
            known = next((p for p in shown if p.pts is not None), None)
            if known is None or all(p.pts is not None for p in shown):
                continue
            at, times = 0, {}
            for p in shown:
                times[id(p)] = at
                at += p.fields * self.frame / 2
            base = known.pts - times[id(known)]
            for p in shown:
                if p.pts is None:
                    p.pts = int(round(base + times[id(p)]))


class _Subpictures:
    """Collect the subpicture units of each stream as their packets come."""

    def __init__(self):
        self.units = {}              # sub-stream -> [(first sector, shown from, shown until)]
        self.current = {}            # sub-stream -> [first sector, pts, bytes]

    def feed(self, sub, chunk, sector, pts):
        unit = self.current.get(sub)
        if unit is None or pts is not None:
            if pts is None:
                raise VtsError(f"sector {sector}: a subpicture unit without a PTS")
            unit = self.current[sub] = [sector, pts, bytearray()]
        unit[2] += chunk
        size = int.from_bytes(unit[2][:2], "big") if len(unit[2]) >= 2 else 0
        if size and len(unit[2]) >= size:
            self.units.setdefault(sub, []).append(_spu_times(*unit))
            del self.current[sub]


def _spu_times(sector, pts, spu):
    """When a subpicture unit is on screen: its PTS plus the delays (in
    units of 1024 ticks) of its start and stop display commands
    (dvdinfo spu.html)."""
    o = int.from_bytes(spu[2:4], "big")
    start = stop = None
    seen = set()
    while o + 4 <= len(spu) and o not in seen:
        seen.add(o)
        delay = int.from_bytes(spu[o:o + 2], "big") * 1024
        nxt = int.from_bytes(spu[o + 2:o + 4], "big")
        k = o + 4
        while k < len(spu):
            cmd = spu[k]
            if cmd == 0xFF:
                break
            if cmd in (0x00, 0x01) and start is None:
                start = pts + delay
            elif cmd == 0x02 and stop is None:
                stop = pts + delay
            if cmd == 0x07:                  # CHG_COLCON: its size word counts itself
                k += 1 + int.from_bytes(spu[k + 1:k + 3], "big")
            else:
                k += {0x03: 3, 0x04: 3, 0x05: 7, 0x06: 5}.get(cmd, 1)
        if nxt == o:
            break
        o = nxt
    return sector, start if start is not None else pts, stop


class Stream:
    """What a navigation pack and a titleset need to know of a stream."""

    def __init__(self):
        self.sectors = 0
        self.navs = []               # (sector, SCR) of each navigation pack
        self.pictures = []
        self.frame = None            # 90 kHz ticks per frame
        self.sequence = None         # (width, height, aspect_ratio_information, frame_rate_code)
        self.audio = {}              # (stream id, sub-stream) -> [(sector, pts, frames)]
        self.mpeg_audio = {}         # stream id -> [bytes so far, first 4 bytes]
        self.subpictures = {}        # sub-stream -> [(first sector, shown from, until)]

    @property
    def video_end(self):
        """When the last picture has been shown."""
        ends = [p.pts + p.fields * self.frame / 2 for p in self.pictures if p.pts is not None]
        return int(max(ends)) if ends else None

    def audio_end(self, key):
        """When a sound stream ends (None when unknown)."""
        if key[0] != 0xBD:
            return mpeg_audio_end(self.audio[key], self.mpeg_audio.get(key[0]))
        return audio_end(self.audio[key], key)

    @property
    def end(self):
        """When the last of the video and the sound ends."""
        ends = [self.video_end] + [self.audio_end(k) for k in self.audio]
        return max(e for e in ends if e is not None)


def scan(data):
    """Read a stream (bytes, a bytearray or an mmap) in one pass."""
    out = Stream()
    video, spu = _Video(), _Subpictures()
    for pk in iter_packs(data):
        out.sectors += 1
        if pk.nav:
            out.navs.append((pk.sector, pk.scr))
        for e in pk.pes:
            if e.sid == 0xE0:
                video.feed(bytes(data[e.start:e.end]), pk.sector, e.pts)
            elif e.sid == 0xBD and e.sub in SUB_SPU:
                spu.feed(e.sub, data[e.start:e.end], pk.sector, e.pts)
            elif e.sid == 0xBD or 0xC0 <= e.sid <= 0xDF:
                key = (0xBD, e.sub) if e.sid == 0xBD else (e.sid, None)
                out.audio.setdefault(key, []).append((pk.sector, e.pts, e.frames))
                if e.sid != 0xBD:
                    m = out.mpeg_audio.setdefault(e.sid, [0, bytes(data[e.start:e.start + 4])])
                    m[0] += e.end - e.start
    video.close()
    out.pictures, out.frame = video.pictures, video.frame
    out.sequence = video.sequence
    out.subpictures = spu.units
    return out


def audio_streams(packs):
    """{stream key: [(sector, pts, frames)]} of each sound stream's packets
    in a list of packs; the key is (0xBD, sub-stream) or (stream id, None)."""
    out = {}
    for pk in packs:
        for e in pk.pes:
            if e.sid == 0xBD and e.sub not in SUB_SPU:
                out.setdefault((0xBD, e.sub), []).append((pk.sector, e.pts, e.frames))
            elif 0xC0 <= e.sid <= 0xDF:
                out.setdefault((e.sid, None), []).append((pk.sector, e.pts, None))
    return out


def audio_frame_ticks(key):
    """90 kHz ticks per audio frame of a sub-stream (the frames counted in
    its packet headers); None for MPEG sound, whose packets do not count
    them."""
    sid, sub = key
    if sid == 0xBD and sub in SUB_AC3:
        return 2880                  # 1536 samples at 48 kHz
    if sid == 0xBD and sub in SUB_DTS:
        return 960                   # 512 samples at 48 kHz
    if sid == 0xBD and sub in SUB_LPCM:
        return 150                   # 1/600 s
    return None


def audio_end(packets, key):
    """When a sound stream ends: its last stamped packet's PTS plus the
    frames that begin there and after (None for MPEG sound)."""
    tick = audio_frame_ticks(key)
    if tick is None:
        return None
    last, frames = None, 0
    for _, pts, n in packets:
        if pts is not None:
            last, frames = pts, 0
        frames += n or 0
    return None if last is None else last + frames * tick


# MPEG-1 audio (ISO/IEC 11172-3) frame header: bit rates in kbit/s by layer
_MPEG_BITRATES = {
    3: (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),   # layer I
    2: (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),      # layer II
}
_MPEG_RATES = (44100, 48000, 32000)


def mpeg_audio_end(packets, counted):
    """When an MPEG audio stream ends: its first stamped packet's PTS plus
    the frames its bytes hold (`counted`: [bytes, the first frame header]),
    at the constant bit rate of that header. None when nothing is known."""
    if not counted or counted[0] == 0:
        return None
    first = next((pts for _, pts, _ in packets if pts is not None), None)
    if first is None:
        return None
    h = counted[1]
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xF8) != 0xF8:
        raise VtsError("MPEG audio that is not MPEG-1 (the only kind a DVD carries)")
    layer = (h[1] >> 1) & 3
    bitrate = _MPEG_BITRATES.get(layer, (0,) * 16)[h[2] >> 4] * 1000
    rate_index = (h[2] >> 2) & 3
    if layer not in _MPEG_BITRATES or not bitrate or rate_index > 2:
        raise VtsError("an MPEG audio frame header vtskit cannot read")
    rate = _MPEG_RATES[rate_index]
    samples = 384 if layer == 3 else 1152
    size = samples // 8 * bitrate // rate
    return first + round(counted[0] / size) * samples * 90000 // rate
