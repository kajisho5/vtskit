"""The titleset vtskit writes (vtskit.titleset), checked against dvdauthor's
for the same titles: VTS_01_0.IFO and the VOBs must be the same byte for
byte, the BUP the IFO again; and a disc made of dvdauthor's VMG and
vtskit's titleset played with libdvdnav."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from vtskit import VtsError, ifo, ps, titleset, vm
from vtskit.titleset import Audio, Subpicture, Title

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor"))
SPUMUX_OK = bool(shutil.which("spumux"))


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def encode(path, std="ntsc", seconds=8, aspect="4:3", gop=None, audio=("-c:a", "ac3", "-ac", "2"),
           tones=1):
    """An MPEG-2 program stream as oncemore makes them (ffmpeg -f dvd)."""
    size, rate = ("720x480", "30000/1001") if std == "ntsc" else ("720x576", "25")
    inputs, maps = [], ["-map", "0:v"]
    for k in range(tones):
        inputs += ["-f", "lavfi", "-i", f"sine=f={440 + 220 * k}:d={seconds}"]
        maps += ["-map", f"{k + 1}:a"]
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={seconds}", *inputs, *maps,
           "-target", f"{std}-dvd", "-aspect", aspect, "-b:v", "3000k",
           "-sc_threshold", "1000000000", *(["-g", str(gop)] if gop else []), *audio, path)


def author(folder, std, video_attrs, streams, pgcs):
    """dvdauthor's VIDEO_TS for titles [(mpg, "chapters")] in one titleset."""
    out = os.path.join(folder, "dvd")
    xml = os.path.join(folder, "dvd.xml")
    body = "".join(f'<pgc><vob file="{mpg}" chapters="{ch}"/></pgc>' for mpg, ch in pgcs)
    with open(xml, "w") as f:
        f.write(f'<dvdauthor dest="{out}"><vmgm/><titleset><titles>'
                f'<video format="{std}" {video_attrs}/>{streams}{body}'
                '</titles></titleset></dvdauthor>')
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": std.upper()})
    return os.path.join(out, "VIDEO_TS")


def author_cells(folder, mpg, cells):
    """dvdauthor's VIDEO_TS for one title given cell by cell: [(start, end, attributes)]."""
    out = os.path.join(folder, "dvd")
    xml = os.path.join(folder, "dvd.xml")
    body = "".join(f'<cell start="{a}" end="{b}" chapter="yes" {attrs}/>' for a, b, attrs in cells)
    with open(xml, "w") as f:
        f.write(f'<dvdauthor dest="{out}"><vmgm/><titleset><titles><video format="ntsc"/>'
                f'<audio lang="en"/><pgc><vob file="{mpg}">{body}</vob></pgc></titles></titleset></dvdauthor>')
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": "NTSC"})
    return os.path.join(out, "VIDEO_TS")


def read(path):
    with open(path, "rb") as f:
        return f.read()


