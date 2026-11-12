"""A bitrate plan for a long programme on one DVD, and its chapters.

Given a programme of sessions (a talk, a panel, a concert set) as a chapter
list and its length, `plan` says which disc holds it (DVD-5 or DVD-9), at
what average video bitrate, whether that bitrate is at or above a stated
quality floor, and, when it is not, by how much the programme is too long
for the disc at that floor. The chapters come out as the seconds a
`vtskit.titleset.Title` takes, one chapter a session.

The plan is arithmetic only; it reads and writes nothing. The encoder
(ffmpeg, oncemore) is given the video bitrate; vtskit then places each
chapter at the VOBU start nearest its time, so a chapter lands within about
half a second of the time listed.

The model, in order (each figure is a parameter):

* the disc's size, the smaller of the recordable discs of its kind
  (DVD-5: DVD+R, 2,295,104 sectors; DVD-9: DVD-R DL, 4,171,712 sectors),
  less a reserve for the file system, the IFO files and the menus;
* a safety margin, as a variable-bitrate encoder lands near its average,
  not on it;
* one navigation pack (2048 bytes) a VOBU, a VOBU every half second;
* the pack and PES headers of the other packs;
* the sound and subtitle streams' bitrates;
* what is left is the video's average, capped where DVD-Video caps it
  (9.8 Mbit/s of video, 10.08 Mbit/s for the whole stream).
"""

import math
import re
from dataclasses import dataclass, field

from .errors import VtsError

SECTOR = 2048


@dataclass(frozen=True)
class Disc:
    name: str
    sectors: int

    @property
    def bytes(self):
        return self.sectors * SECTOR


DISCS = {
    "dvd5": Disc("DVD-5", 2_295_104),
    "dvd9": Disc("DVD-9", 4_171_712),
}

MAX_VIDEO = 9_800_000          # bit/s, the video's peak in DVD-Video
MAX_MUX = 10_080_000           # bit/s, the whole program stream
DEFAULT_FLOOR = 3_500_000      # bit/s: full-size (720 wide) MPEG-2 at or above it
DEFAULT_AUDIO = 192_000        # bit/s: one AC-3 stereo track
DEFAULT_RESERVE = 32 * 1024 * 1024  # bytes: file system, IFOs and BUPs, menus
DEFAULT_MARGIN = 0.02          # of the disc: the encoder's distance from its average
NAV_RATE = 2 * SECTOR * 8      # bit/s: a navigation pack every half second
PACK_OVERHEAD = 0.015          # of each pack: pack header and PES header
MAX_CHAPTERS = 99              # chapters (PTTs) a title holds


@dataclass(frozen=True)
class Session:
    start: float               # seconds from the programme's start
    label: str = ""


@dataclass
class Plan:
    """The result of `plan`. Bitrates are in bit/s, sizes in bytes."""

    disc: Disc
    duration: float
    sessions: tuple
    video_bitrate: int
    audio_bitrate: int         # every sound track together
    subtitle_bitrate: int
    floor: int
    meets_floor: bool
    shortfall_bytes: int = 0       # what the disc lacks at the floor
    shortfall_seconds: float = 0.0  # programme beyond what fits at the floor
    max_duration: float = 0.0      # the longest programme the disc holds at the floor
    warnings: list = field(default_factory=list)

    @property
    def chapters(self):
        """The chapter times for `vtskit.titleset.Title(chapters=...)`."""
        return tuple(s.start for s in self.sessions)

    @property
    def mux_bitrate(self):
        return mux_bitrate(self.video_bitrate + self.audio_bitrate + self.subtitle_bitrate)

    @property
    def size(self):
        """The programme's expected size on the disc, without the reserve."""
        return math.ceil(self.mux_bitrate * self.duration / 8)

    def summary(self):
        lines = [f"{self.disc.name}: {_hms(self.duration)} at {self.video_bitrate / 1e6:.2f} Mbit/s video"
                 f" (floor {self.floor / 1e6:.2f} Mbit/s), {len(self.sessions)} chapters"]
        if not self.meets_floor:
            lines.append(f"short by {self.shortfall_bytes / 1e6:.0f} MB at the floor:"
                         f" {_hms(self.shortfall_seconds)} too long (the disc holds {_hms(self.max_duration)})")
        lines += [f"warning: {w}" for w in self.warnings]
        for n, s in enumerate(self.sessions, 1):
            lines.append(f"  {n:2d} {_hms(s.start)} {s.label}".rstrip())
        return "\n".join(lines)


def mux_bitrate(payload):
    """The program stream's bitrate for `payload` bit/s of elementary streams."""
    return payload / (1 - PACK_OVERHEAD) + NAV_RATE


def payload_bitrate(mux):
    """The elementary streams' bitrate a program stream of `mux` bit/s carries."""
    return (mux - NAV_RATE) * (1 - PACK_OVERHEAD)


_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def parse_time(text):
    """Seconds from "H:MM:SS", "MM:SS" (with decimals) or plain seconds."""
    t = text.strip()
    m = _TIME.match(t)
    if m:
        h, mm, ss = m.groups()
        if (h is not None and int(mm) > 59) or float(ss) >= 60:
            raise VtsError(f"not a time: {text!r}")
        return int(h or 0) * 3600 + int(mm) * 60 + float(ss)
    try:
        v = float(t)
    except ValueError:
        raise VtsError(f"not a time: {text!r}") from None
    if not math.isfinite(v) or v < 0:
        raise VtsError(f"not a time: {text!r}")
    return v


