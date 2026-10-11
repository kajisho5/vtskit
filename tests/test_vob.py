"""The navigation packs vtskit fills (vtskit.vob), checked byte for byte
against the VOB dvdauthor writes from the same stream, for streams of
several shapes; and a disc whose VOB is vtskit's, played with libdvdnav."""

import os
import shutil
import subprocess
import tempfile
import unittest

from vtskit import VtsError, nav, ps, vob

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor"))

# name: (seconds, PAL?, chapter times, ffmpeg video options, ffmpeg audio options)
STREAMS = {
    # oncemore's shape: one GOP a VOBU, chapters on forced key frames
    "chapters": (12, False, [0, 4, 8], ["-g", "15", "-force_key_frames", "4,8"],
                 ["-c:a", "ac3", "-b:a", "192k"]),
    # long enough for the 10 s search pointers, B frames (open GOPs)
    "long": (25, False, [0], ["-g", "15", "-bf", "2"], ["-c:a", "ac3", "-b:a", "192k"]),
    "pal": (6, True, [0], ["-g", "12"], ["-c:a", "mp2", "-b:a", "192k"]),
    # several GOPs a VOBU, chapters between VOBU starts
    "short-gops": (8, False, [0, 3.3, 5.0], ["-g", "2"], ["-c:a", "ac3", "-b:a", "192k"]),
    # fewer than three reference pictures a VOBU
    "few-refs": (4, False, [0], ["-g", "15", "-bf", "13", "-b_strategy", "0"],
                 ["-c:a", "ac3", "-b:a", "192k"]),
    "lpcm": (6, False, [0, 2.5], ["-g", "18"], ["-c:a", "pcm_s16be", "-ar", "48000", "-ac", "2"]),
}


def encode(path, seconds, pal, video, audio):
    """An MPEG-2 program stream as oncemore makes them (ffmpeg -f dvd)."""
    size, rate, target = ("720x576", "25", "pal-dvd") if pal else ("720x480", "30000/1001", "ntsc-dvd")
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-target", target, "-aspect", "4:3", "-b:v", "3000k",
                    "-sc_threshold", "1000000000", *video, *audio,
                    "-f", "dvd", "-muxrate", "10080000", "-packetsize", "2048", path], check=True)


def author(folder, mpg, pal, chapters):
    out = os.path.join(folder, "dvd")
    xml = os.path.join(folder, "dvd.xml")
    with open(xml, "w") as f:
        f.write(f"""<dvdauthor dest="{out}">
  <vmgm />
  <titleset><titles>
    <pgc><vob file="{mpg}" chapters="{','.join(str(c) for c in chapters)}" /></pgc>
  </titles></titleset>
</dvdauthor>
""")
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": "PAL" if pal else "NTSC"})
    return os.path.join(out, "VIDEO_TS")


def nav_fields(data):
    """The PCI and DSI fields of a navigation pack, by name."""
    pci, dsi = ps.parse_pack(data).nav()
    return {**{"pci." + k: v for k, v in nav.parse_pci(data[pci.start:pci.end]).items()},
            **{"dsi." + k: v for k, v in nav.parse_dsi(data[dsi.start:dsi.end]).items()}}


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
class TestAgainstDvdauthor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-vob-")
        cls.discs = {}
        for name, (seconds, pal, chapters, video, audio) in STREAMS.items():
            folder = os.path.join(cls.tmp, name)
            os.mkdir(folder)
            mpg = os.path.join(folder, "title.mpg")
            encode(mpg, seconds, pal, video, audio)
            video_ts = author(folder, mpg, pal, chapters)
            cls.discs[name] = (mpg, video_ts, pal, chapters)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def check(self, name):
        mpg, video_ts, pal, chapters = self.discs[name]
        theirs = os.path.join(video_ts, "VTS_01_1.VOB")
        ours = os.path.join(os.path.dirname(mpg), "ours.vob")
        stream = vob.scan(mpg)
        starts = vob.cells_for(stream, chapters)
        self.assertEqual(len(starts), len(chapters), "a chapter per cell")
        cells = vob.write(stream, ours, starts)
        self.assertEqual(os.path.getsize(ours), os.path.getsize(theirs))
        with open(ours, "rb") as f, open(theirs, "rb") as g:
            for v in stream.vobus:
                f.seek(v.sector * ps.SECTOR)
                g.seek(v.sector * ps.SECTOR)
                a, b = f.read(v.sectors * ps.SECTOR), g.read(v.sectors * ps.SECTOR)
                if a != b:
                    mine, dvdauthor = nav_fields(a[:ps.SECTOR]), nav_fields(b[:ps.SECTOR])
                    diff = {k: (mine[k], dvdauthor[k]) for k in mine if mine[k] != dvdauthor[k]}
                    self.fail(f"{name}: VOBU at sector {v.sector} differs: {diff or 'outside the NAV pack'}")
        return stream, cells

    def test_chapters(self):
        stream, cells = self.check("chapters")
        self.assertEqual([c.number for c in cells], [1, 2, 3])
        self.assertEqual(cells[0].first_sector, 0)
        self.assertEqual(cells[-1].last_sector, stream.sectors - 1)
        for a, b in zip(cells, cells[1:]):
            self.assertEqual(a.last_sector + 1, b.first_sector)
            self.assertEqual(a.e_ptm, b.s_ptm)
        self.assertAlmostEqual(cells[1].s_ptm - cells[0].s_ptm, 4 * 90000, delta=0.6 * 90000)

    def test_long(self):
        self.check("long")

    def test_pal(self):
        stream, _ = self.check("pal")
        self.assertEqual(stream.frame_rate_code, 3)

    def test_short_gops(self):
        self.check("short-gops")

    def test_few_refs(self):
        self.check("few-refs")

    def test_lpcm(self):
        stream, _ = self.check("lpcm")
        self.assertEqual(sorted(stream.audio), [0])

    def test_cells_must_be_in_order(self):
        stream = vob.scan(self.discs["chapters"][0])
        with self.assertRaises(VtsError):
            vob.cells(stream, [1])
        with self.assertRaises(VtsError):
            vob.cells(stream, [0, 5, 3])
        with self.assertRaises(VtsError):
            vob.cells(stream, [0, len(stream.vobus)])

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_libdvdnav_plays_our_vob(self):
        # dvdauthor's disc with its title VOB replaced by ours: the three
        # chapters play in order to the end
        mpg, video_ts, pal, chapters = self.discs["chapters"]
        disc = os.path.join(self.tmp, "ours-chapters", "VIDEO_TS")
        shutil.copytree(video_ts, disc)
        stream = vob.scan(mpg)
        vob.write(stream, os.path.join(disc, "VTS_01_1.VOB"), vob.cells_for(stream, chapters))
        seen = []
        with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
            self.assertEqual(d.wait_title(), (1, 1))
            for _ in range(200_000):
                if d.step() == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                    tp = d.title_part()
                    if not seen or seen[-1] != tp:
                        seen.append(tp)
                if d.stopped:
                    break
        self.assertEqual(seen, [(1, 1), (1, 2), (1, 3)])


