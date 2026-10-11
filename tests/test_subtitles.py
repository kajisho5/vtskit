"""Subtitles (vtskit.subtitles): the units the same bytes as spumux's for
the same pictures, their packs placed in the stream, and a title with the
subtitles authored by dvdauthor and by vtskit from the same stream (the
navigation packs point at the units the same way), played with libdvdnav."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from vtskit import VtsError, ps, spu, subtitles, titleset, vmg
from vtskit.subtitles import Cue
from vtskit.titleset import Audio, Subpicture, Title

from .test_titleset import encode, read

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

FFMPEG_OK = bool(shutil.which("ffmpeg"))
TOOLS_OK = FFMPEG_OK and bool(shutil.which("dvdauthor") and shutil.which("spumux"))

# a subtitle picture as oncemore draws them: index 1 the text (fill), 2 its
# outline, 3 the anti-aliasing between, 0 transparent
PALETTE = [(0, 0, 0, 0), (255, 255, 255, 255), (0, 0, 0, 255), (128, 128, 128, 255)]
WHITE, BLACK, GREY = (0xEB, 0x80, 0x80), (0x10, 0x80, 0x80), (0x7E, 0x80, 0x80)


def picture(w, h):
    """An outlined box with a hole, as codes: 2 outline, 1 fill, 3 mix, 0 hole."""
    px = bytearray([2]) * (w * h)
    for y in range(4, h - 4):
        px[y * w + 4:y * w + w - 4] = bytes([1]) * (w - 8)
    for y in range(2, 6):
        px[y * w + 2:y * w + 6] = bytes([3]) * 4
    for y in range(10, 14):
        px[y * w + 10:y * w + 20] = bytes(10)
    return spu.Overlay(w, h, bytes(px))


def png(path, pic, palette):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + pic.pixels[y * pic.width:(y + 1) * pic.width] for y in range(pic.height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", pic.width, pic.height, 8, 3, 0, 0, 0))
                + chunk(b"PLTE", b"".join(bytes(c[:3]) for c in palette))
                + chunk(b"tRNS", bytes(c[3] for c in palette))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def spu_packs(data):
    """[(sector, sub-stream, PTS, unit bytes)] of a stream's subpicture units
    (those in one pack)."""
    out = []
    for p in ps.iter_packs(data):
        for pes in p.pes:
            if pes.sid == 0xBD and pes.sub in ps.SUB_SPU and pes.pts is not None:
                pack = data[p.sector * ps.SECTOR:(p.sector + 1) * ps.SECTOR]
                out.append((p.sector, pes.sub, pes.pts, bytes(pack[pes.start + 1:pes.end])))
    return out


class TestUnits(unittest.TestCase):
    def test_round_trip(self):
        pic = picture(150, 32)
        cue = Cue(0.5, 3.0, pic, 285, 420, colours=(0, 3, 1, 2), contrasts=(0, 15, 15, 15))
        u = subtitles.unit(cue)
        area, pixels, cmds = spu.decode(u)
        self.assertEqual(area, (285, 434, 420, 451))
        self.assertEqual(pixels, {(285 + x, 420 + y): pic.pixels[y * 150 + x] for y in range(32) for x in range(150)})
        self.assertEqual(cmds[:7], bytes.fromhex("01" "03" "2130" "04" "fff0"))   # shown; colours, contrasts
        # the second display control sequence: off after 2.5 s (in units of 1024 ticks)
        dcsqt = struct.unpack_from(">H", u, 2)[0]
        second = struct.unpack_from(">H", u, dcsqt + 2)[0]
        self.assertNotEqual(second, dcsqt)
        self.assertEqual(struct.unpack_from(">HH", u, second), (220, second))
        self.assertEqual(u[second + 4:second + 6], b"\x02\xff")
        self.assertEqual(len(u), struct.unpack_from(">H", u, 0)[0])

    def test_refused(self):
        pic = picture(40, 20)
        with self.assertRaises(VtsError):
            subtitles.unit(Cue(2.0, 1.0, pic))
        with self.assertRaises(VtsError):
            subtitles.unit(Cue(0.0, 1.0, pic, 1000, 0))
        with self.assertRaises(VtsError):
            subtitles.unit(Cue(0.0, 1.0, pic, colours=(0, 16, 0, 0)))
        with self.assertRaises(VtsError):
            subtitles.unit(Cue(0.0, 1.0, pic, contrasts=(0, 1, 2)))


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg, dvdauthor and spumux")
class TestAgainstSpumux(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = t = tempfile.mkdtemp(prefix="vtskit-subs-")
        cls.plain = os.path.join(t, "plain.mpg")
        encode(cls.plain, seconds=8)
        cls.pics = {"a": picture(200, 40), "b": picture(120, 30), "c": picture(150, 32)}
        for name, pic in cls.pics.items():
            png(os.path.join(t, f"{name}.png"), pic, PALETTE)
        # spumux: stream 0 with two cues, stream 1 with one
        cls.cues = {0: [("a", 1.0, 2.5, 260, 400), ("b", 4.2, 6.0, 300, 60)], 1: [("c", 0.5, 3.0, 285, 420)]}
        cur = cls.plain
        for n, cues in cls.cues.items():
            with open(os.path.join(t, f"s{n}.xml"), "w") as f:
                f.write('<subpictures format="NTSC"><stream>' + "".join(
                    f'<spu start="{_ts(a)}" end="{_ts(b)}" image="{t}/{name}.png" xoffset="{x}" yoffset="{y}"/>'
                    for name, a, b, x, y in cues) + "</stream></subpictures>")
            nxt = os.path.join(t, f"spumux{n}.mpg")
            with open(cur, "rb") as src, open(nxt, "wb") as dst:
                subprocess.run(["spumux", "-s", str(n), os.path.join(t, f"s{n}.xml")], stdin=src, stdout=dst,
                               check=True, stderr=subprocess.DEVNULL, env={**os.environ, "VIDEO_FORMAT": "NTSC"})
            cur = nxt
        cls.theirs = read(cur)
        # vtskit: the same cues; spumux numbers the codes in the order the
        # colours first appear (outline 0, mix 1, fill 2, hole 3) and lists
        # the colours in that order, so the same here
        remap = {2: 0, 3: 1, 1: 2, 0: 3}
        cls.vcues = {n: [Cue(a, b, spu.Overlay(p.width, p.height, bytes(remap[v] for v in p.pixels)), x, y,
                             colours=(0, 1, 2, 0), contrasts=(15, 15, 15, 0))
                         for p, (name, a, b, x, y) in ((cls.pics[name], c) for c in cues for name in [c[0]])]
                     for n, cues in cls.cues.items()}
        cls.mine = subtitles.mux(read(cls.plain), cls.vcues)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_units_as_spumux(self):
        a, b = spu_packs(self.mine), spu_packs(ps.drop_padding(self.theirs))
        self.assertEqual([(x[1], x[2]) for x in a], [(x[1], x[2]) for x in b])    # streams and PTSs
        self.assertEqual([x[3] for x in a], [x[3] for x in b])                    # the units, byte for byte
        # placed where spumux places them (its padding packs dropped)
        self.assertEqual([x[0] for x in a], [x[0] for x in b])

    def test_written_to_a_file_the_same(self):
        # a mapped stream muxed into a file: the same bytes
        path = os.path.join(self.tmp, "out.mpg")
        with ps.Mapped(self.plain) as m, open(path, "wb") as f:
            self.assertIsNone(subtitles.mux(m, self.vcues, out=f))
        self.assertEqual(read(path), bytes(self.mine))

    def test_other_packs_untouched(self):
        plain = read(self.plain)
        spus = {x[0] for x in spu_packs(self.mine)}
        rest = b"".join(self.mine[s * ps.SECTOR:(s + 1) * ps.SECTOR] for s in range(len(self.mine) // ps.SECTOR)
                        if s not in spus)
        self.assertEqual(rest, plain)
        scrs = [p.scr for p in ps.iter_packs(self.mine)]
        self.assertEqual(scrs, sorted(scrs))
        self.assertEqual(len(set(scrs)), len(scrs))

    def test_refused(self):
        plain = read(self.plain)
        with self.assertRaises(VtsError):
            subtitles.mux(plain, {0: [Cue(1.0, 3.0, self.pics["a"]), Cue(2.0, 4.0, self.pics["a"])]})
        with self.assertRaises(VtsError):
            subtitles.mux(plain, {32: [Cue(1.0, 3.0, self.pics["a"])]})
        with self.assertRaises(VtsError):
            subtitles.mux(self.mine, {0: [Cue(1.0, 3.0, self.pics["a"])]})      # already has stream 0

    def test_titleset_as_dvdauthor(self):
        # the vtskit-muxed stream authored by both: the navigation packs point
        # at the units the same way (the palette is given to both: spumux's
        # colour list is a padding packet vtskit does not write)
        self.same_titleset(self.mine, "dvd", 'aspect="4:3"')

    def test_widescreen_titleset_as_dvdauthor(self):
        # a 16:9 picture: the program chain names each subpicture stream for
        # the wide, letterboxed and pan-scanned displays
        wide = os.path.join(self.tmp, "wide.mpg")
        encode(wide, seconds=8, aspect="16:9")
        data = subtitles.mux(read(wide), self.vcues)
        self.same_titleset(data, "dvdwide", 'aspect="16:9"')
        self.same_titleset(data, "dvdwidenops", 'aspect="16:9" widescreen="nopanscan"', pan_scan=False)
        self.same_titleset(data, "dvdwidenolb", 'aspect="16:9" widescreen="noletterbox"', letterbox=False)

    def same_titleset(self, data, name, video_attrs, **kw):
        t = self.tmp
        mpg = os.path.join(t, name + ".mpg")
        with open(mpg, "wb") as f:
            f.write(data)
        out = os.path.join(t, name)
        with open(out + ".xml", "w") as f:
            f.write(f'<dvdauthor dest="{out}"><vmgm/><titleset><titles><video format="ntsc" {video_attrs}/>'
                    '<audio format="ac3" channels="2" lang="en"/><subpicture lang="en"/><subpicture lang="ja"/>'
                    f'<pgc><vob file="{mpg}" chapters="0,3"/></pgc></titles></titleset></dvdauthor>')
        subprocess.run(["dvdauthor", "-x", out + ".xml"], check=True, capture_output=True,
                       env={**os.environ, "VIDEO_FORMAT": "NTSC"})
        mine = os.path.join(t, name + "-vts")
        titleset.write(mine, 1, [Title(mpg, (0, 3), (Audio("en", 2),), (Subpicture("en"), Subpicture("ja")),
                                       palette=())], **kw)
        self.assertEqual(read(os.path.join(mine, "VTS_01_1.VOB")), read(os.path.join(out, "VIDEO_TS", "VTS_01_1.VOB")))
        a, b = read(os.path.join(mine, "VTS_01_0.IFO")), read(os.path.join(out, "VIDEO_TS", "VTS_01_0.IFO"))
        self.assertEqual(len(a), len(b))
        # (dvdauthor's palette without spumux's colour list: whatever it is, the rest the same)
        pgc = struct.unpack_from(">I", a, 0xCC)[0] * 2048 + 16
        if a[:pgc + 0xA4] != b[:pgc + 0xA4]:
            at = next(k for k in range(pgc + 0xA4) if a[k] != b[k])
            self.fail(f"IFO differs from dvdauthor's at {at:#x}: mine {a[at:at + 8].hex()} theirs {b[at:at + 8].hex()}")
        self.assertEqual(a[pgc + 0xE4:], b[pgc + 0xE4:])

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_under_libdvdnav(self):
        t = self.tmp
        mpg = os.path.join(t, "mine2.mpg")
        with open(mpg, "wb") as f:
            f.write(self.mine)
        out = os.path.join(t, "disc")
        ts = titleset.write(out, 1, [Title(mpg, (0, 3), (Audio("en", 2),), (Subpicture("en"), Subpicture("ja")),
                                           palette=(BLACK, GREY, WHITE))])
        vmg.write(out, [ts])
        with dvdnav_sim.DvdSim(out, LIBDVDNAV) as d:
            self.assertEqual(d.wait_title(), (1, 1))
            self.assertEqual([d.subtitle_lang(0), d.subtitle_lang(1)], ["en", "ja"])


def _ts(seconds):
    m, s = divmod(seconds, 60)
    return f"00:{int(m):02d}:{s:06.3f}"[:-1]


if __name__ == "__main__":
    unittest.main()
