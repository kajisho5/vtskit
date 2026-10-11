"""The video manager vtskit writes (vtskit.vmg), checked against
dvdauthor's VIDEO_TS.IFO for the same titlesets (the same bytes but for
the provider name), and a whole VIDEO_TS written by vtskit alone played
with libdvdnav."""

import os
import shutil
import subprocess
import tempfile
import unittest

from vtskit import VtsError, ifo, titleset, vm, vmg
from vtskit.titleset import Audio, Title

from .test_titleset import encode

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor"))


def author(folder, std, titlesets):
    """dvdauthor's VIDEO_TS for titlesets of [(mpg, "chapters")]."""
    out = os.path.join(folder, "dvd")
    xml = os.path.join(folder, "dvd.xml")
    body = ""
    for pgcs in titlesets:
        body += f'<titleset><titles><video format="{std}"/><audio lang="en"/>'
        body += "".join(f'<pgc><vob file="{mpg}" chapters="{ch}"/></pgc>' for mpg, ch in pgcs)
        body += "</titles></titleset>"
    with open(xml, "w") as f:
        f.write(f'<dvdauthor dest="{out}"><vmgm/>{body}</dvdauthor>')
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": std.upper()})
    return os.path.join(out, "VIDEO_TS")


def read(path):
    with open(path, "rb") as f:
        return f.read()


def play_through(disc, budget=400_000):
    """(title, chapter) in the order libdvdnav plays them until the disc stops."""
    seen = []
    with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
        d.wait_title()
        for _ in range(budget):
            if d.step() == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                tp = d.title_part()
                if not seen or seen[-1] != tp:
                    seen.append(tp)
            if d.stopped:
                break
    return seen


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg and dvdauthor")
class TestAgainstDvdauthor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="vtskit-vmg-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def same(self, std, titlesets):
        """dvdauthor and vtskit write the same VIDEO_TS for these titlesets:
        [[Title, ...], ...]; returns (theirs, mine)."""
        theirs = author(self.tmp, std,
                        [[(t.stream, ",".join(str(c) for c in t.chapters)) for t in ts] for ts in titlesets])
        mine = os.path.join(self.tmp, "mine")
        written = [titleset.write(mine, n, ts) for n, ts in enumerate(titlesets, 1)]
        vmg.write(mine, written)
        a, b = read(os.path.join(mine, "VIDEO_TS.IFO")), read(os.path.join(theirs, "VIDEO_TS.IFO"))
        self.assertEqual(len(a), len(b))
        self.assertEqual(a[:0x40], b[:0x40])
        self.assertEqual(a[0x40:0x60].rstrip(b"\0"), b"vtskit")
        self.assertEqual(b[0x40:0x60].rstrip(b"\0")[:9], b"DVDAuthor")
        if a[0x60:] != b[0x60:]:
            at = 0x60 + next(k for k in range(len(a) - 0x60) if a[0x60 + k] != b[0x60 + k])
            self.fail(f"VIDEO_TS.IFO differs at {at:#x}: mine {a[at:at + 8].hex()} theirs {b[at:at + 8].hex()}")
        self.assertEqual(read(os.path.join(mine, "VIDEO_TS.BUP")), a)
        self.assertEqual(sorted(os.listdir(mine)), sorted(os.listdir(theirs)))
        for name in os.listdir(theirs):
            if name.startswith("VTS_"):
                self.assertEqual(read(os.path.join(mine, name)), read(os.path.join(theirs, name)), name)
        return theirs, mine

    def test_one_titleset(self):
        a, b = os.path.join(self.tmp, "a.mpg"), os.path.join(self.tmp, "b.mpg")
        encode(a, seconds=6)
        encode(b, seconds=3)
        self.same("ntsc", [[Title(a, (0, 2, 4), (Audio("en", 2),)), Title(b, audio=(Audio("en", 2),))]])

    def test_two_titlesets_pal(self):
        a, b = os.path.join(self.tmp, "a.mpg"), os.path.join(self.tmp, "b.mpg")
        encode(a, "pal", 5, audio=("-c:a", "mp2", "-b:a", "192k", "-ac", "2"))
        encode(b, "pal", 4, audio=("-c:a", "mp2", "-b:a", "192k", "-ac", "2"))
        theirs, mine = self.same("pal", [[Title(a, (0, 2), (Audio("en", 2),))],
                                         [Title(b, audio=(Audio("en", 2),))]])
        self.assertEqual(ifo.chapters(ifo.read_folder(mine)), ifo.chapters(ifo.read_folder(theirs)))
        self.assertEqual([c[:3] for c in ifo.chapters(ifo.read_folder(mine))],
                         [(1, 1, 1), (1, 1, 2), (2, 1, 1)])

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_libdvdnav_plays_a_whole_disc(self):
        # a VIDEO_TS written by vtskit alone: two titlesets, three titles;
        # the first-play chain starts title 2 (the second of titleset 1),
        # whose post jumps to the first; that one ends. Title 3 is titleset 2.
        a, b, c = (os.path.join(self.tmp, n) for n in ("a.mpg", "b.mpg", "c.mpg"))
        encode(a, seconds=4)
        encode(b, seconds=3)
        encode(c, seconds=2)
        disc = os.path.join(self.tmp, "disc", "VIDEO_TS")
        written = [titleset.write(disc, 1, [Title(a, (0, 2)),
                                            Title(b, post=vm.assemble([vm.jump_vts_tt(1)]))]),
                   titleset.write(disc, 2, [Title(c)])]
        vmg.write(disc, written, first_play=vm.assemble([vm.jump_tt(2)]))
        self.assertEqual(play_through(disc), [(2, 1), (1, 1), (1, 2)])
        with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
            self.assertEqual(d.num_titles(), 3)
            self.assertEqual([d.num_parts(n) for n in (1, 2, 3)], [2, 1, 1])
        self.assertEqual([ch[:3] for ch in ifo.chapters(ifo.read_folder(disc))],
                         [(1, 1, 1), (1, 1, 2), (1, 2, 1), (2, 1, 1)])

    def test_refused(self):
        a = os.path.join(self.tmp, "a.mpg")
        encode(a, seconds=2)
        ts = titleset.write(os.path.join(self.tmp, "x"), 2, [Title(a)])
        with self.assertRaisesRegex(VtsError, "numbered"):
            vmg.write(os.path.join(self.tmp, "x"), [ts])
        with self.assertRaises(VtsError):
            vmg.write(os.path.join(self.tmp, "x"), [])
        ts = titleset.write(os.path.join(self.tmp, "y"), 1, [Title(a)])
        with self.assertRaisesRegex(VtsError, "8 bytes"):
            vmg.write(os.path.join(self.tmp, "y"), [ts], first_play=[b"\x30"])
        with self.assertRaisesRegex(VtsError, "commands"):
            vmg.write(os.path.join(self.tmp, "y"), [ts], first_play=[])


if __name__ == "__main__":
    unittest.main()
