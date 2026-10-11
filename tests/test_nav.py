"""The navigation packs vtskit fills, checked against dvdauthor's.

The same program stream (ffmpeg -f dvd, as oncemore encodes) goes to
dvdauthor and to vtskit.nav: every VOB written must be the same, byte for
byte, for NTSC and PAL, AC-3, LPCM and MPEG sound, B-pictures, short GOPs,
a long title (the search pointers of 10 s and more), two sound tracks and
a subtitle track (spumux). The cells come from dvdauthor's own packs here;
choosing them is the titleset writer's job."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest
import zlib

from vtskit import VtsError, nav, ps

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor"))
SPUMUX_OK = bool(shutil.which("spumux"))


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], check=True)


def encode(path, std="ntsc", seconds=8, extra=(), audio=("-c:a", "ac3")):
    size, rate = ("720x480", "30000/1001") if std == "ntsc" else ("720x576", "25")
    ffmpeg("-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d={seconds}",
           "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
           *extra, *audio, "-target", f"{std}-dvd", "-aspect", "16:9", path)


def png(path, w, h, box):
    """An indexed PNG of a white box on a transparent ground (what spumux reads)."""
    x0, y0, x1, y1 = box
    rows = b"".join(b"\x00" + bytes(1 if x0 <= x < x1 and y0 <= y < y1 else 0 for x in range(w))
                    for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + \
            struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0)) +
                chunk(b"PLTE", bytes((0, 0, 0, 255, 255, 255, 0, 0, 0, 128, 128, 128))) +
                chunk(b"tRNS", bytes((0, 255, 255, 255))) +
                chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def author(folder, mpg, std, chapters, streams=""):
    xml = os.path.join(folder, "dvd.xml")
    with open(xml, "w") as f:
        f.write(f'<dvdauthor dest="{folder}/dvd"><vmgm/><titleset><titles>'
                f'<video format="{std}"/>{streams}<pgc><vob file="{mpg}" chapters="{chapters}"/>'
                f'</pgc></titles></titleset></dvdauthor>')
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": std.upper()})
    vts = os.path.join(folder, "dvd", "VIDEO_TS")
    data = b""
    for name in sorted(os.listdir(vts)):
        if name.startswith("VTS_01_") and name.endswith(".VOB") and name != "VTS_01_0.VOB":
            with open(os.path.join(vts, name), "rb") as f:
                data += f.read()
    return data


def cells_of(vob):
    """[(vob id, cell id, first VOBU, last VOBU)] as a VOB's own DSI packets say."""
    cells = []
    navs = [p.sector for p in ps.read_packs(vob) if p.nav]
    for k, s in enumerate(navs):
        dsi = vob[s * ps.SECTOR + nav.DSI:]
        key = (int.from_bytes(dsi[0x18:0x1A], "big"), dsi[0x1B])
        if cells and cells[-1][:2] == key:
            cells[-1] = (*key, cells[-1][2], k)
        else:
            cells.append((*key, k, k))
    return cells


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
class TestAgainstDvdauthor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vtskit-nav-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def same(self, mpg, std, chapters, streams=""):
        theirs = author(self.tmp, mpg, std, chapters, streams)
        with open(mpg, "rb") as f:
            mine = ps.drop_padding(f.read())
        vs = nav.fill(mine, cells_of(theirs))
        self.assertEqual(len(mine), len(theirs))
        for v in vs:
            a = mine[v.start * ps.SECTOR:(v.start + 1) * ps.SECTOR]
            b = theirs[v.start * ps.SECTOR:(v.start + 1) * ps.SECTOR]
            if a != b:
                at = next(k for k in range(ps.SECTOR) if a[k] != b[k])
                self.fail(f"navigation pack at sector {v.start} differs at byte {at:#x}")
        self.assertEqual(bytes(mine), theirs)
        return vs

    def mpg(self, **kw):
        path = os.path.join(self.tmp, "in.mpg")
        encode(path, **kw)
        return path

    def test_ntsc_ac3(self):
        vs = self.same(self.mpg(), "ntsc", "0:00,0:02,0:04")
        self.assertGreater(len(vs), 8)

    def test_pal_lpcm(self):
        self.same(self.mpg(std="pal", seconds=10,
                           audio=("-c:a", "pcm_s16be", "-af", "asetnsamples=n=80")),
                  "pal", "0:00,0:04")

    def test_mpeg_audio(self):
        self.same(self.mpg(std="pal", seconds=6, audio=("-c:a", "mp2")), "pal", "0:00,0:03")

    def test_b_pictures(self):
        self.same(self.mpg(extra=("-bf", "2")), "ntsc", "0:00,0:03")

    def test_short_gops(self):
        # VOBUs of 0.2 s: the 0.5 s search spans skip VOBUs
        self.same(self.mpg(extra=("-g", "6")), "ntsc", "0:00,0:02,0:05")

    def test_long_title(self):
        # cells of 12 and 18 s: the search spans of 10 and 30 s
        self.same(self.mpg(seconds=40), "ntsc", "0:00,0:12,0:30")

    @unittest.skipUnless(SPUMUX_OK, "needs spumux")
    def test_two_sound_tracks_and_subtitles(self):
        plain = os.path.join(self.tmp, "plain.mpg")
        ffmpeg("-f", "lavfi", "-i", "testsrc2=s=720x480:r=30000/1001:d=8",
               "-f", "lavfi", "-i", "sine=f=440:d=8", "-f", "lavfi", "-i", "sine=f=660:d=8",
               "-map", "0:v", "-map", "1:a", "-map", "2:a", "-target", "ntsc-dvd",
               "-aspect", "16:9", "-c:a:0", "ac3", "-c:a:1", "pcm_s16be",
               "-af:a:1", "asetnsamples=n=80", plain)
        png(os.path.join(self.tmp, "sub.png"), 720, 480, (100, 380, 400, 420))
        xml = os.path.join(self.tmp, "spu.xml")
        with open(xml, "w") as f:
            f.write('<subpictures><stream>'
                    f'<spu start="00:00:01.00" end="00:00:02.50" image="{self.tmp}/sub.png"/>'
                    f'<spu start="00:00:04.00" end="00:00:05.00" image="{self.tmp}/sub.png"/>'
                    '</stream></subpictures>')
        mpg = os.path.join(self.tmp, "in.mpg")
        with open(plain, "rb") as src, open(mpg, "wb") as dst:
            subprocess.run(["spumux", "-s0", xml], stdin=src, stdout=dst, check=True,
                           capture_output=False, stderr=subprocess.DEVNULL)
        with open(mpg, "rb") as f:
            packs = ps.read_packs(ps.drop_padding(f.read()))
        self.assertEqual(len(ps.audio_streams(packs)), 2)
        self.same(mpg, "ntsc", "0:00,0:03",
                  '<audio lang="en"/><audio lang="ja"/><subpicture lang="en"/>')


class TestPieces(unittest.TestCase):
    def test_elapsed(self):
        self.assertEqual(nav.elapsed(0, 3003), bytes((0, 0, 0, 0xC0)))
        self.assertEqual(nav.elapsed(36 * 3003, 3003), bytes((0, 0, 1, 0xC6)))
        self.assertEqual(nav.elapsed(3600 * 90000 + 61 * 90000, 3600),
                         bytes((0x01, 0x01, 0x01, 0x40)))

    def test_not_a_stream(self):
        with self.assertRaises(VtsError):
            ps.read_packs(b"\x00" * 100)
        with self.assertRaises(VtsError):
            ps.read_packs(b"\x00" * 2048)

    def test_padding_dropped(self):
        pack = bytearray(2048)
        pack[:14] = bytes.fromhex("000001ba4400040004010189c3f8")
        pack[14:20] = b"\x00\x00\x01\xbe" + (2048 - 20).to_bytes(2, "big")
        self.assertEqual(ps.drop_padding(bytes(pack) * 3), b"")
        self.assertEqual(ps.read_packs(bytes(pack))[0].pes, [])


if __name__ == "__main__":
    unittest.main()
