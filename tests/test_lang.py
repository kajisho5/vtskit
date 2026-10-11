"""dvdauthor's command language compiled by vtskit.lang: each form against
the commands dvdauthor writes for the same text, a whole disc in the
language (menus of commands alone, buttons of several commands, cell
commands, a title menu without pictures, two titlesets) whose IFOs come
out the same as dvdauthor's, and jumps to a chapter of another titleset
(the "jumppads") played with libdvdnav."""

import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from vtskit import VtsError, lang, menu, spu, titleset, vm, vmg
from vtskit.lang import Disc, Menus
from vtskit.menu import Button, Menu
from vtskit.titleset import Audio, Title

from .test_menu import RECTS, menu_stream, png, read
from .test_titleset import encode

try:
    from . import dvdnav_sim
    LIBDVDNAV = dvdnav_sim.load_library()
except Exception:  # pragma: no cover
    LIBDVDNAV = None

TOOLS_OK = bool(shutil.which("ffmpeg") and shutil.which("dvdauthor") and shutil.which("spumux"))
FFMPEG_OK = bool(shutil.which("ffmpeg"))

GREY, YELLOW, RED = (0x7E, 0x80, 0x80), (0xD2, 0x92, 0x10), (0x52, 0xF0, 0x5A)

# the disc the language test authors: two titlesets, menus in the first
FP = "subtitle = 64; g1 = 1; jump titleset 1 menu entry root;"
VMGM_TITLE = ("if (g1 == 1000) { g1 = 0; jump title 3; } g3 = 0; if (g1 == 0) { g1 = 1; } "
              "jump titleset 1 menu entry root;")
VMGM_SECOND = "g2 = 1; jump title 1;"
ROOT_PRE = ("if (g1 == 0) { g1 = g3; } g3 = 0; if (g1 == 2) { g1 = 0; jump menu 2; } g1 = 0; "
            "if (g5 != 0) { button = g5; } else { button = 1024; }")
ROOT_BUTTONS = ["g5 = button; g2 = 0; jump title 1;", "g5 = button; g2 = 0; jump title 1 chapter 2;"]
ROOT_POST = "g4 += 1; if (g4 lt 3) { jump cell 2; } g2 = 1; jump title 1;"
SECOND_PRE = "button = 2048;"
SECOND_BUTTONS = ["g6 = button; jump title 2;", "audio = 0; subtitle = 64;"]
SECOND_POST = "jump cell 1;"
THIRD_PRE = "g1 = g3; jump menu 1;"
THIRD_BUTTONS = ["jump menu 1;", "button = 1024; jump menu 1;"]
TITLE1_PRE = "g3 = 1; g9 = 1;"
TITLE1_CELL = "if (g12 == 1) jump pgc tail;"
TITLE1_POST = "g9 = 0; if (g2 == 1) { jump title 2; } g1 = 1; call menu entry root;"
TITLE2_POST = "g2 = 0; g1 = 2000; call vmgm menu entry title;"
VTS2_ROOT = "g1 = g3; if (g1 == 0) { g1 = 1; } jump vmgm menu entry title;"
VTS2_TITLE_POST = "g1 = 1; call vmgm menu entry title;"


def disc():
    return Disc({(1, 1): 1, (1, 2): 2, (2, 1): 3}, Menus(2, {"title": 1}),
                {1: Menus(3, {"root": 1}), 2: Menus(1, {"root": 1})})


def hexes(commands):
    return [c.hex() for c in commands]


