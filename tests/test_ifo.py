"""The chapters of a disc read from its IFO files (vtskit.ifo), checked on a
disc dvdauthor writes: where each titleset starts, where each chapter
starts (a navigation pack whose start time is the chapter's), and the
titles, chapters and angles libdvdnav sees on the same disc."""

import os
import shutil
import subprocess
import tempfile
import unittest

from vtskit import VtsError, ifo

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor"))

# titleset 1: one title, chapters asked at 0, 2 and 4 s; titleset 2: one title of
# one chapter. dvdauthor starts a chapter at the VOBU (a GOP: 18 frames, 0.6 s, of
# -target ntsc-dvd) where the time asked falls.
CHAPTERS = [0.0, 2.0, 4.0]
GOP = 18 * 1001 / 30000


def encode(path, seconds, colour):
    """An MPEG-2 program stream as oncemore makes them (ffmpeg -f dvd)."""
    keys = ",".join(str(t) for t in CHAPTERS)
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s=720x480:r=30000/1001:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-vf", f"drawbox=c={colour}:t=20", "-target", "ntsc-dvd", "-aspect", "4:3",
                    "-force_key_frames", keys, "-sc_threshold", "1000000000",
                    "-c:a", "ac3", "-b:a", "192k", path], check=True)


def author(folder):
    out = os.path.join(folder, "dvd")
    a, b = os.path.join(folder, "a.mpg"), os.path.join(folder, "b.mpg")
    encode(a, 6, "red")
    encode(b, 3, "blue")
    xml = os.path.join(folder, "dvd.xml")
    with open(xml, "w") as f:
        f.write(f"""<dvdauthor dest="{out}">
  <vmgm />
  <titleset><titles><video format="ntsc" />
    <pgc><vob file="{a}" chapters="0:00,0:02,0:04" /></pgc>
  </titles></titleset>
  <titleset><titles><video format="ntsc" />
    <pgc><vob file="{b}" /></pgc>
  </titles></titleset>
</dvdauthor>
""")
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": "NTSC"})
    return os.path.join(out, "VIDEO_TS")