def describe(data):
    """Where two IFOs differ: the table (by its sector pointer) and the byte."""
    at = data[0]
    tables = {"VTS_MAT": 0}
    for name, o in (("VTS_PTT_SRPT", 0xC8), ("VTS_PGCI", 0xCC), ("VTS_TMAPT", 0xD4),
                    ("VTS_C_ADT", 0xE0), ("VTS_VOBU_ADMAP", 0xE4)):
        tables[name] = struct.unpack_from(">I", data[1], o)[0] * ps.SECTOR
    name = max((o, n) for n, o in tables.items() if o <= at)[1]
    return f"byte {at:#x}, in {name} at +{at - tables[name]:#x}"


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
class TestAgainstDvdauthor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vtskit-vts-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def same(self, std, video_attrs, streams, titles, **kw):
        """dvdauthor and vtskit write the same titleset for these titles."""
        theirs = author(self.tmp, std, video_attrs, streams,
                        [(t.stream, ",".join(str(c) for c in t.chapters)) for t in titles])
        mine = os.path.join(self.tmp, "mine")
        ts = titleset.write(mine, 1, titles, **kw)
        self.assertEqual(sorted(ts.files), sorted(n for n in os.listdir(theirs) if n.startswith("VTS_")))
        a, b = read(os.path.join(mine, "VTS_01_0.IFO")), read(os.path.join(theirs, "VTS_01_0.IFO"))
        if a != b:
            at = next(k for k in range(min(len(a), len(b))) if a[k] != b[k])
            self.fail(f"IFO differs from dvdauthor's at {describe((at, b))}: "
                      f"mine {a[at:at + 8].hex()} theirs {b[at:at + 8].hex()}")
        self.assertEqual(read(os.path.join(mine, "VTS_01_0.BUP")), a)
        for name in ts.files:
            if name.endswith(".VOB"):
                self.assertEqual(read(os.path.join(mine, name)), read(os.path.join(theirs, name)), name)
        self.assertEqual(ts.sectors, struct.unpack_from(">I", a, 0x0C)[0] + 1)
        return ts, theirs, mine

    def test_chapters(self):
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=12)
        ts, _, _ = self.same("ntsc", "", '<audio lang="en"/>',
                             [Title(mpg, (0, 4, 8), (Audio("en", 2),))])
        self.assertEqual([c.number for c in ts.titles[0].cells], [1, 2, 3])
        self.assertEqual(ts.attributes[0x100:0x102], bytes((0x43, 0)))

    def test_chapters_between_vobus(self):
        # VOBUs of 0.4 s (GOPs of 2 frames, several a VOBU) and chapters that
        # fall between their starts; the sound outlasts the video a little
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=10, gop=2)
        self.same("ntsc", "", "", [Title(mpg, (0, 3.3, 5.0))])

    def test_two_titles_pal_widescreen(self):
        a, b = os.path.join(self.tmp, "a.mpg"), os.path.join(self.tmp, "b.mpg")
        encode(a, "pal", 5, "16:9", audio=("-c:a", "mp2", "-b:a", "192k", "-ac", "2"))
        encode(b, "pal", 4, "16:9", audio=("-c:a", "mp2", "-b:a", "192k", "-ac", "2"))
        ts, _, _ = self.same("pal", 'aspect="16:9"', "", [Title(a, (0, 2)), Title(b)])
        self.assertEqual([t.vob_id for t in ts.titles], [1, 2])
        self.assertEqual(ts.titles[1].first_sector, ts.titles[0].sectors)

    def test_one_picture(self):
        # a still: one picture, then its sound
        mpg = os.path.join(self.tmp, "still.mpg")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x480:r=30000/1001:d=8", "-f", "lavfi", "-i", "sine=d=8",
               "-frames:v", "1", "-target", "ntsc-dvd", "-aspect", "4:3", "-c:a", "ac3", "-ac", "2", mpg)
        self.same("ntsc", 'aspect="4:3"', '<audio lang="en"/>', [Title(mpg, audio=(Audio("en", 2),))])

    def test_sound_outlasts_the_picture(self):
        # the program chain and the last cell last until the sound ends
        mpg = os.path.join(self.tmp, "tail.mpg")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x480:r=30000/1001:d=3", "-f", "lavfi", "-i", "sine=d=8",
               "-target", "ntsc-dvd", "-aspect", "4:3", "-c:a", "ac3", "-ac", "2", mpg)
        self.same("ntsc", 'aspect="4:3"', '<audio lang="en"/>', [Title(mpg, (0, 2), audio=(Audio("en", 2),))])

    def test_two_sound_tracks(self):
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=6, aspect="16:9", tones=2,
               audio=("-c:a:0", "ac3", "-ac:a:0", "1", "-c:a:1", "pcm_s16be", "-ac:a:1", "2",
                      "-af:a:1", "asetnsamples=n=80"))
        self.same("ntsc", 'aspect="16:9" widescreen="nopanscan"',
                  '<audio lang="en"/><audio lang="ja"/>',
                  [Title(mpg, (0, 3), (Audio("en", 1), Audio("ja", 2)))], pan_scan=False)

    @unittest.skipUnless(SPUMUX_OK, "needs spumux")
    def test_subtitles(self):
        from .test_nav import png
        plain, mpg = os.path.join(self.tmp, "plain.mpg"), os.path.join(self.tmp, "a.mpg")
        encode(plain, seconds=6)
        png(os.path.join(self.tmp, "sub.png"), 720, 480, (100, 380, 400, 420))
        xml = os.path.join(self.tmp, "spu.xml")
        with open(xml, "w") as f:
            f.write('<subpictures><stream>'
                    f'<spu start="00:00:01.00" end="00:00:02.50" image="{self.tmp}/sub.png"/>'
                    '</stream></subpictures>')
        with open(plain, "rb") as src, open(mpg, "wb") as dst:
            subprocess.run(["spumux", "-s0", xml], stdin=src, stdout=dst, check=True,
                           stderr=subprocess.DEVNULL)
        # (spumux tells dvdauthor the subtitle's colours; here they are given:
        # the white of the box first, then black)
        self.same("ntsc", "", '<audio lang="en"/><subpicture lang="ja"/>',
                  [Title(mpg, (0, 3), (Audio("en", 2),), (Subpicture("ja"),),
                         palette=((0xEB, 0x80, 0x80),))])

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_libdvdnav_plays_it(self):
        # dvdauthor's VMG with vtskit's titleset: two titles, the first of
        # three chapters; the first plays through its chapters in order and
        # ends (its program chain's post command is Exit)
        a, b = os.path.join(self.tmp, "a.mpg"), os.path.join(self.tmp, "b.mpg")
        encode(a, seconds=6)
        encode(b, seconds=3)
        ts, theirs, mine = self.same("ntsc", "", "", [Title(a, (0, 2, 4)), Title(b)])
        disc = os.path.join(self.tmp, "disc", "VIDEO_TS")
        shutil.copytree(theirs, disc)
        for name in ts.files:
            shutil.copy(os.path.join(mine, name), os.path.join(disc, name))
        ifos = ifo.read_folder(disc)
        self.assertEqual([c[:3] for c in ifo.chapters(ifos)], [(1, 1, 1), (1, 1, 2), (1, 1, 3), (1, 2, 1)])
        seen = []
        with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
            self.assertEqual(d.num_titles(), 2)
            self.assertEqual([d.num_parts(1), d.num_parts(2)], [3, 1])
            self.assertEqual(d.wait_title(), (1, 1))
            for _ in range(400_000):
                if d.step() == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                    tp = d.title_part()
                    if not seen or seen[-1] != tp:
                        seen.append(tp)
                if d.stopped:
                    break
        self.assertEqual(seen, [(1, 1), (1, 2), (1, 3)])
        self.assertTrue(d.stopped)

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_commands_run(self):
        # the first title's post command jumps to the second title, and the
        # second's first chapter ends its program chain (a cell command), so
        # its post runs: into title 1 again, chapter 2
        a, b = os.path.join(self.tmp, "a.mpg"), os.path.join(self.tmp, "b.mpg")
        encode(a, seconds=4)
        encode(b, seconds=4)
        theirs = author(self.tmp, "ntsc", "", "", [(a, "0,2"), (b, "0,2")])
        mine = os.path.join(self.tmp, "mine")
        ts = titleset.write(mine, 1, [
            Title(a, (0, 2), post=vm.assemble([vm.jump_vts_tt(2)])),
            Title(b, (0, 2), cell_commands={1: vm.link(vm.TAIL_PGC).encode()},
                  post=vm.assemble([vm.If(vm.compare(vm.GPRM(0), vm.EQ, 0),
                                          [vm.set_gprm(0, vm.MOV, 1), vm.jump_vts_ptt(1, 2)])])),
        ])
        disc = os.path.join(self.tmp, "disc", "VIDEO_TS")
        shutil.copytree(theirs, disc)
        for name in ts.files:
            shutil.copy(os.path.join(mine, name), os.path.join(disc, name))
        seen = []
        with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
            self.assertEqual(d.wait_title(), (1, 1))
            for _ in range(400_000):
                if d.step() == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                    tp = d.title_part()
                    if not seen or seen[-1] != tp:
                        seen.append(tp)
                if d.stopped:
                    break
        # title 1 whole, title 2's first chapter only, title 1 from chapter 2,
        # title 2's first chapter again, then its post falls through (g0 is 1)
        self.assertEqual(seen, [(1, 1), (1, 2), (2, 1), (1, 2), (2, 1)])
        self.assertTrue(d.stopped)

    def test_stills_and_uops(self):
        # cells that stop (for ever, for 3 s) as dvdauthor writes them, given
        # one by one; dvdauthor leaves the seamless flag off every such cell,
        # vtskit only off the stopping ones: the first byte of a cell may differ
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=12)
        theirs = author_cells(self.tmp, mpg, [("0", "4", 'pause="inf"'), ("4", "8", 'pause="3"'), ("8", "-1", "")])
        mine = os.path.join(self.tmp, "mine")
        uops = vm.UOP_TIME_SEARCH | vm.UOP_PTT_SEARCH | vm.UOP_STOP
        titleset.write(mine, 1, [Title(mpg, (0, 4, 8), (Audio("en", 2),), stills={1: None, 2: 3}, uops=uops)])
        a, b = read(os.path.join(mine, "VTS_01_0.IFO")), read(os.path.join(theirs, "VTS_01_0.IFO"))
        pgci = struct.unpack_from(">I", a, 0xCC)[0] * ps.SECTOR
        p = pgci + struct.unpack_from(">I", a, pgci + 12)[0]
        cpi = struct.unpack_from(">H", a, p + 0xE8)[0]
        self.assertEqual(struct.unpack_from(">I", a, p + 8)[0], uops)
        a, b = bytearray(a), bytearray(b)
        struct.pack_into(">I", b, p + 8, uops)
        a[p + cpi + 48] = b[p + cpi + 48] = 0          # the third cell's first byte (0x08 / 0x00)
        self.assertEqual(bytes(a), bytes(b))
        with self.assertRaisesRegex(VtsError, "still"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, (0, 4), stills={3: None})])
        with self.assertRaises(VtsError):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, (0, 4), stills={1: 300})])

    def test_captions_flag(self):
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=2)
        for attrs, captions, want in (('caption="field1"', (True, False), 0x4380),
                                      ('caption="field2"', (False, True), 0x4340)):
            theirs = author(self.tmp, "ntsc", attrs, "", [(mpg, "0")])
            mine = os.path.join(self.tmp, "mine")
            titleset.write(mine, 1, [Title(mpg)], captions=captions)
            self.assertEqual(read(os.path.join(mine, "VTS_01_0.IFO"))[0x200:0x202], want.to_bytes(2, "big"))
            self.assertEqual(read(os.path.join(theirs, "VTS_01_0.IFO"))[0x200:0x202], want.to_bytes(2, "big"))
            shutil.rmtree(mine)
            shutil.rmtree(os.path.join(self.tmp, "dvd"))

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_still_under_libdvdnav(self):
        a = os.path.join(self.tmp, "a.mpg")
        encode(a, seconds=6)
        theirs = author(self.tmp, "ntsc", "", "", [(a, "0,2,4")])
        mine = os.path.join(self.tmp, "mine")
        ts = titleset.write(mine, 1, [Title(a, (0, 2, 4), stills={1: None, 2: 2})])
        disc = os.path.join(self.tmp, "disc", "VIDEO_TS")
        shutil.copytree(theirs, disc)
        for name in ts.files:
            shutil.copy(os.path.join(mine, name), os.path.join(disc, name))
        with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
            d.wait_title()
            for _ in range(400_000):
                d.step()
                if d.stopped:
                    break
            self.assertEqual([(s[2], s[3]) for s in d.stills], [(1, 255), (2, 2)])

    def test_refused(self):
        mpg = os.path.join(self.tmp, "a.mpg")
        encode(mpg, seconds=2)
        with self.assertRaisesRegex(VtsError, "chapter"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, (0, -1))])
        with self.assertRaisesRegex(VtsError, "sound streams"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, audio=(Audio(), Audio()))])
        with self.assertRaisesRegex(VtsError, "language"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, audio=(Audio("eng"),))])
        with self.assertRaisesRegex(VtsError, "8 bytes"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, post=(b"\x30",))])
        with self.assertRaisesRegex(VtsError, "JumpTT"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, post=vm.assemble([vm.jump_tt(1)]))])
        with self.assertRaisesRegex(VtsError, "cell command"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg, cell_commands={2: titleset.EXIT})])
        pal = os.path.join(self.tmp, "b.mpg")
        encode(pal, "pal", 2)
        with self.assertRaisesRegex(VtsError, "differs"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(mpg), Title(pal)])
        with self.assertRaises(VtsError):
            titleset.write(os.path.join(self.tmp, "x"), 100, [Title(mpg)])


