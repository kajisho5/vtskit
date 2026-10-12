"""Angle blocks (vtskit.titleset.Angles): the interleaved layout, every
pointer of the navigation packs landing where the descriptions say, the
cells and tables of the IFO, libdvdnav playing and switching the angles,
and the pictures it hands a player decoded, through a switch. dvdauthor writes no angles, so there is nothing of its to compare
with; the structure is checked against itself and the descriptions."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from vtskit import VtsError, ifo, ps, titleset, vm, vmg
from vtskit.titleset import Angles, Title

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg"))


def encode(path, seconds, colour, gop=15, extra=()):
    """A stream like oncemore's, a coloured frame around the test picture;
    the same arguments give angles the same GOPs and VOBUs."""
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc2=s=720x480:r=30000/1001:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",
                    "-vf", f"drawbox=c={colour}:t=40", "-target", "ntsc-dvd", "-aspect", "4:3",
                    "-b:v", "3000k", "-g", str(gop), "-sc_threshold", "1000000000", *extra,
                    "-c:a", "ac3", "-ac", "2", path], check=True)


def border(path, frames=None):
    """The colour of each picture's frame (the drawbox), as (Y, Cb, Cr)."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path] +
                         (["-frames:v", str(frames)] if frames else []) +
                         ["-vf", "crop=20:20:5:5,scale=1:1:flags=area,format=yuv444p",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]


def play(disc, switch=None):
    """What libdvdnav hands a player, title domain only, as one program
    stream (the VOBUs as delivered, navigation packs included), and the
    angle switches made: `switch` = (after that many navigation packs, to
    angle n)."""
    blocks, done = [], []
    with dvdnav_sim.DvdSim(disc, LIBDVDNAV) as d:
        d.wait_title()
        navs = 0
        for _ in range(2_000_000):
            e = d.step()
            if e in (dvdnav_sim.EV_BLOCK_OK, dvdnav_sim.EV_NAV_PACKET) and d.domain() == "title":
                blocks.append(bytes(d.buf.raw[:2048]))
            if e == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                navs += 1
                if switch and not done and navs == switch[0]:
                    done.append(d.change_angle(switch[1]))
            if d.stopped:
                break
    return b"".join(blocks), done


def runs(colours, names):
    """The colours as [(name, pictures in a row)], each picture named after
    the nearest of `names` ({name: (Y, Cb, Cr)})."""
    out = []
    for c in colours:
        n = min(names, key=lambda k: sum((a - b) ** 2 for a, b in zip(names[k], c)))
        if out and out[-1][0] == n:
            out[-1][1] += 1
        else:
            out.append([n, 1])
    return [tuple(x) for x in out]


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def nav_packs(vob):
    """{sector: (PCI data, DSI data)} of a VOB."""
    out = {}
    for pk in ps.iter_packs(vob):
        if pk.nav:
            o = pk.sector * ps.SECTOR
            out[pk.sector] = (vob[o + 0x2D:o + 0x2D + 979], vob[o + 0x407:o + 0x407 + 1017])
    return out