class TestForms(unittest.TestCase):
    """Each form against dvdauthor's commands for it (read from discs it
    authored: tests/test_vm.py has the plain instructions, these the
    language's shapes)."""

    def setUp(self):
        self.d = disc()

    def test_first_play(self):
        self.assertEqual(hexes(self.d.compile(FP, "fp")),
                         ["51000000c0000000", "7100000100010000", "3006000101830000"])
        self.assertEqual(hexes(self.d.compile("jump menu entry title;", "fp")), ["3006000100c00000"])

    def test_video_manager(self):
        self.assertEqual(hexes(self.d.compile(
            "if (g0 == 1) jump menu entry title; if (g0 == 2) jump menu 2; "
            "if (g0 == 3) { g1 = 0; jump menu 2; } g1 = 0; jump titleset 1 menu entry root;", "vmgm")),
            ["00b1000000010003", "3006000100c00000", "00b1000000020005", "3006000200c00000",
             "00b1000000030008", "7100000100000000", "3006000200c00000", "7100000100000000",
             "3006000101830000"])
        self.assertEqual(hexes(self.d.compile(VMGM_SECOND, "vmgm")), ["7100000200010000", "3002000000010000"])
        # (dvdauthor pads `jump titleset 2 title 1;` from the VMG; vtskit knows the title's number)
        self.assertEqual(hexes(self.d.compile("jump titleset 2 title 1;", "vmgm")), ["3002000000030000"])
        self.assertEqual(hexes(self.d.compile(VMGM_TITLE, "vmgm")),
                         ["00b1000103e80004", "7100000100000000", "3002000000030000", "7100000300000000",
                          "71a0010100010000", "3006000101830000"])

    def test_menu_commands(self):
        self.assertEqual(hexes(self.d.compile(ROOT_PRE, "vtsm", 1)),
                         ["61a0010100030000", "7100000300000000", "00b1000100020005", "7104000100000002",
                          "7100000100000000", "00a1000500000009", "4600000000050000", "000100000000000a",
                          "5600000004000000", "0000000000000000"])
        self.assertEqual(hexes(self.d.compile(VTS2_ROOT, "vtsm", 2)),
                         ["6100000100030000", "71a0010100010000", "3006000000420000"])
        self.assertEqual(hexes(self.d.compile(THIRD_PRE, "vtsm", 1)), ["6104000100030001"])
        self.assertEqual(hexes(self.d.compile("audio = 1; jump cell 1;", "vtsm", 1)), ["5107008100000001"])
        self.assertEqual(hexes(self.d.compile("g1 = 0; resume;", "vtsm", 1)), ["7101000100000010"])
        self.assertEqual(hexes(self.d.compile("audio = 0; subtitle = 64;", "vtsm", 1)), ["51000080c0000000"])

    def test_buttons(self):
        # a button of one command runs it; of several, GPRM 15 and a dispatcher in the post
        buttons, post = self.d.menu(ROOT_BUTTONS, ROOT_POST, "vtsm", 1)
        self.assertEqual(hexes(buttons), ["7101000f0001000d", "7101000f0002000d"])
        self.assertEqual(hexes(post), [
            "6100000e000f0000", "7100000f00000000", "00a1000e00010009", "00a1000e0002000c",
            "7300000400010000", "20f7000400030002", "7100000200010000", "3003000000010000",
            "6100000500880000", "7100000200000000", "3003000000010000",
            "6100000500880000", "7100000200000000", "3005000200010000"])
        buttons, post = self.d.menu(["jump menu 2;", "g5 = button;"], "audio = 1; jump cell 1;", "vtsm", 1)
        self.assertEqual(hexes(buttons), ["2004000000000002", "6100000500880000"])
        self.assertEqual(hexes(post), ["5107008100000001"])
        # (a Set and a Link fold, so `g1 = 0; jump pgc tail;` is one command; no post: exit)
        buttons, post = self.d.menu(["if (g1 == 1) jump title 1;", "g1 = 0; jump pgc tail;"], "", "vtsm", 1)
        self.assertEqual(hexes(buttons), ["7101000f0001000d", "710100010000000d"])
        self.assertEqual(hexes(post), ["6100000e000f0000", "7100000f00000000", "00a1000e00010005",
                                       "3001000000000000", "00b1000100010007", "3003000000010000",
                                       "0000000000000000"])
        buttons, _ = self.d.menu(THIRD_BUTTONS, SECOND_POST, "vtsm", 1)
        self.assertEqual(hexes(buttons), ["2004000000000001", "5604000004000001"])
        # one such button and a post of one command: that command, unless the button was pressed
        buttons, post = self.d.menu(SECOND_BUTTONS, SECOND_POST, "vtsm", 1)
        self.assertEqual(hexes(buttons), ["7101000f0001000d", "51000080c0000000"])
        self.assertEqual(hexes(post), ["6100000e000f0000", "7100000f00000000", "20b7000e00010001",
                                       "6100000600880000", "3003000000020000"])

    def test_title_commands(self):
        self.assertEqual(hexes(self.d.compile(TITLE1_POST, "vts", 1)),
                         ["7100000900000000", "00b1000200010004", "3003000000020000", "7100000100010000",
                          "3008000001830000"])
        self.assertEqual(hexes(self.d.compile(TITLE1_CELL, "vts", 1)), ["20a1000c0001000d"])
        self.assertEqual(hexes(self.d.compile(TITLE2_POST, "vts", 1)),
                         ["7100000200000000", "7100000107d00000", "3008000001420000"])
        self.assertEqual(hexes(self.d.compile("g1 = 0; jump pgc tail;", "vts", 1)), ["710100010000000d"])
        self.assertEqual(hexes(self.d.compile("g1 = 0; exit;", "vts", 1)), ["7100000100000000", "3001000000000000"])
        self.assertEqual(hexes(self.d.compile("call menu entry root resume 3;", "vts", 1)), ["3008000003830000"])

    def test_dead_code_after_a_jump(self):
        self.assertEqual(hexes(self.d.compile("g1 = 0; resume; g1 = 0; jump title 1 chapter 2; "
                                              "subtitle = 65; audio = 1;", "vtsm", 1)),
                         ["7101000100000010"])

    def test_conditions(self):
        self.assertEqual(hexes(self.d.compile("if (g0 == 3 and g1 == 4) jump title 2;", "vts", 1)),
                         ["00b1000000030004", "00b1000100040004", "3003000000020000", "0000000000000000"])
        self.assertEqual(hexes(self.d.compile("if (g0 == 3 or g1 == 4) jump title 2;", "vts", 1)),
                         ["00a1000000030003", "00b1000100040004", "3003000000020000", "0000000000000000"])
        self.assertEqual(hexes(self.d.compile("if (3 lt g0) exit;", "vts", 1))[0], "00e1000000030003")
        self.assertEqual(hexes(self.d.compile("if (s1 == 1) jump title 2;", "vts", 1))[0], "00b1008100010003")
        self.assertEqual(hexes(self.d.compile("g0 = random(5); g11 = g0; g1 = g1 + 1; g2 -= 2;", "vts", 1)),
                         ["7800000000050000", "6100000b00000000", "7300000100010000", "7400000200020000"])

    def test_pads(self):
        # from the video manager: the code of title 1, chapter 2 of titleset 2, then its PTT menu
        self.assertEqual(hexes(self.d.compile("jump titleset 2 title 1 chapter 2;", "vmgm")),
                         ["7100000f81020000", "3006000102870000"])
        pad = self.d.vts_pad(2)
        self.assertEqual(hexes(pad), ["6100000e000f0000", "7100000f00000000", "7100000d81020000",
                                      "3025000200010e0d", "2004000000000001"])
        self.assertIsNone(self.d.vts_pad(1))
        # from a titleset's menus: through a pad chain of the video manager (after its 2 menus)
        self.assertEqual(hexes(self.d.compile("jump titleset 2 title 1 chapter 3;", "vtsm", 1)),
                         ["7100000f00010000", "3006000300c00000"])
        self.assertEqual(hexes(self.d.compile("jump titleset 2 title 1;", "vtsm", 1)),
                         ["7100000f00020000", "3006000300c00000"])
        # a title calls the pad chain (it cannot JumpSS); the same codes
        self.assertEqual(hexes(self.d.compile("jump titleset 2 title 1 chapter 3;", "vts", 1)),
                         ["7100000f00010000", "3008000301c00000"])
        pads = self.d.vmgm_pads()
        self.assertEqual(len(pads), 1)
        self.assertEqual(hexes(pads[0]), [
            "6100000e000f0000", "7100000f00000000",
            "7100000d00010000", "0031000e000d0007", "7100000f81030000", "3006000102870000",
            "7100000d00020000", "3022000000030e0d",
            "3006000100c00000"])
        self.assertEqual(hexes(self.d.vts_pad(2))[2:6], ["7100000d81020000", "3025000200010e0d",
                                                        "7100000d81030000", "3025000300010e0d"])
        self.assertEqual(self.d.vmgm_pads(), pads)                   # (asking again adds nothing)

    def test_refused(self):
        for text, domain in (("jump title 1 chapter 2 extra;", "vts"), ("jump nowhere;", "vts"),
                             ("g1 = g2 + g3;", "vts"), ("call menu entry root;", "vtsm"),
                             ("jump menu 1;", "vts"), ("jump titleset 9 title 1;", "vts"),
                             ("if (1 == 2) exit;", "vts"), ("g1 = ;", "vts"), ("jump title 9;", "vmgm"),
                             ("jump menu entry audio;", "vtsm"), ("frobnicate;", "vts"), ("exit;", "nowhere")):
            with self.assertRaises(VtsError, msg=text):
                self.d.compile(text, domain, 1)
        with self.assertRaises(VtsError):
            self.d.menu(["g1 = 1; g2 = 2;"], "g3 = 3;", "vtsm", 1)   # post that does not leave
        with self.assertRaises(VtsError):
            self.d.menu(["exit;"], "exit;", "vts", 1)