class TestPieces(unittest.TestCase):
    class V:
        def __init__(self, s_ptm):
            self.s_ptm = s_ptm

    def test_cell_starts(self):
        # VOBUs of 12 frames (0.4 s): a chapter goes to the nearest start
        vs = [self.V(48003 + 12 * 3003 * k) for k in range(25)]
        self.assertEqual(titleset.cell_starts(vs, 3003, (0, 3.3, 5.0)), [0, 8, 12])
        self.assertEqual(titleset.cell_starts(vs, 3003, (3.6, 3.9)), [0, 9, 10])
        with self.assertRaises(VtsError):
            titleset.cell_starts(vs, 3003, (-1,))

    def test_palette(self):
        self.assertEqual(titleset._palette(()), bytes.fromhex("00108080") * 16)
        self.assertEqual(titleset._palette(((0xEB, 0x80, 0x80),))[:8], bytes.fromhex("00eb808000108080"))
        with self.assertRaises(VtsError):
            titleset._palette([(0, 0, 0)] * 17)
        with self.assertRaises(VtsError):
            titleset._palette([(256, 0, 0)])

    def test_lang(self):
        self.assertEqual(titleset._lang("en"), b"en")
        self.assertEqual(titleset._lang(None), b"\0\0")
        for bad in ("", "e", "eng", "e1", "日本"):
            with self.assertRaises(VtsError):
                titleset._lang(bad)


if __name__ == "__main__":
    unittest.main()
