"""Menus (vtskit.menu, vtskit.spu): the subpicture overlay, the highlight
information checked against dvdauthor's bytes for the same buttons, the
menu tables of the IFOs checked field by field against dvdauthor's, and a
disc with menus written by vtskit alone driven with libdvdnav's buttons."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from vtskit import VtsError, menu, spu, titleset, vm, vmg
from vtskit.menu import Button, Menu
from vtskit.titleset import Title

from .test_titleset import encode

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor") and shutil.which("spumux"))

RECTS = [(100, 100, 300, 140), (100, 200, 300, 240)]


def png(path, w, h, rects, colour):
    """A 2-colour indexed PNG (0 transparent, 1 the colour): what oncemore
    gives spumux as highlight and select pictures."""
    import zlib
    rows = b"".join(b"\x00" + bytes(1 if any(x0 <= x < x1 and y0 <= y < y1 for x0, y0, x1, y1 in rects)
                                      else 0 for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
                + chunk(b"PLTE", bytes((0, 0, 0) + colour)) + chunk(b"tRNS", bytes((0, 255)))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def menu_stream(path, seconds=4):
    encode(path, seconds=seconds, audio=("-c:a", "ac3", "-ac", "2"))


def read(path):
    with open(path, "rb") as f:
        return f.read()


class TestOverlay(unittest.TestCase):
    def test_round_trip(self):
        o = spu.Overlay.from_rects(720, 480, RECTS)
        self.assertEqual(o.bounds(), (100, 100, 299, 239))
        unit = spu.encode(o)
        self.assertEqual(len(unit), 308)                   # spumux's size for the same buttons
        area, pixels, cmds = spu.decode(unit)
        self.assertEqual(area, (100, 299, 100, 239))
        self.assertEqual(sum(1 for v in pixels.values() if v), 2 * 200 * 40)
        self.assertEqual(pixels[(150, 120)], 1)
        self.assertEqual(pixels[(150, 170)], 0)
        self.assertEqual(cmds.hex(), "000300000400000506412b0640ef0600040090ff")

    def test_codes_and_runs(self):
        # every code, runs of every length form, an odd number of lines
        w, h = 300, 7
        pixels = bytearray(w * h)
        for y in range(h):
            row = pixels[y * w:(y + 1) * w]
            row[0:2] = b"\x01\x01"
            row[2:12] = b"\x02" * 10
            row[12:60] = b"\x03" * 48
            row[60:200] = b"\x01" * 140
            row[200:299] = b"\x02" * 99
            row[299] = 3 if y % 2 else 0
            pixels[y * w:(y + 1) * w] = row
        o = spu.Overlay(w, h, bytes(pixels))
        area, got, _ = spu.decode(spu.encode(o))
        x0, x1, y0, y1 = area
        self.assertEqual((x0, x1), (0, 299))
        for (x, y), code in got.items():
            self.assertEqual(code, pixels[y * w + x] if y < h else 0, (x, y))

    def test_refused(self):
        with self.assertRaises(VtsError):
            spu.encode(spu.Overlay(4, 4, bytes(16)))          # blank
        with self.assertRaises(VtsError):
            spu.Overlay(4, 4, bytes(15))
        with self.assertRaises(VtsError):
            spu.Overlay(4, 4, bytes([4]) * 16)
        big = spu.Overlay.from_rects(720, 480, [(0, 0, 720, 480)])
        # a checkerboard: a run a pixel, far over the buffer
        pixels = bytes((x + y) % 2 for y in range(480) for x in range(720))
        with self.assertRaisesRegex(VtsError, "at most"):
            spu.encode(spu.Overlay(720, 480, pixels))
        self.assertLess(len(spu.encode(big)), 1000)


class TestHighlight(unittest.TestCase):
    def test_hli_fields(self):
        m = Menu("x", [Button(RECTS[0], vm.jump_vts_tt(1).encode(), up=2, down=2),
                       Button(RECTS[1], vm.jump_vts_tt(2).encode(), up=1, down=1, auto=True)],
                 first_button=2, button_offset=3)
        h = menu.hli(m, 48003)
        self.assertEqual(len(h), 0x2B6)
        self.assertEqual(struct.unpack_from(">HIIIH", h, 0), (1, 48003, 0xFFFFFFFF, 0xFFFFFFFF, 0x1000))
        self.assertEqual(h[0x10:0x16], bytes((3, 2, 2, 0, 2, 0)))
        b2 = h[0x2E + 18:0x2E + 36]
        self.assertEqual(b2[3] >> 4, 0x4)                     # auto action mode 01 (as dvdauthor writes <action>)
        self.assertEqual(b2[6:10], bytes((1, 1, 2, 2)))        # up, down; left and right itself
        self.assertEqual(b2[10:], vm.jump_vts_tt(2).encode())
        self.assertEqual(menu.hli(Menu("x"), 0), bytes(0x2B6))

    def test_refused(self):
        with self.assertRaises(VtsError):
            menu.check(Menu("x", [Button((0, 0, 800, 10), bytes(8))]), 720, 480)
        with self.assertRaises(VtsError):
            menu.check(Menu("x", [Button(RECTS[0], bytes(8), up=3)]), 720, 480)
        with self.assertRaises(VtsError):
            menu.check(Menu("x", [Button(RECTS[0], bytes(7))]), 720, 480)
        with self.assertRaises(VtsError):
            menu.check(Menu("x", entry="chapter"), 720, 480)
        with self.assertRaises(VtsError):
            menu.check(Menu("x", [Button(RECTS[0], bytes(8))] * 37), 720, 480)


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg, dvdauthor and spumux")
class TestAgainstDvdauthor(unittest.TestCase):
    """dvdauthor's disc with menus (buttons from spumux) beside vtskit's for
    the same layout: a title menu in the VMG, a root menu with a loop cell
    and a second menu in the titleset, two titles."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-menu-")
        t = cls.tmp
        cls.menu_mpg = os.path.join(t, "menu.mpg")
        menu_stream(cls.menu_mpg)
        cls.a, cls.b = os.path.join(t, "a.mpg"), os.path.join(t, "b.mpg")
        encode(cls.a, seconds=6)
        encode(cls.b, seconds=3)
        # spumux: the same buttons as pictures, then dvdauthor
        png(os.path.join(t, "hl.png"), 720, 480, RECTS, (255, 255, 0))
        png(os.path.join(t, "sel.png"), 720, 480, RECTS, (255, 0, 0))
        with open(os.path.join(t, "spu.xml"), "w") as f:
            f.write(f'<subpictures format="NTSC"><stream><spu start="00:00:00.00" force="yes" '
                    f'highlight="{t}/hl.png" select="{t}/sel.png">'
                    '<button name="b1" x0="100" y0="100" x1="300" y1="140" up="b2" down="b2" left="b1" right="b1"/>'
                    '<button name="b2" x0="100" y0="200" x1="300" y1="240" up="b1" down="b1" left="b2" right="b2"/>'
                    '</spu></stream></subpictures>')
        spumuxed = os.path.join(t, "menu_buttons.mpg")
        with open(cls.menu_mpg, "rb") as src, open(spumuxed, "wb") as dst:
            subprocess.run(["spumux", "-s", "0", os.path.join(t, "spu.xml")], stdin=src, stdout=dst,
                           check=True, stderr=subprocess.DEVNULL, env={**os.environ, "VIDEO_FORMAT": "NTSC"})
        with open(os.path.join(t, "dvd.xml"), "w") as f:
            f.write(f'''<dvdauthor dest="{t}/dvd">
 <vmgm><menus><video format="ntsc" aspect="4:3"/>
   <pgc entry="title"><pre>button = 1024;</pre><vob file="{spumuxed}"/>
     <button name="b1">jump title 1;</button><button name="b2">jump titleset 1 menu entry root;</button>
     <post>jump cell 1;</post></pgc></menus></vmgm>
 <titleset><menus><video format="ntsc" aspect="4:3"/>
   <pgc entry="root"><pre>g4 = 0;</pre><vob file="{spumuxed}"><cell start="0" end="2" chapter="1"/><cell start="2"/></vob>
     <button name="b1">jump title 1;</button><button name="b2">jump title 2;</button><post>jump cell 2;</post></pgc>
   <pgc><vob file="{spumuxed}"/><button name="b1">jump title 2;</button><button name="b2">jump menu 1;</button>
     <post>jump cell 1;</post></pgc></menus>
  <titles><video format="ntsc" aspect="4:3"/><audio lang="en"/>
   <pgc><vob file="{cls.a}" chapters="0,2,4"/><post>call menu entry root;</post></pgc>
   <pgc><vob file="{cls.b}"/><post>call vmgm menu entry title;</post></pgc></titles></titleset>
</dvdauthor>''')
        subprocess.run(["dvdauthor", "-x", os.path.join(t, "dvd.xml")], check=True, capture_output=True,
                       env={**os.environ, "VIDEO_FORMAT": "NTSC"})
        cls.theirs = os.path.join(t, "dvd", "VIDEO_TS")
        # vtskit, the same layout
        cls.mine = os.path.join(t, "mine")
        root = Menu(cls.menu_mpg, [Button(RECTS[0], vm.jump_vts_tt(1).encode(), up=2, down=2),
                                   Button(RECTS[1], vm.jump_vts_tt(2).encode(), up=1, down=1)],
                    cells=(0, 2), pre=vm.assemble([vm.set_gprm(4, vm.MOV, 0)]),
                    post=vm.assemble([vm.link_cn(2)]), entry="root")
        second = Menu(cls.menu_mpg, [Button(RECTS[0], vm.jump_vts_tt(2).encode(), up=2, down=2),
                                     Button(RECTS[1], vm.link_pgcn(1).encode(), up=1, down=1)],
                      pre=vm.assemble([vm.set_hl_btnn(1024)]), post=vm.assemble([vm.link_cn(1)]))
        ts = titleset.write(cls.mine, 1, [
            Title(cls.a, (0, 2, 4), (titleset.Audio("en", 2),),
                  post=vm.assemble([vm.call_ss_vtsm_menu(vm.MENU_ROOT)])),
            Title(cls.b, audio=(titleset.Audio("en", 2),),
                  post=vm.assemble([vm.call_ss_vmgm_menu(vm.MENU_TITLE)]))], menus=[root, second])
        title_menu = Menu(cls.menu_mpg, [Button(RECTS[0], vm.jump_tt(1).encode(), up=2, down=2),
                                         Button(RECTS[1], vm.jump_ss_vtsm(1, 1, vm.MENU_ROOT).encode(), up=1, down=1)],
                          pre=vm.assemble([vm.set_hl_btnn(1024)]), post=vm.assemble([vm.link_cn(1)]),
                          entry="title")
        vmg.write(cls.mine, [ts], menus=[title_menu])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_files(self):
        self.assertEqual(sorted(os.listdir(self.mine)), sorted(os.listdir(self.theirs)))
        for name in ("VTS_01_1.VOB", "VTS_01_0.BUP"):
            if name.endswith(".VOB"):
                self.assertEqual(read(os.path.join(self.mine, name)), read(os.path.join(self.theirs, name)))
        self.assertEqual(read(os.path.join(self.mine, "VTS_01_0.BUP")), read(os.path.join(self.mine, "VTS_01_0.IFO")))
        self.assertEqual(read(os.path.join(self.mine, "VIDEO_TS.BUP")), read(os.path.join(self.mine, "VIDEO_TS.IFO")))

    def test_highlight_as_dvdauthor(self):
        # the HLI of every menu VOBU, and the whole menu VOB but for the
        # subpicture pack's place (spumux puts it later in the first VOBU)
        for name in ("VTS_01_0.VOB", "VIDEO_TS.VOB"):
            a, b = read(os.path.join(self.mine, name)), read(os.path.join(self.theirs, name))
            self.assertEqual(len(a), len(b), name)
            navs = [s for s in range(len(a) // 2048) if a[s * 2048 + 0x26:s * 2048 + 0x2A] == b"\x00\x00\x01\xbf"]
            self.assertEqual(navs, [s for s in range(len(b) // 2048) if b[s * 2048 + 0x26:s * 2048 + 0x2A] == b"\x00\x00\x01\xbf"])
            for s in navs:
                o = s * 2048 + 0x2D
                self.assertEqual(a[o + 0x60:o + 0x316], b[o + 0x60:o + 0x316], f"{name} HLI at sector {s}")
                self.assertEqual(a[o:o + 0x18], b[o:o + 0x18], f"{name} PCI_GI at sector {s}")

    def ifo_menu_fields(self, data, ptr):
        """(LU language and flags, [(category, commands, palette, programs, cells)])."""
        o = struct.unpack_from(">I", data, ptr)[0] * 2048
        lang, flags = data[o + 8:o + 10], data[o + 11]
        lu = o + struct.unpack_from(">I", data, o + 12)[0]
        out = []
        for k in range(struct.unpack_from(">H", data, lu)[0]):
            cat, po = struct.unpack_from(">II", data, lu + 8 + 8 * k)
            p = lu + po
            co = struct.unpack_from(">H", data, p + 0xE4)[0]
            c = p + co
            n = sum(struct.unpack_from(">HHH", data, c))
            out.append((cat, data[c:c + 8 + 8 * n], data[p + 0xA4:p + 0xE4], data[p + 2], data[p + 3],
                        data[p + 0xC:p + 0x20]))
        return lang, flags, out

    def test_menu_tables_as_dvdauthor(self):
        for name, ptr in (("VTS_01_0.IFO", 0xD0), ("VIDEO_TS.IFO", 0xC8)):
            a, b = read(os.path.join(self.mine, name)), read(os.path.join(self.theirs, name))
            self.assertEqual(self.ifo_menu_fields(a, ptr), self.ifo_menu_fields(b, ptr), name)
            self.assertEqual(a[0x100:0x15C], b[0x100:0x15C], f"{name}: menu attributes")
            self.assertEqual(a[0xC0:0xC4], b[0xC0:0xC4], f"{name}: menu VOB start")
        a, b = read(os.path.join(self.mine, "VIDEO_TS.IFO")), read(os.path.join(self.theirs, "VIDEO_TS.IFO"))
        self.assertEqual(a[0x0C:0x10], b[0x0C:0x10])                 # the VMG's last sector
        fp = struct.unpack_from(">I", a, 0x84)[0]
        self.assertEqual(a[fp:fp + 0x100], b[fp:fp + 0x100])         # the first-play chain

    @unittest.skipUnless(LIBDVDNAV, "needs libdvdnav")
    def test_buttons_under_libdvdnav(self):
        # (libdvdnav reports no program chain number in the VMG's menus)
        with dvdnav_sim.DvdSim(self.mine, LIBDVDNAV) as d:
            d.wait_menu()                                           # the title menu
            self.assertEqual(d.domain(), "vmgm")
            self.assertEqual(d.button_count(), 2)
            self.assertEqual(d.highlight(), 1)
            self.assertEqual(d.move("down"), 2)
            d.activate()                                            # to the root menu
            self.assertEqual(d.wait_menu(), 1)
            self.assertEqual(d.domain(), "vtsm")
            self.assertEqual(d.button_count(), 2)
            d.activate(2)                                           # title 2
            self.assertEqual(d.wait_title(), (2, 1))
            self.assertEqual(d.play_to_end_of_title()[0], "menu")  # its post: the title menu
            self.assertEqual(d.domain(), "vmgm")
            d.activate(1)                                           # title 1, three chapters
            self.assertEqual(d.wait_title(), (1, 1))
            seen, pgcn = d.trace_until_menu()
            self.assertEqual(seen, [(1, 1), (1, 2), (1, 3)])
            self.assertEqual((pgcn, d.domain()), (1, "vtsm"))      # its post: the root menu


if __name__ == "__main__":
    unittest.main()