def sectors(path):
    return os.path.getsize(path) // ifo.SECTOR


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
class TestDisc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-ifo-")
        cls.video_ts = author(cls.tmp)
        cls.ifos = ifo.read_folder(cls.video_ts)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def files(self, vts):
        names = sorted(n for n in os.listdir(self.video_ts) if n.startswith(f"VTS_{vts:02d}_"))
        return [os.path.join(self.video_ts, n) for n in names]

    def test_folder(self):
        self.assertEqual(sorted(self.ifos), ["VIDEO_TS.IFO", "VTS_01_0.IFO", "VTS_02_0.IFO"])

    def test_titleset_starts(self):
        # On the disc the VMG comes first, then each titleset, each as large as
        # its IFO says (the last sector of the VMG or titleset at 12; an image
        # writer pads the files to that), and a titleset's title VOBs start
        # where its IFO says (0xC4), after the IFO and any menu VOB.
        def size(name):
            return int.from_bytes(self.ifos[name][12:16], "big") + 1
        def title_vobs(vts):
            return int.from_bytes(self.ifos[f"VTS_{vts:02d}_0.IFO"][0xC4:0xC8], "big")
        vmg, vts1 = size("VIDEO_TS.IFO"), size("VTS_01_0.IFO")
        # (the IFO, its backup and the VOBs, as dvdauthor wrote them, fit)
        self.assertLessEqual(sum(sectors(p) for p in self.files(1)), vts1)
        self.assertEqual(title_vobs(1), sectors(self.files(1)[0]))     # (no menu)
        self.assertEqual(ifo.vts_bases(self.ifos),
                         {1: vmg + title_vobs(1), 2: vmg + vts1 + title_vobs(2)})

    def test_chapters(self):
        ch = ifo.chapters(self.ifos)
        self.assertEqual([c[:3] for c in ch], [(1, 1, 1), (1, 1, 2), (1, 1, 3), (2, 1, 1)])
        self.assertEqual(ifo.chapter_cells(self.ifos["VTS_01_0.IFO"])[0], (1, 1, 0))
        sectors_ = [c[3] for c in ch]
        self.assertEqual(sectors_, sorted(set(sectors_)))

    def test_chapter_starts_at_a_navigation_pack(self):
        # the title VOBs of titleset 1 as one run of sectors
        data = b""
        for p in self.files(1):
            if p.endswith(".VOB") and not p.endswith("_0.VOB"):
                with open(p, "rb") as f:
                    data += f.read()
        for (ttn, ch, s), want in zip(ifo.chapter_cells(self.ifos["VTS_01_0.IFO"]), CHAPTERS):
            pack = data[s * ifo.SECTOR:(s + 1) * ifo.SECTOR]
            self.assertEqual(pack[:4], b"\x00\x00\x01\xba", f"chapter {ch}: no pack")
            # pack header (14), system header (24), then the PCI packet:
            # private stream 2, substream 0
            self.assertEqual(pack[38:42], b"\x00\x00\x01\xbf", f"chapter {ch}: no nav pack")
            self.assertEqual(pack[44], 0)
            # the PCI's start time of the VOBU (90 kHz), 12 bytes in
            start = int.from_bytes(pack[45 + 12:45 + 16], "big") / 90000
            first = int.from_bytes(data[45 + 12:45 + 16], "big") / 90000
            self.assertTrue(want - GOP - 0.01 < start - first <= want + 0.01,
                            f"chapter {ch} starts at {start - first:.3f} s, asked {want}")

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_libdvdnav_agrees(self):
        with dvdnav_sim.DvdSim(self.video_ts, LIBDVDNAV) as d:
            self.assertEqual(d.num_titles(), 2)
            self.assertEqual([d.num_parts(1), d.num_parts(2)], [3, 1])
            self.assertEqual([d.num_angles(1), d.num_angles(2)], [1, 1])
            self.assertEqual(d.wait_title(), (1, 1))
            self.assertEqual(d.angle(), (1, 1))
            self.assertFalse(d.change_angle(2))             # there is no second angle


class TestErrors(unittest.TestCase):
    def test_not_an_ifo(self):
        with self.assertRaises(VtsError):
            ifo.chapter_cells(b"x" * 4096)
        with self.assertRaises(VtsError):
            ifo.vts_bases({"VIDEO_TS.IFO": b"DVDVIDEO-VTS" + bytes(4096)})
        with self.assertRaises(VtsError):
            ifo.vts_bases({})

    def test_damaged(self):
        # tables pointing past the end, cut short at every length
        vts = bytearray(b"DVDVIDEO-VTS" + bytes(0x100))
        vts[0xC8:0xCC] = (99).to_bytes(4, "big")
        with self.assertRaises(VtsError):
            ifo.chapter_cells(bytes(vts))
        vmg = bytearray(b"DVDVIDEO-VMG" + bytes(0x100))
        vmg[0xC4:0xC8] = (99).to_bytes(4, "big")
        with self.assertRaises(VtsError):
            ifo.vts_bases({"VIDEO_TS.IFO": bytes(vmg)})

    @unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
    def test_truncated(self):
        tmp = tempfile.mkdtemp(prefix="vtskit-ifo-")
        self.addCleanup(shutil.rmtree, tmp, True)
        ifos = ifo.read_folder(author(tmp))
        whole = ifos["VTS_01_0.IFO"]
        for n in range(0, len(whole), 97):
            try:
                ifo.chapter_cells(whole[:n])
            except VtsError:
                pass
        missing = dict(ifos)
        del missing["VTS_02_0.IFO"]
        with self.assertRaisesRegex(VtsError, "VTS_02_0.IFO is missing"):
            ifo.vts_bases(missing)


if __name__ == "__main__":
    unittest.main()
