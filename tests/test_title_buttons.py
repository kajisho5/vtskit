"""Buttons over a stretch of a title (vtskit.menu.TitleButtons): the VOB
checked byte for byte against dvdauthor's for buttons over the whole
title, the highlight information only in the VOBUs of a stretch, and
libdvdnav offering the buttons during the stretch alone and running their
commands."""

import ctypes
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from vtskit import VtsError, menu, ps, spu, subtitles, titleset, vm, vmg
from vtskit.menu import Button, TitleButtons
from vtskit.titleset import Audio, Title

from .test_menu import RECTS, png
from .test_titleset import encode

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor") and shutil.which("spumux"))
ENV = {**os.environ, "VIDEO_FORMAT": "NTSC"}
YELLOW, RED = (0xD2, 0x92, 0x10), (0x52, 0xF0, 0x5A)


def read(path):
    with open(path, "rb") as f:
        return f.read()


def buttons(start, end, first=(vm.jump_vts_ptt(1, 1), vm.exit())):
    return TitleButtons(start, end, [Button(RECTS[0], first[0].encode(), up=2, down=2),
                                     Button(RECTS[1], first[1].encode(), up=1, down=1)],
                        highlight=(0, 15), select=(1, 15))


def navs(vob):
    """(sector, VOBU s_ptm, the PCI's highlight information) of every navigation pack."""
    out = []
    for pk in ps.iter_packs(vob):
        if pk.nav:
            o = pk.sector * ps.SECTOR + 0x2D
            out.append((pk.sector, struct.unpack_from(">I", vob, o + 0x0C)[0], vob[o + 0x60:o + 0x316]))
    return out