def author(folder, menu_n, menu_h, a, b):
    """dvdauthor's disc for the language test: the same text as `build`."""
    out = os.path.join(folder, "dvd")
    xml = os.path.join(folder, "dvd.xml")

    def buttons(texts):
        return "".join(f'<button name="b{k}">{t}</button>' for k, t in enumerate(texts, 1))
    with open(xml, "w") as f:
        f.write(f'''<dvdauthor dest="{out}">
 <vmgm><fpc>{FP}</fpc><menus><video format="ntsc" aspect="4:3"/>
   <pgc entry="title"><pre>{VMGM_TITLE}</pre></pgc>
   <pgc><pre>{VMGM_SECOND}</pre></pgc></menus></vmgm>
 <titleset><menus><video format="ntsc" aspect="4:3"/>
   <pgc entry="root"><pre>{ROOT_PRE}</pre>{buttons(ROOT_BUTTONS)}
     <vob file="{menu_n}"><cell start="0" end="2" chapter="1"/><cell start="2"/></vob><post>{ROOT_POST}</post></pgc>
   <pgc><pre>{SECOND_PRE}</pre>{buttons(SECOND_BUTTONS)}<vob file="{menu_h}"/><post>{SECOND_POST}</post></pgc>
   <pgc><pre>{THIRD_PRE}</pre>{buttons(THIRD_BUTTONS)}<vob file="{menu_n}"/></pgc></menus>
  <titles><video format="ntsc" aspect="4:3"/><audio format="ac3" channels="2" lang="en"/>
   <pgc><pre>{TITLE1_PRE}</pre><vob file="{a}"><cell start="0" end="2" chapter="1" pause="inf">{TITLE1_CELL}</cell>
     <cell start="2" end="4" chapter="1" pause="inf"/><cell start="4" end="-1" chapter="1" pause="inf"/></vob>
     <post>{TITLE1_POST}</post></pgc>
   <pgc><vob file="{b}"/><post>{TITLE2_POST}</post></pgc></titles></titleset>
 <titleset><menus><video format="ntsc" aspect="4:3"/><pgc entry="root"><pre>{VTS2_ROOT}</pre></pgc></menus>
  <titles><video format="ntsc" aspect="4:3"/><audio format="ac3" channels="2" lang="en"/>
   <pgc><vob file="{a}" chapters="0,2,4"/><post>{VTS2_TITLE_POST}</post></pgc></titles></titleset>
</dvdauthor>''')
    subprocess.run(["dvdauthor", "-x", xml], check=True, capture_output=True,
                   env={**os.environ, "VIDEO_FORMAT": "NTSC"})
    return os.path.join(out, "VIDEO_TS")