class TestLayout(unittest.TestCase):
    def test_time_code(self):
        self.assertEqual(nav.time_code(0, 4), bytes.fromhex("000000c0"))
        self.assertEqual(nav.time_code(15, 4), bytes.fromhex("000000d5"))
        self.assertEqual(nav.time_code(448, 4), bytes.fromhex("000014e8"))       # 14 s 28 frames
        self.assertEqual(nav.time_code(36, 3), bytes.fromhex("00000151"))        # PAL: 1 s 11 frames
        self.assertEqual(nav.time_code(30 * 3661 + 7, 4), bytes.fromhex("010101c7"))
        with self.assertRaises(VtsError):
            nav.time_code(1, 2)

    def test_round_trip(self):
        eltm = nav.time_code(7, 4)
        p = nav.pci(1234, 48003, 93048, eltm)
        self.assertEqual(len(p), nav.PCI_SIZE)
        f = nav.parse_pci(p)
        self.assertEqual((f["nv_pck_lbn"], f["vobu_s_ptm"], f["vobu_e_ptm"]), (1234, 48003, 93048))
        self.assertEqual(f["c_eltm"], eltm.hex())
        sri = [nav.SRI_NONE] * 42
        d = nav.dsi(99, 1234, 176, [13, 27, 39], 1, 2, eltm, 48003, 1129083, sri, [2] + [0] * 7)
        self.assertEqual(len(d), nav.DSI_SIZE)
        g = nav.parse_dsi(d)
        self.assertEqual((g["nv_pck_scr"], g["vobu_ea"], g["vobu_ref_ea"], g["vobu_c_idn"]),
                         (99, 176, [13, 27, 39], 2))
        self.assertEqual(g["a_synca"], [2, 0, 0, 0, 0, 0, 0, 0])
        with self.assertRaises(VtsError):
            nav.dsi(0, 0, 0, [1, 2], 1, 1, eltm, 0, 0, sri, [0] * 8)

    def test_pictures(self):
        seq = bytes.fromhex("000001b3 2d01e014 ffffe018".replace(" ", ""))   # frame_rate_code 4
        pic_i = bytes.fromhex("00000100 0008ffff".replace(" ", ""))          # I
        ext = bytes.fromhex("000001b5 8ffff3 42 00".replace(" ", ""))        # frame, tff, rff=1
        pic_p = bytes.fromhex("00000100 0010ffff".replace(" ", ""))          # P
        pics, code, prog = ps.pictures(seq + pic_i + ext + b"\x55" * 10 + pic_p + b"\x66" * 5)
        self.assertEqual(code, 4)
        self.assertEqual([(p.coding, p.fields) for p in pics], [(1, 3), (2, 2)])
        self.assertEqual(pics[0].end, len(seq) + len(pic_i) + len(ext) + 10)
        self.assertTrue(pics[0].reference)

    def test_not_a_pack(self):
        with self.assertRaises(VtsError):
            ps.parse_pack(b"\x00" * ps.SECTOR)
        with self.assertRaises(VtsError):
            ps.parse_pack(b"\x00\x00\x01\xba")


if __name__ == "__main__":
    unittest.main()