class TestFields(unittest.TestCase):
    def test_hli(self):
        h = buttons(1.0, 2.0).hli(1000, 2000)
        self.assertEqual(struct.unpack_from(">HIIIHBBBBBB", h, 0),
                         (1, 1000, 2000, 2000, 0x1000, 0, 2, 2, 0, 0, 0))
        self.assertEqual(h[0x16:0x1E].hex(), "000000f0001000f0")       # dvdauthor's for a title
        self.assertEqual(h[0x2E:0x34].hex(), "46412c06408c")      # colour table 1

    def test_cue(self):
        c = buttons(1.0, 3.5).cue(720, 480)
        self.assertEqual((c.start, c.end, c.x, c.y, c.forced), (1.0, 3.5, 100, 100, True))
        self.assertEqual((c.picture.width, c.picture.height), (200, 140))
        self.assertEqual((c.colours, c.contrasts), ((0, 0, 0, 0), (0, 0, 0, 0)))
        area, pixels, cmds = spu.decode(subtitles.unit(c))
        self.assertEqual(area, (100, 299, 100, 239))
        self.assertEqual(cmds[0], 0x00)                                # forced start
        self.assertEqual(sum(1 for v in pixels.values() if v == 1), 2 * 200 * 40)
        shown = TitleButtons(0, None, buttons(0, 1).buttons, (0, 15), (1, 15), normal=(2, 9))
        c = shown.cue(720, 480, length=8)
        self.assertEqual((c.end, c.colours, c.contrasts), (8, (0, 2, 0, 0), (0, 9, 0, 0)))
        with self.assertRaisesRegex(VtsError, "length"):
            shown.cue(720, 480)

    def test_refused(self):
        bad = [TitleButtons(2.0, 1.0, buttons(0, 1).buttons, (0, 15), (1, 15)),
               TitleButtons(0, 1, buttons(0, 1).buttons, (16, 15), (1, 15)),
               TitleButtons(0, 1, buttons(0, 1).buttons, (0, 15), (1,)),
               TitleButtons(0, 1, [], (0, 15), (1, 15)),
               TitleButtons(0, 1, [Button((0, 0, 800, 10), bytes(8))], (0, 15), (1, 15)),
               TitleButtons(0, 1, buttons(0, 1).buttons, (0, 15), (1, 15), first_button=3)]
        for tb in bad:
            with self.assertRaises(VtsError, msg=tb):
                menu.check_title_buttons(tb, 720, 480, "t")


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg, spumux and dvdauthor")
class TestTitleButtons(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = t = tempfile.mkdtemp(prefix="vtskit-tbtn-")
        cls.plain = os.path.join(t, "plain.mpg")
        encode(cls.plain, seconds=10)
        cls.short = os.path.join(t, "short.mpg")
        encode(cls.short, seconds=2)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_whole_title_as_dvdauthor(self):
        # spumux's buttons over the whole title, then dvdauthor; vtskit
        # given the same stream and buttons without an end writes the same
        # VOB (dvdauthor writes the HLI in every VOBU, never ending)
        t = self.tmp
        png(f"{t}/hl.png", 720, 480, RECTS, (255, 255, 0))
        png(f"{t}/sel.png", 720, 480, RECTS, (255, 0, 0))
        with open(f"{t}/spu.xml", "w") as f:
            f.write('<subpictures format="NTSC"><stream><spu start="00:00:00.00" end="00:00:10.00" '
                    f'highlight="{t}/hl.png" select="{t}/sel.png">'
                    '<button name="b1" x0="100" y0="100" x1="300" y1="140" up="b2" down="b2"/>'
                    '<button name="b2" x0="100" y0="200" x1="300" y1="240" up="b1" down="b1"/>'
                    '</spu></stream></subpictures>')
        sp = f"{t}/buttons.mpg"
        with open(self.plain, "rb") as src, open(sp, "wb") as dst:
            subprocess.run(["spumux", "-s", "0", f"{t}/spu.xml"], stdin=src, stdout=dst, check=True,
                           stderr=subprocess.DEVNULL, env=ENV)
        with open(f"{t}/dvd.xml", "w") as f:
            f.write(f'<dvdauthor dest="{t}/dvd"><vmgm/><titleset><titles>'
                    '<video format="ntsc" aspect="4:3"/><audio format="ac3" channels="2" lang="en"/>'
                    f'<pgc><vob file="{sp}"/><button name="b1">jump title 1 chapter 1;</button>'
                    '<button name="b2">exit;</button></pgc></titles></titleset></dvdauthor>')
        subprocess.run(["dvdauthor", "-x", f"{t}/dvd.xml"], check=True, capture_output=True, env=ENV)
        mine = f"{t}/mine-whole"
        titleset.write(mine, 1, [Title(sp, audio=(Audio("en", 2),), buttons=(buttons(0, None),))])
        a, b = read(f"{mine}/VTS_01_1.VOB"), read(f"{t}/dvd/VIDEO_TS/VTS_01_1.VOB")
        self.assertEqual([n[2] for n in navs(a)], [n[2] for n in navs(b)])
        self.assertEqual(a, b)

    def stretch_disc(self, start, end):
        """Title 1 (10 s) with buttons from `start` to `end` (button 1:
        title 2, button 2: exit), title 2 short."""
        t = self.tmp
        tb = buttons(start, end, (vm.jump_vts_tt(2), vm.exit()))
        mpg = f"{t}/stretch-{start}-{end}.mpg"
        with open(mpg, "wb") as f:
            f.write(subtitles.mux(read(self.plain), {0: [tb.cue(720, 480)]}))
        out = f"{t}/disc-{start}-{end}"
        ts = titleset.write(out, 1, [Title(mpg, audio=(Audio("en", 2),), palette=(YELLOW, RED),
                                           buttons=(tb,)),
                                     Title(self.short, audio=(Audio("en", 2),))])
        vmg.write(out, [ts])
        return out

    def test_hli_in_the_stretch_only(self):
        out = self.stretch_disc(3.0, 6.0)
        packs = navs(read(f"{out}/VTS_01_1.VOB"))
        packs = packs[:next(k for k in range(1, len(packs)) if packs[k][1] < packs[k - 1][1])]  # title 1's
        s0 = packs[0][1]
        ends = [p[1] for p in packs[1:]] + [None]
        lit = 0
        for (sector, s_ptm, h), e_ptm in zip(packs, ends):
            during = (e_ptm is None or e_ptm > s0 + 270000) and s_ptm < s0 + 540000
            if during:
                lit += 1
                self.assertEqual(struct.unpack_from(">HIII", h, 0),
                                 (1, s0 + 270000, s0 + 540000, s0 + 540000), sector)
                self.assertEqual(h[17], 2)                             # btn_ns
            else:
                self.assertEqual(h, bytes(len(h)), sector)            # hli_ss 0, nothing
        self.assertGreater(lit, 4)
        self.assertLess(lit, len(packs) - 4)

    def test_refused_in_a_title(self):
        def write(*tbs, stream=None):
            titleset.write(os.path.join(self.tmp, "refused"), 1,
                           [Title(stream or self.plain, audio=(Audio("en", 2),), buttons=tbs)])
        with self.assertRaisesRegex(VtsError, "overlap"):
            write(buttons(1, 4), buttons(3, 5))
        with self.assertRaisesRegex(VtsError, "overlap"):
            write(buttons(1, None), buttons(3, 5))
        with self.assertRaisesRegex(VtsError, "one set of buttons"):
            write(buttons(1, 2.8), buttons(2.9, 5))                   # in the VOBU from 2.4 s to 3.0 s
        with self.assertRaisesRegex(VtsError, "after the title ends"):
            write(buttons(30, 40))

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_under_libdvdnav(self):
        out = self.stretch_disc(3.0, 6.0)
        with dvdnav_sim.DvdSim(out, LIBDVDNAV) as d:
            self.assertEqual(d.wait_title(), (1, 1))
            seen = []                                # (VOBU start, buttons) through title 1
            e = dvdnav_sim.EV_NAV_PACKET
            while not d.stopped:                     # (title 1 ends with Exit)
                if e == dvdnav_sim.EV_NAV_PACKET and d.domain() == "title":
                    self.assertEqual(d.title_part()[0], 1)
                    raw = ctypes.string_at(LIBDVDNAV.dvdnav_get_current_nav_pci(d.h), 16)
                    seen.append((int.from_bytes(raw[12:16], sys.byteorder), d.button_count()))
                e = d.step()
        s0 = seen[0][0]
        starts = [s for s, _ in seen]
        ends = starts[1:] + [None]
        expect = [2 if (e is None or e > s0 + 270000) and s < s0 + 540000 else 0
                  for s, e in zip(starts, ends)]
        self.assertEqual([n for _, n in seen], expect)
        self.assertIn(0, expect[:2])
        # the first button, pressed while the buttons are there: title 2
        with dvdnav_sim.DvdSim(out, LIBDVDNAV) as d:
            d.wait_title()
            d.run_until(lambda e: e == dvdnav_sim.EV_NAV_PACKET and d.button_count() > 0)
            self.assertEqual(d.title_part(), (1, 1))
            d.activate(1)
            self.assertEqual(d.wait_title(), (2, 1))


if __name__ == "__main__":
    unittest.main()