def build(folder, menu_mpg, a, b):
    """vtskit's disc for the language test."""
    d = disc()

    def menu_of(texts, post, pre, stream, **kw):
        cmds, post_cmds = d.menu(texts, post, "vtsm", 1)
        bs = [Button(RECTS[0], cmds[0], up=2, down=2), Button(RECTS[1], cmds[1], up=1, down=1, **kw)]
        return Menu(stream, bs, pre=d.compile(pre, "vtsm", 1), post=post_cmds)
    root = menu_of(ROOT_BUTTONS, ROOT_POST, ROOT_PRE, menu_mpg)
    root.cells, root.entry, root.normal = (0, 2), "root", GREY
    second = menu_of(SECOND_BUTTONS, SECOND_POST, SECOND_PRE, menu_mpg, auto=True)
    third = menu_of(THIRD_BUTTONS, "", THIRD_PRE, menu_mpg)
    third.normal = GREY
    ts1 = titleset.write(folder, 1, [
        Title(a, (0, 2, 4), (Audio("en", 2),), pre=d.compile(TITLE1_PRE, "vts", 1),
              post=d.compile(TITLE1_POST, "vts", 1), cell_commands={1: d.compile(TITLE1_CELL, "vts", 1)[0]},
              stills={1: None, 2: None, 3: None}),
        Title(b, audio=(Audio("en", 2),), post=d.compile(TITLE2_POST, "vts", 1))],
        menus=[root, second, third])
    ts2 = titleset.write(folder, 2, [Title(a, (0, 2, 4), (Audio("en", 2),),
                                           post=d.compile(VTS2_TITLE_POST, "vts", 2))],
                         menus=[Menu(None, pre=d.compile(VTS2_ROOT, "vtsm", 2), entry="root")])
    vmg.write(folder, [ts1, ts2], menus=[Menu(None, pre=d.compile(VMGM_TITLE, "vmgm"), entry="title"),
                                         Menu(None, pre=d.compile(VMGM_SECOND, "vmgm"))],
              first_play=d.compile(FP, "fp"), provider="DVDAuthor 0.7.2")
    return d


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg, dvdauthor and spumux")
class TestAgainstDvdauthor(unittest.TestCase):
    """The language test disc by dvdauthor and by vtskit: the IFOs the
    same byte for byte, the menus' highlight information too, and the
    overlay a menu shows in its normal colour (spumux's `image`)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = t = tempfile.mkdtemp(prefix="vtskit-lang-")
        cls.menu_mpg = os.path.join(t, "menu.mpg")
        menu_stream(cls.menu_mpg)
        a, b = os.path.join(t, "a.mpg"), os.path.join(t, "b.mpg")
        encode(a, seconds=6)
        encode(b, seconds=3)
        for name, colour in (("hl", (255, 255, 0)), ("sel", (255, 0, 0)), ("normal", (128, 128, 128))):
            png(os.path.join(t, f"{name}.png"), 720, 480, RECTS, colour)
        muxed = {}
        for kind, image, second in (("n", f'image="{t}/normal.png" ', "button"), ("h", "", "action")):
            with open(os.path.join(t, f"spu_{kind}.xml"), "w") as f:
                f.write(f'<subpictures format="NTSC"><stream><spu start="00:00:00.00" force="yes" {image}'
                        f'highlight="{t}/hl.png" select="{t}/sel.png">'
                        '<button name="b1" x0="100" y0="100" x1="300" y1="140" up="b2" down="b2" left="b1" right="b1"/>'
                        f'<{second} name="b2" x0="100" y0="200" x1="300" y1="240" up="b1" down="b1" left="b2" right="b2"/>'
                        '</spu></stream></subpictures>')
            muxed[kind] = os.path.join(t, f"menu_{kind}.mpg")
            with open(cls.menu_mpg, "rb") as src, open(muxed[kind], "wb") as dst:
                subprocess.run(["spumux", "-s", "0", os.path.join(t, f"spu_{kind}.xml")], stdin=src,
                               stdout=dst, check=True, stderr=subprocess.DEVNULL,
                               env={**os.environ, "VIDEO_FORMAT": "NTSC"})
        cls.theirs = author(t, muxed["n"], muxed["h"], a, b)
        cls.mine = os.path.join(t, "mine")
        cls.disc = build(cls.mine, cls.menu_mpg, a, b)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_files(self):
        self.assertEqual(sorted(os.listdir(self.mine)), sorted(os.listdir(self.theirs)))
        self.assertNotIn("VTS_02_0.VOB", os.listdir(self.mine))      # (menus of commands alone)
        self.assertNotIn("VIDEO_TS.VOB", os.listdir(self.mine))

    def test_ifos(self):
        for name in ("VIDEO_TS.IFO", "VTS_01_0.IFO", "VTS_02_0.IFO"):
            a, b = read(os.path.join(self.mine, name)), read(os.path.join(self.theirs, name))
            if a != b:
                at = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y) if len(a) == len(b) else min(len(a), len(b))
                self.fail(f"{name} differs at {at:#x}: {a[at:at + 16].hex()} / {b[at:at + 16].hex()}")
            self.assertEqual(read(os.path.join(self.mine, name[:-4] + ".BUP")), a)

    def test_title_vobs(self):
        for name in ("VTS_01_1.VOB", "VTS_02_1.VOB"):
            self.assertEqual(read(os.path.join(self.mine, name)), read(os.path.join(self.theirs, name)), name)

    def test_menu_vob(self):
        # the same packs but for the subpicture pack (spumux places it later
        # in the first VOBU; the unit itself is the same bytes), the highlight
        # information of every navigation pack the same
        a, b = read(os.path.join(self.mine, "VTS_01_0.VOB")), read(os.path.join(self.theirs, "VTS_01_0.VOB"))
        self.assertEqual(len(a), len(b))
        units = {"a": [], "b": []}
        for which, data in (("a", a), ("b", b)):
            for s in range(len(data) // 2048):
                p = data[s * 2048:(s + 1) * 2048]
                if p[0x26:0x2A] == b"\x00\x00\x01\xbf":
                    o = s * 2048 + 0x2D
                    self.assertEqual(a[o:o + 0x18], b[o:o + 0x18], f"PCI_GI at sector {s}")
                    self.assertEqual(a[o + 0x60:o + 0x316], b[o + 0x60:o + 0x316], f"HLI at sector {s}")
                pes = 14 + (18 + struct.unpack_from(">H", p, 16)[0] - 14 if p[14:18] == b"\x00\x00\x01\xbb" else 0)
                if p[pes:pes + 4] == b"\x00\x00\x01\xbd":
                    hl = p[pes + 8]
                    if p[pes + 9 + hl] == 0x20:
                        units[which].append(p[pes + 10 + hl:pes + 6 + struct.unpack_from(">H", p, pes + 4)[0]])
        self.assertEqual(len(units["a"]), 3)
        # (the menus with a normal colour: the same unit; for the other spumux
        # codes the rows between the buttons 1 as well, all transparent, so
        # only the commands are compared)
        self.assertEqual(units["a"][0], units["b"][0])
        self.assertEqual(units["a"][2], units["b"][2])
        self.assertEqual(spu.decode(units["a"][1])[2], spu.decode(units["b"][1])[2])
        self.assertEqual(spu.decode(units["a"][1])[0], spu.decode(units["b"][1])[0])

    def test_vmg_menu_attributes_without_pictures(self):
        a = read(os.path.join(self.mine, "VIDEO_TS.IFO"))
        self.assertEqual(a[0x100:0x104], b"\xff\xf8\x00\x00")
        self.assertEqual(struct.unpack_from(">I", a, 0xC0)[0], struct.unpack_from(">I", a, 0x1C)[0] + 1)


@unittest.skipUnless(TOOLS_OK, "needs ffmpeg, dvdauthor and spumux")
class TestEntryMenusAgainstDvdauthor(unittest.TestCase):
    """Menus of commands alone marked for the remote keys, by dvdauthor and
    by vtskit: every kind, and domains whose menus name no root (title in
    the VMG), where the first menu becomes it and its own kind links to it."""

    TEXT = {"root": "g1 = 1; jump title 1;", "audio": "audio = 1; jump title 1;",
            "subtitle": "subtitle = 64; jump title 1;", "angle": "angle = 1; jump title 1;",
            "ptt": "g1 = 3; jump title 1;", "title": "g1 = 1; jump title 1;", None: "g1 = 9; jump title 1;"}
    CASES = (["root", "audio", "subtitle", "angle"], ["audio"], ["subtitle", "angle"], [None, "audio"],
             [None], ["audio", "root"], ["ptt"])

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vtskit-entries-")
        cls.mpg = os.path.join(cls.tmp, "a.mpg")
        encode(cls.mpg, seconds=4)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def same(self, name, kinds, vmgm=False):
        t = self.tmp
        out = os.path.join(t, "dvd-" + name)
        pgcs = "".join(f'<pgc{f" entry={chr(34)}{k}{chr(34)}" if k else ""}><pre>{self.TEXT[k]}</pre></pgc>'
                       for k in kinds)
        menus = f'<menus><video format="ntsc" aspect="4:3"/>{pgcs}</menus>'
        with open(out + ".xml", "w") as f:
            f.write(f'<dvdauthor dest="{out}">{"<vmgm>" + menus + "</vmgm><titleset>" if vmgm else "<vmgm/><titleset>" + menus}'
                    f'<titles><video format="ntsc" aspect="4:3"/><audio lang="en"/>'
                    f'<pgc><vob file="{self.mpg}"/></pgc></titles></titleset></dvdauthor>')
        subprocess.run(["dvdauthor", "-x", out + ".xml"], check=True, capture_output=True,
                       env={**os.environ, "VIDEO_FORMAT": "NTSC"})
        d = Disc({(1, 1): 1}, Menus(len(kinds), {}) if vmgm else None,
                 {1: Menus(len(kinds), {k: n for n, k in enumerate(kinds, 1) if k})})
        mine = os.path.join(t, "mine-" + name)
        domain = "vmgm" if vmgm else "vtsm"
        ms = [Menu(None, pre=d.compile(self.TEXT[k], domain, 1), entry="subpicture" if k == "subtitle" else k)
              for k in kinds]
        ts = titleset.write(mine, 1, [Title(self.mpg, audio=(Audio("en", 2),))], menus=[] if vmgm else ms)
        vmg.write(mine, [ts], menus=ms if vmgm else [], provider="DVDAuthor 0.7.2")
        for ifo in ("VTS_01_0.IFO", "VIDEO_TS.IFO"):
            a, b = read(os.path.join(mine, ifo)), read(os.path.join(out, "VIDEO_TS", ifo))
            if a != b:
                at = next(k for k in range(min(len(a), len(b))) if a[k] != b[k])
                self.fail(f"{name}: {ifo} differs at {at:#x}: mine {a[at:at + 8].hex()} theirs {b[at:at + 8].hex()}")

    def test_titleset_menus(self):
        for kinds in self.CASES:
            with self.subTest(kinds=kinds):
                self.same("-".join(k or "none" for k in kinds), kinds)

    def test_vmgm_without_a_title_menu(self):
        self.same("vmgm-none", [None], vmgm=True)
        self.same("vmgm-title", ["title"], vmgm=True)


@unittest.skipUnless(FFMPEG_OK and LIBDVDNAV, "needs ffmpeg and libdvdnav")
class TestPadsUnderLibdvdnav(unittest.TestCase):
    """Jumps to a chapter of a title in another titleset: from the
    first-play chain and the video manager (through the titleset's PTT
    menu) and from a titleset's menu (through a pad chain of the video
    manager), played with libdvdnav."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = t = tempfile.mkdtemp(prefix="vtskit-pads-")
        menu_mpg, a, b = os.path.join(t, "menu.mpg"), os.path.join(t, "a.mpg"), os.path.join(t, "b.mpg")
        menu_stream(menu_mpg)
        encode(a, seconds=6)
        encode(b, seconds=3)
        d = Disc({(1, 1): 1, (2, 1): 2}, Menus(1, {"title": 1}), {1: Menus(1, {"root": 1}), 2: Menus(1, {"root": 1})})
        buttons, post = d.menu(["jump titleset 2 title 1 chapter 3;", "g1 = 0; jump titleset 2 title 1;"],
                               "jump cell 1;", "vtsm", 1)
        root = Menu(menu_mpg, [Button(RECTS[0], buttons[0], up=2, down=2), Button(RECTS[1], buttons[1], up=1, down=1)],
                    pre=d.compile("button = 1024;", "vtsm", 1), post=post, entry="root")
        title_pre = d.compile("if (g1 == 2000) { g1 = 0; jump titleset 2 title 1 chapter 2; } "
                              "jump titleset 1 menu entry root;", "vmgm")
        fp = d.compile("g1 = 2000; jump vmgm menu entry title;", "fp")
        # the titles jump into each other's titleset: t1 to a chapter of t2, t2 back to t1
        t1 = Title(b, audio=(Audio("en", 2),), post=d.compile("jump titleset 2 title 1 chapter 2;", "vts", 1))
        t2 = Title(a, (0, 2, 4), (Audio("en", 2),), post=d.compile("g1 = 0; jump titleset 1 title 1;", "vts", 2))
        cls.out = os.path.join(t, "dvd")
        # the pads come last: every command is compiled by now
        vmgm_menus = [Menu(None, pre=title_pre, entry="title")] + [Menu(None, pre=p) for p in d.vmgm_pads()]
        ts1 = titleset.write(cls.out, 1, [t1], menus=[root] + (
            [Menu(None, pre=d.vts_pad(1), entry="ptt")] if d.vts_pad(1) else []))
        ts2 = titleset.write(cls.out, 2, [t2], menus=[Menu(None, pre=d.compile("jump vmgm menu entry title;", "vtsm", 2), entry="root"),
                                                     Menu(None, pre=d.vts_pad(2), entry="ptt")])
        vmg.write(cls.out, [ts1, ts2], menus=vmgm_menus, first_play=fp)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_jumps_land(self):
        with dvdnav_sim.DvdSim(self.out, LIBDVDNAV) as d:
            self.assertEqual(d.num_titles(), 2)
            self.assertEqual(d.wait_title(), (2, 2))         # first play: chapter 2 of titleset 2's title
            # the titles' posts: t2 calls the VMG pad, which JumpTTs to t1; t1
            # calls it for chapter 2 of t2
            self.assertEqual(d.play_to_end_of_title(), ("title", (1, 1)))
            self.assertEqual(d.play_to_end_of_title(), ("title", (2, 2)))
            d.menu_key()                                       # the root menu of titleset 1
            self.assertEqual(d.wait_menu(), 1)
            self.assertEqual(d.domain(), "vtsm")
            d.activate(1)                                      # through the VMG pad to chapter 3
            self.assertEqual(d.wait_title(), (2, 3))
            d.menu_key()
            self.assertEqual(d.wait_menu(), 1)
            d.activate(2)                                      # the whole title, JumpTT from the pad
            self.assertEqual(d.wait_title(), (2, 1))