def pgc_cells(data):
    """[(category bytes, time, first, first ILVU end, last VOBU, last, vob, cell)] of title PGC 1."""
    pgci = u32(data, 0xCC) * ps.SECTOR
    p = pgci + u32(data, pgci + 12)
    n = data[p + 3]
    cpi, cpo = u16(data, p + 0xE8), u16(data, p + 0xEA)
    out = []
    for k in range(n):
        e = p + cpi + 24 * k
        pos = p + cpo + 4 * k
        out.append((data[e:e + 4], data[e + 4:e + 8], u32(data, e + 8), u32(data, e + 12),
                    u32(data, e + 16), u32(data, e + 20), u16(data, pos), data[pos + 3]))
    return out, list(data[p + u16(data, p + 0xE6):p + u16(data, p + 0xE6) + data[p + 2]])


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg")
class TestAngleBlock(unittest.TestCase):
    """A title of an intro, a block of three angles (two chapters in it), and
    an outro; a second plain title."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-angles-")
        t = cls.tmp
        intro, outro, plain = (os.path.join(t, n) for n in ("intro.mpg", "outro.mpg", "plain.mpg"))
        encode(intro, 3, "white")
        encode(outro, 2, "black")
        encode(plain, 2, "gray")
        cls.angles = [os.path.join(t, f"angle{k}.mpg") for k in range(3)]
        for path, colour in zip(cls.angles, ("red", "green", "blue")):
            encode(path, 6, colour)
        cls.disc = os.path.join(t, "disc")
        cls.title = Title(parts=(intro, Angles(tuple(cls.angles)), outro), chapters=(0, 3, 6, 9),
                          post=vm.assemble([vm.jump_vts_tt(2)]))
        cls.ts = titleset.write(cls.disc, 1, [cls.title, Title(plain)])
        vmg.write(cls.disc, [cls.ts])
        cls.ifo = open(os.path.join(cls.disc, "VTS_01_0.IFO"), "rb").read()
        cls.vob = b"".join(open(os.path.join(cls.disc, n), "rb").read()
                           for n in sorted(cls.ts.files) if n.endswith(".VOB"))
        cls.navs = nav_packs(cls.vob)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_cells(self):
        cells, program_map = pgc_cells(self.ifo)
        # intro; 3 angles of chapter 2; 3 angles of chapter 3; outro
        self.assertEqual(len(cells), 8)
        self.assertEqual(program_map, [1, 2, 5, 8])
        self.assertEqual([c[6:] for c in cells],
                         [(1, 1), (2, 1), (3, 1), (4, 1), (2, 2), (3, 2), (4, 2), (5, 1)])
        cats = [c[0][0] for c in cells]
        # intro: STC discontinuity. angle cells: first/middle/last of an angle
        # block, interleaved, the first stretch with STC discontinuity, the
        # second seamless and "seamless angle linked in DSI"
        self.assertEqual(cats[0], 0x02)
        # (every cell of a seamless block is "seamless angle linked in DSI",
        # the first stretch's too: libdvdnav switches it at a unit's end)
        self.assertEqual(cats[1:4], [0x40 | 0x10 | 0x04 | 0x02 | 0x01, 0x80 | 0x10 | 0x04 | 0x02 | 0x01,
                                     0xC0 | 0x10 | 0x04 | 0x02 | 0x01])
        self.assertEqual(cats[4:7], [0x40 | 0x10 | 0x08 | 0x04 | 0x01, 0x80 | 0x10 | 0x08 | 0x04 | 0x01,
                                     0xC0 | 0x10 | 0x08 | 0x04 | 0x01])
        self.assertEqual(cats[7], 0x02)
        for c in cells[1:7]:
            self.assertGreater(c[3], c[2])            # the first ILVU ends after the cell starts
            self.assertLessEqual(c[3], c[5])
        for c in (cells[0], cells[7]):
            self.assertEqual(c[3], 0)
        # the angles of a stretch have the same playing time
        self.assertEqual(len({c[1] for c in cells[1:4]}), 1)
        self.assertEqual(len({c[1] for c in cells[4:7]}), 1)
        self.assertEqual(self.ts.titles[0].angles, 3)
        self.assertEqual(self.ts.titles[1].angles, 1)

    def test_layout_and_tables(self):
        # every sector of the title VOBs belongs to exactly one VOBU, the VOBU
        # map lists them in order, the cell table covers each cell's range
        admap = u32(self.ifo, 0xE4) * ps.SECTOR
        n = (u32(self.ifo, admap) + 1 - 4) // 4
        sectors = [u32(self.ifo, admap + 4 + 4 * k) for k in range(n)]
        self.assertEqual(sectors, sorted(self.navs))
        self.assertEqual(sectors[0], 0)
        total = len(self.vob) // ps.SECTOR
        ends = sectors[1:] + [total]
        for s, e in zip(sectors, ends):
            self.assertEqual(u32(self.navs[s][1], 8), e - s - 1, f"VOBU at {s}: end address")
        c_adt = u32(self.ifo, 0xE0) * ps.SECTOR
        self.assertEqual(u16(self.ifo, c_adt), 6)       # VOBs: intro, 3 angles, outro, title 2
        entries = [(u16(self.ifo, o), self.ifo[o + 2], u32(self.ifo, o + 4), u32(self.ifo, o + 8))
                   for o in range(c_adt + 8, c_adt + u32(self.ifo, c_adt + 4) + 1, 12)]
        self.assertEqual([e[:2] for e in entries],
                         [(1, 1), (2, 1), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2), (5, 1), (6, 1)])
        for vob, cell, start, end in entries:
            self.assertEqual(u16(self.navs[start][1], 0x18), vob)
            self.assertEqual(self.navs[start][1][0x1B], cell)
            self.assertGreater(end, start)
        # the VMG counts the angles
        vmg_ifo = open(os.path.join(self.disc, "VIDEO_TS.IFO"), "rb").read()
        tt = u32(vmg_ifo, 0xC4) * ps.SECTOR
        self.assertEqual((vmg_ifo[tt + 9], vmg_ifo[tt + 21]), (3, 1))
        self.assertEqual([c[:3] for c in ifo.chapters(ifo.read_folder(self.disc))],
                         [(1, 1, 1), (1, 1, 2), (1, 1, 3), (1, 1, 4), (1, 2, 1)])

    def test_interleave_pointers(self):
        cells, _ = pgc_cells(self.ifo)
        by_vob = {}
        for s, (pci, dsi) in self.navs.items():
            by_vob.setdefault(u16(dsi, 0x18), []).append(s)
        angle_vobs = [2, 3, 4]
        # the angles' VOBUs take turns on the disc: unit by unit, angle by angle
        block = sorted(s for v in angle_vobs for s in by_vob[v])
        order = [u16(self.navs[s][1], 0x18) for s in block]
        self.assertEqual(order[0], 2)
        self.assertEqual(set(order), {2, 3, 4})
        for s in block:
            pci, dsi = self.navs[s]
            vob = u16(dsi, 0x18)
            flags, ea, nxt_sa, nxt_sz = struct.unpack_from(">HIIH", dsi, 0x20)
            self.assertTrue(flags & 0x4000, "ILVU flag")
            # the unit ends at a VOBU boundary or the block's end
            unit_end = s + ea
            self.assertTrue(unit_end + 1 in self.navs or unit_end + 1 == len(self.vob) // ps.SECTOR
                            or unit_end + 1 == min(by_vob[5]))
            unit_vobus = [x for x in block if s - ea <= x <= unit_end and u16(self.navs[x][1], 0x18) == vob]
            first_in_unit = min(x for x in unit_vobus if x <= s) if (flags & 0x2000) else None
            if flags & 0x2000:
                self.assertEqual(first_in_unit, s, "Unit_Start on the unit's first VOBU")
            if flags & 0x1000:
                self.assertEqual(max(unit_vobus), s, "Unit_End on the unit's last VOBU")
            if nxt_sa != 0xFFFFFFFF:
                target = s + nxt_sa
                tdsi = self.navs[target][1]
                self.assertEqual(u16(tdsi, 0x18), vob, "the next unit is this angle's")
                self.assertTrue(u16(tdsi, 0x20) & 0x2000, "the next unit starts there")
                self.assertEqual(nxt_sz, u32(tdsi, 0x22) + 1, "and is that large")
            else:
                self.assertEqual(nxt_sz, 0xFFFF)
            for b in range(9):
                dsta, size = struct.unpack_from(">IH", dsi, 0xB4 + 6 * b)
                if b >= 3:
                    self.assertEqual((dsta, size), (0, 0))
                elif nxt_sa == 0xFFFFFFFF:
                    self.assertEqual((dsta, size), (0x7FFFFFFF, 0))
                else:
                    target = s + dsta
                    tdsi = self.navs[target][1]
                    self.assertEqual(u16(tdsi, 0x18), 2 + b, f"angle {b + 1}'s next unit")
                    self.assertTrue(u16(tdsi, 0x20) & 0x2000)
                    self.assertEqual(size, u32(tdsi, 0x22) + 1)
                    self.assertGreater(target, unit_end)
            # NSML_AGLI is left empty in a seamless block (TestBreak fills it)
            self.assertEqual(pci[0x3C:0x60], bytes(36))
        # the VOB start and end times of every angle VOBU are its own VOB's
        for s in block:
            dsi = self.navs[s][1]
            vob = u16(dsi, 0x18)
            firsts = [self.navs[x][0] for x in by_vob[vob]]
            self.assertEqual(u32(dsi, 0x2C), min(u32(p, 0x0C) for p in firsts))
        # PREU on the last three intro VOBUs, nowhere else outside the block
        intro = sorted(by_vob[1])
        flags = [u16(self.navs[s][1], 0x20) for s in intro]
        self.assertEqual(flags[-3:], [0xA000, 0x8000, 0x9000])
        self.assertTrue(all(f == 0 for f in flags[:-3]))
        self.assertTrue(all(u16(self.navs[s][1], 0x20) == 0 for s in by_vob[5]))

    def test_search_pointers_stay_in_the_angle(self):
        # the DSI's search table of an angle's VOBU points at the same angle's VOBUs
        for s, (pci, dsi) in self.navs.items():
            vob = u16(dsi, 0x18)
            for k in range(42):
                p = u32(dsi, 0xEA + 4 * k)
                if p & 0x80000000 and p & 0x3FFFFFFF not in (0, 0x3FFFFFFF):
                    sign = -1 if 21 <= k <= 41 else 1
                    target = s + sign * (p & 0x3FFFFFFF)
                    self.assertIn(target, self.navs, f"VOBU at {s}, pointer {k}")
                    self.assertEqual(u16(self.navs[target][1], 0x18), vob, f"VOBU at {s}, pointer {k}")

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_what_the_player_shows(self):
        # the pictures libdvdnav hands a player through a switch from angle 1
        # to 2, decoded: every picture once, in order (as many as the sources
        # have), the switch at the end of an interleaved unit
        stream, done = play(self.disc, switch=(9, 2))
        self.assertEqual(done, [True])
        path = os.path.join(self.tmp, "played.mpg")
        with open(path, "wb") as f:
            f.write(stream)
        sources = [os.path.join(self.tmp, n) for n in ("intro.mpg", "outro.mpg", "plain.mpg")] + \
            self.angles
        names = dict(zip(("white", "black", "gray", "red", "green", "blue"),
                         (border(p, 1)[0] for p in sources)))
        seen = runs(border(path), names)
        self.assertEqual([n for n, _ in seen], ["white", "red", "green", "black", "gray"], seen)
        counts = dict(seen)
        self.assertEqual((counts["white"], counts["black"], counts["gray"]), (90, 60, 60))
        self.assertEqual(counts["red"] + counts["green"], 180)      # nothing twice, nothing lost
        self.assertEqual(counts["red"] % 30, 0)                     # at a unit's end (a second)
        self.assertGreater(counts["red"], 15 * 3)                   # (after the 9th VOBU played)

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_libdvdnav_switches_angles(self):
        played = []
        with dvdnav_sim.DvdSim(self.disc, LIBDVDNAV) as d:
            self.assertEqual(d.num_angles(1), 3)
            self.assertEqual(d.num_angles(2), 1)
            self.assertEqual(d.wait_title(), (1, 1))
            switched = False
            for _ in range(600_000):
                e = d.step()
                if e == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                    tp, vc = d.title_part(), d.vob_cell()
                    if not played or played[-1] != (tp, vc):
                        played.append((tp, vc))
                    if tp == (1, 2) and not switched and vc[0] == 2:
                        self.assertEqual(d.angle(), (1, 3))
                        self.assertTrue(d.change_angle(2))
                        switched = True
                if d.stopped:
                    break
        vobs = [vc[0] for tp, vc in played]
        self.assertEqual(vobs[0], 1)                      # the intro
        self.assertIn(2, vobs)                            # angle 1 played
        self.assertIn(3, vobs)                            # then angle 2, after the switch
        self.assertNotIn(4, vobs)                         # never angle 3
        self.assertLess(vobs.index(2), vobs.index(3))
        self.assertEqual(vobs[-2:], [5, 6])               # the outro, then title 2
        self.assertEqual(played[-1][0], (2, 1))
        chapters = [tp for tp, _ in played]
        self.assertEqual([c for i, c in enumerate(chapters) if i == 0 or chapters[i - 1] != c],
                         [(1, 1), (1, 2), (1, 3), (1, 4), (2, 1)])

    def test_open_groups_refused(self):
        # B-pictures shown before a unit's I-picture lean on the group before
        # (another angle's after a switch) unless the group is closed
        a, b = (os.path.join(self.tmp, f"open{k}.mpg") for k in (1, 2))
        for path, colour in ((a, "red"), (b, "green")):
            encode(path, 4, colour, extra=("-bf", "2"))
        with self.assertRaisesRegex(VtsError, "closed groups"):
            titleset.write(os.path.join(self.tmp, "open"), 1, [Title(parts=(Angles((a, b)),))])
        for path, colour in ((a, "red"), (b, "green")):
            encode(path, 4, colour, extra=("-bf", "2", "-flags", "+cgop"))
        titleset.write(os.path.join(self.tmp, "closed"), 1, [Title(parts=(Angles((a, b)),))])

    def test_refused(self):
        short = os.path.join(self.tmp, "short.mpg")
        encode(short, 4, "red")
        with self.assertRaisesRegex(VtsError, "differs"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(parts=(Angles((self.angles[0], short)),))])
        with self.assertRaisesRegex(VtsError, "2 to 9"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(parts=(Angles((self.angles[0],)),))])
        with self.assertRaisesRegex(VtsError, "not both"):
            titleset.write(os.path.join(self.tmp, "x"), 1, [Title(self.angles[0], parts=(self.angles[1],))])


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg")
class TestBreak(unittest.TestCase):
    """A title that is one angle block, switched with a break (not
    seamless), as a title of oncemore's angles is laid out: NSML_AGLI
    filled, SML_AGLI empty, cells not marked seamless."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-angles-break-")
        cls.angles = [os.path.join(cls.tmp, f"angle{k}.mpg") for k in range(2)]
        for path, colour in zip(cls.angles, ("red", "green")):
            encode(path, 4, colour)
        cls.disc = os.path.join(cls.tmp, "disc")
        cls.ts = titleset.write(cls.disc, 1, [Title(parts=(Angles(tuple(cls.angles), seamless=False),),
                                                    chapters=(0, 2))])
        vmg.write(cls.disc, [cls.ts])
        cls.ifo = open(os.path.join(cls.disc, "VTS_01_0.IFO"), "rb").read()
        cls.vob = b"".join(open(os.path.join(cls.disc, n), "rb").read()
                           for n in sorted(cls.ts.files) if n.endswith(".VOB"))
        cls.navs = nav_packs(cls.vob)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_cells(self):
        cells, program_map = pgc_cells(self.ifo)
        self.assertEqual(program_map, [1, 3])
        self.assertEqual([c[0][0] for c in cells],
                         [0x40 | 0x10 | 0x04 | 0x02, 0xC0 | 0x10 | 0x04 | 0x02,
                          0x40 | 0x10 | 0x04, 0xC0 | 0x10 | 0x04])

    def test_pointers(self):
        for s, (pci, dsi) in self.navs.items():
            self.assertTrue(u16(dsi, 0x20) & 0x4000, "ILVU flag")
            self.assertEqual(dsi[0xB4:0xB4 + 54], bytes(54), "SML_AGLI empty")
            for b in range(9):
                dsta = u32(pci, 0x3C + 4 * b)
                if b >= 2:
                    self.assertEqual(dsta, 0)
                    continue
                target = s + (dsta & 0x7FFFFFFF) * (-1 if dsta & 0x80000000 else 1)
                tpci, tdsi = self.navs[target]
                self.assertEqual(u16(tdsi, 0x18), 1 + b)
                self.assertEqual(tpci[0x18:0x1C], pci[0x18:0x1C], "the same cell elapsed time")
                self.assertEqual(tdsi[0x1B], dsi[0x1B], "the same cell")

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_switches(self):
        stream, done = play(self.disc, switch=(3, 2))
        self.assertEqual(done, [True])
        path = os.path.join(self.tmp, "played.mpg")
        with open(path, "wb") as f:
            f.write(stream)
        names = {"red": border(self.angles[0], 1)[0], "green": border(self.angles[1], 1)[0]}
        seen = runs(border(path), names)
        # angle 1, then angle 2 to the end (from the moment it was at: a
        # VOBU may come again, as a break)
        self.assertEqual([n for n, _ in seen], ["red", "green"], seen)
        self.assertGreaterEqual(sum(n for _, n in seen), 120)


if __name__ == "__main__":
    unittest.main()