def parse_chapters(text):
    """Sessions from a chapter list, a line a session: its start, then its
    name ("0:00 Opening", "1:05:30 Panel"). Blank lines and lines starting
    with "#" are skipped."""
    sessions = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        when, _, label = line.partition(" ")
        try:
            start = parse_time(when)
        except VtsError:
            raise VtsError(f"chapter list line {n}: no start time in {line!r}") from None
        sessions.append(Session(start, label.strip()))
    return sessions


def _sessions(chapters, duration):
    if isinstance(chapters, str):
        chapters = parse_chapters(chapters)
    out = []
    for c in chapters:
        if isinstance(c, Session):
            out.append(c)
        elif isinstance(c, (int, float)):
            out.append(Session(float(c)))
        else:
            start, label = c
            out.append(Session(float(start) if not isinstance(start, str) else parse_time(start), label))
    for s in out:
        if not math.isfinite(s.start) or s.start < 0:
            raise VtsError(f"a chapter at {s.start} s, before the start")
    if not out:
        out = [Session(0.0)]
    if out[0].start > 0:
        out.insert(0, Session(0.0))       # a title's first chapter is at its start
    for a, b in zip(out, out[1:]):
        if b.start <= a.start:
            raise VtsError(f"chapter at {_hms(b.start)} does not come after {_hms(a.start)}")
    if out[-1].start >= duration:
        raise VtsError(f"chapter at {_hms(out[-1].start)} is not before the end ({_hms(duration)})")
    if len(out) > MAX_CHAPTERS:
        raise VtsError(f"{len(out)} chapters; a title holds {MAX_CHAPTERS}")
    return tuple(out)


def plan(chapters, duration, disc="auto", *, audio=DEFAULT_AUDIO, audio_tracks=1, subtitles=0,
         floor=DEFAULT_FLOOR, reserve=DEFAULT_RESERVE, margin=DEFAULT_MARGIN):
    """Plan a programme of `duration` seconds with `chapters` (a chapter list
    as text, or (start, label) pairs, or start times) on `disc` ("dvd5",
    "dvd9", or "auto": DVD-5 when it meets the floor there, else DVD-9).
    `audio` is one sound track's bitrate, `subtitles` all subtitle streams'
    together, `floor` the lowest average video bitrate called acceptable."""
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        raise VtsError("the programme needs a length above zero")
    if floor <= 0 or audio < 0 or audio_tracks < 0 or subtitles < 0 or reserve < 0 or not 0 <= margin < 1:
        raise VtsError("bitrates, the reserve and the margin cannot be negative")
    if floor > MAX_VIDEO:
        raise VtsError(f"a floor above {MAX_VIDEO / 1e6} Mbit/s cannot be met on DVD-Video")
    sessions = _sessions(chapters, duration)
    if disc == "auto":
        p = _plan(sessions, duration, DISCS["dvd5"], audio * audio_tracks, subtitles, floor, reserve, margin)
        if p.meets_floor:
            return p
        disc = "dvd9"
    if disc not in DISCS:
        raise VtsError(f"no disc {disc!r}; one of {', '.join(DISCS)} or auto")
    return _plan(sessions, duration, DISCS[disc], audio * audio_tracks, subtitles, floor, reserve, margin)


def _plan(sessions, duration, disc, audio, subtitles, floor, reserve, margin):
    room = (disc.bytes - reserve) * (1 - margin)
    if room <= 0:
        raise VtsError(f"the reserve leaves nothing of {disc.name}")
    others = audio + subtitles
    if mux_bitrate(others) >= MAX_MUX:
        raise VtsError("the sound and subtitle streams alone exceed DVD-Video's 10.08 Mbit/s")
    cap = min(MAX_VIDEO, int(payload_bitrate(MAX_MUX) - others))
    if floor > cap:
        raise VtsError(f"a floor of {floor / 1e6:.2f} Mbit/s is above the {cap / 1e6:.2f} Mbit/s of video"
                       " DVD-Video's stream limit leaves beside the sound and subtitles")
    video = min(cap, int(payload_bitrate(room * 8 / duration) - others))
    at_floor = mux_bitrate(floor + others)
    max_duration = room * 8 / at_floor
    p = Plan(disc, duration, sessions, max(video, 0), audio, subtitles, floor, video >= floor,
             max_duration=max_duration)
    if not p.meets_floor:
        p.shortfall_bytes = math.ceil(at_floor * duration / 8 - room)
        p.shortfall_seconds = duration - max_duration
        p.warnings.append(f"{max(video, 0) / 1e6:.2f} Mbit/s of video is below the floor of"
                          f" {floor / 1e6:.2f} Mbit/s on {disc.name}")
    for a, b in zip(sessions, sessions[1:] + (Session(duration),)):
        if b.start - a.start < 1:
            p.warnings.append(f"chapter at {_hms(a.start)} lasts under a second; it may land on the next")
    return p


def _hms(seconds):
    s = round(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"
