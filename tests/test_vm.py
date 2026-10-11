"""The VM commands vtskit encodes (vtskit.vm), checked against the bytes
dvdauthor compiles for the same statements (read from the IFOs of discs it
authored; the statement is given in dvdauthor's language for reference)."""

import unittest

from vtskit import VtsError, vm
from vtskit.vm import GPRM, SPRM, If, compare

g = GPRM


def one(*items):
    return [c.hex() for c in vm.assemble(list(items))]


class TestAgainstDvdauthor(unittest.TestCase):
    def test_titles(self):
        # a title's <pre>
        self.assertEqual(one(vm.set_gprm(0, vm.MOV, 5)), ["7100000000050000"])        # g0 = 5;
        self.assertEqual(one(vm.set_gprm(1, vm.ADD, 1)), ["7300000100010000"])        # g1 = g1 + 1;
        self.assertEqual(one(vm.set_gprm(2, vm.SUB, 2)), ["7400000200020000"])
        self.assertEqual(one(vm.set_gprm(3, vm.MUL, 3)), ["7500000300030000"])
        self.assertEqual(one(vm.set_gprm(4, vm.DIV, 4)), ["7600000400040000"])
        self.assertEqual(one(vm.set_gprm(5, vm.MOD, 5)), ["7700000500050000"])
        self.assertEqual(one(vm.set_gprm(6, vm.AND, 6)), ["7900000600060000"])
        self.assertEqual(one(vm.set_gprm(7, vm.OR, 7)), ["7a00000700070000"])
        self.assertEqual(one(vm.set_gprm(8, vm.XOR, 8)), ["7b00000800080000"])
        self.assertEqual(one(vm.set_gprm(0, vm.RND, 5)), ["7800000000050000"])        # g0 = random(5);
        self.assertEqual(one(vm.set_gprm(9, vm.MOV, g(0))), ["6100000900000000"])     # g9 = g0;
        self.assertEqual(one(vm.set_gprm(10, vm.MOV, SPRM(8))), ["6100000a00880000"])  # g10 = s8;
        self.assertEqual(one(vm.set_gprm(11, vm.ADD, g(1))), ["6300000b00010000"])    # g11 += g1
        self.assertEqual(one(vm.set_stn(1, 64, 3)), ["51000081c0830000"])  # audio = 1; subtitle = 64; angle = 3;
        self.assertEqual(one(vm.set_hl_btnn(1024)), ["5600000004000000"])             # button = 1024;
        # if (g0 == 3) jump title 2;   (a Jump cannot compare with a number: Goto)
        self.assertEqual(one(If(compare(g(0), vm.EQ, 3), [vm.jump_vts_tt(2)])),
                         ["00b1000000030003", "3003000000020000", "0000000000000000"])
        # if (g0 != 3) jump title 1 chapter 2;
        self.assertEqual(one(If(compare(g(0), vm.NE, 3), [vm.jump_vts_ptt(1, 2)]), vm.nop()),
                         ["00a1000000030003", "3005000200010000", "0000000000000000"])
        # if (g0 >= 3) call menu entry root;
        self.assertEqual(one(If(compare(g(0), vm.GE, 3), [vm.call_ss_vtsm_menu(vm.MENU_ROOT)]))[:2],
                         ["00f1000000030003", "3008000001830000"])
        # if (g0 > 3) call vmgm menu entry title;
        self.assertEqual(one(If(compare(g(0), vm.GT, 3), [vm.call_ss_vmgm_menu(vm.MENU_TITLE)]))[:2],
                         ["00e1000000030003", "3008000001420000"])
        self.assertEqual(one(If(compare(g(0), vm.LE, 3), [vm.exit()]))[0], "00d1000000030003")
        self.assertEqual(one(If(compare(g(0), vm.LT, 3), [vm.exit()]))[0], "00c1000000030003")
        # if (g0 == g1) jump chapter 2;   (a Link carries the comparison)
        self.assertEqual(one(If(compare(g(0), vm.EQ, g(1)), [vm.link_pttn(2)])), ["2025000000010002"])
        # if (g1 >= g2) call menu entry root resume 2;   (a Call compares two registers)
        self.assertEqual(one(If(compare(g(1), vm.GE, g(2)), [vm.call_ss_vtsm_menu(vm.MENU_ROOT, 2)])),
                         ["3048000002830102"])
        self.assertEqual(one(vm.link_cn(2)), ["2007000000000002"])                   # jump cell 2;
        self.assertEqual(one(If(compare(g(0), vm.EQ, 1), [vm.link_pgn(3)])), ["20a6000000010003"])
        self.assertEqual(one(If(compare(SPRM(1), vm.EQ, 1), [vm.jump_vts_tt(2)]))[0], "00b1008100010003")
        self.assertEqual(one(If(compare(g(0), vm.EQ, 3), [vm.link(vm.RSM)])), ["20a1000000030010"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 2), [vm.link(vm.TAIL_PGC)])), ["20a100000002000d"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 9), [vm.exit()])),
                         ["00b1000000090003", "3001000000000000", "0000000000000000"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 1), [vm.link(vm.NEXT_PGC)])), ["20a100000001000a"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 1), [vm.link_cn(3)])), ["20a7000000010003"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 1), [vm.call_ss_vtsm_menu(vm.MENU_ROOT, 3)])),
                         ["00b1000000010003", "3008000003830000", "0000000000000000"])
        # if (g0 == 3) { g1 = 2; jump title 2; } else { g1 = 3; }
        self.assertEqual(one(If(compare(g(0), vm.EQ, 3), [vm.set_gprm(1, vm.MOV, 2), vm.jump_vts_tt(2)],
                                [vm.set_gprm(1, vm.MOV, 3)])),
                         ["00b1000000030004", "7100000100020000", "3003000000020000",
                          "7100000100030000"])
        # if (g0 == 3 and g1 == 4) jump title 2;
        self.assertEqual(one(If([compare(g(0), vm.EQ, 3), compare(g(1), vm.EQ, 4)], [vm.jump_vts_tt(2)])),
                         ["00b1000000030004", "00b1000100040004", "3003000000020000", "0000000000000000"])
        # a title's <post>
        self.assertEqual(one(vm.call_ss_vmgm_menu(vm.MENU_TITLE)), ["3008000001420000"])
        self.assertEqual(one(vm.link(vm.TAIL_PGC)), ["200100000000000d"])            # jump pgc tail;
        self.assertEqual(one(vm.jump_vts_ptt(1, 2)), ["3005000200010000"])           # jump title 1 chapter 2;
        self.assertEqual(one(vm.exit()), ["3001000000000000"])
        self.assertEqual(one(vm.jump_vts_tt(1)), ["3003000000010000"])               # jump title 1;
        self.assertEqual(one(vm.set_gprm(2, vm.MOV, 7)), ["7100000200070000"])        # a cell command

    def test_menus(self):
        # the VMG's title menu
        self.assertEqual(one(vm.jump_ss_vtsm(1, 1, vm.MENU_ROOT)), ["3006000101830000"])  # jump titleset 1 menu entry root;
        self.assertEqual(one(vm.jump_tt(2)), ["3002000000020000"])                   # jump title 2;
        self.assertEqual(one(vm.jump_ss_fp()), ["3006000000000000"])                 # jump fpc;
        self.assertEqual(one(vm.jump_ss_vmgm_pgc(1)), ["3006000100c00000"])          # jump menu 1;
        self.assertEqual(one(If(compare(g(0), vm.EQ, 8), [vm.link_cn(1)])), ["20a7000000080001"])
        self.assertEqual(one(If(compare(g(0), vm.EQ, 9), [vm.link_pgn(1)])), ["20a6000000090001"])
        for code, want in ((vm.NEXT_PGC, "0a"), (vm.PREV_PGC, "0b"), (vm.TOP_PGC, "09"), (vm.NEXT_PG, "06"),
                           (vm.PREV_PG, "07"), (vm.TOP_CELL, "01"), (vm.NEXT_CELL, "02"), (vm.PREV_CELL, "03"),
                           (vm.TOP_PG, "05"), (vm.RSM, "10")):
            self.assertEqual(one(If(compare(g(0), vm.EQ, 10), [vm.link(code)])), ["20a10000000a00" + want])
        self.assertEqual(one(vm.set_hl_btnn(2048)), ["5600000008000000"])
        self.assertEqual(one(vm.jump_tt(1)), ["3002000000010000"])
        # the first-play program chain, and a titleset's root menu
        self.assertEqual(one(vm.jump_ss_vmgm_menu(vm.MENU_TITLE)), ["3006000000420000"])  # jump vmgm menu entry title;
        self.assertEqual(one(vm.jump_vts_ptt(1, 3)), ["3005000300010000"])           # jump title 1 chapter 3;
        self.assertEqual(one(If(compare(g(0), vm.EQ, 5), [vm.link_pgcn(1)])), ["20a4000000050001"])  # jump menu entry root;
        self.assertEqual(one(vm.link_pgcn(1)), ["2004000000000001"])


class TestRules(unittest.TestCase):
    def test_registers(self):
        self.assertEqual(repr(GPRM(3)), "GPRM(3)")
        self.assertEqual(repr(SPRM(8)), "SPRM(8)")
        self.assertEqual(vm.HL_BTNN, 0x88)
        for bad in ((GPRM, 16), (SPRM, 24), (GPRM, -1)):
            with self.assertRaises(VtsError):
                bad[0](bad[1])

    def test_conditions(self):
        with self.assertRaises(VtsError):
            compare(3, vm.EQ, g(0))                       # a number on the left
        with self.assertRaises(VtsError):
            compare(g(0), vm.EQ, 70000)
        with self.assertRaises(VtsError):
            compare(g(0), vm.BC, 1).inverse()
        with self.assertRaises(VtsError):
            vm.jump_tt(1).when(compare(g(0), vm.EQ, 1))   # a Jump compares registers only
        self.assertEqual(vm.jump_tt(1).when(compare(g(0), vm.EQ, g(1))).encode().hex(), "3022000000010001")
        with self.assertRaises(VtsError):
            vm.link_cn(1).when(compare(g(0), vm.EQ, 1)).when(compare(g(0), vm.EQ, 2))

    def test_bitwise_test_needs_a_link(self):
        # if (g0 & 4) has no opposite: it cannot guard a Goto, only a Link
        self.assertEqual(one(If(compare(g(0), vm.BC, 4), [vm.link_pgcn(2)])), ["2094000000040002"])
        with self.assertRaises(VtsError):
            one(If(compare(g(0), vm.BC, 4), [vm.jump_tt(2)]))

    def test_limits(self):
        with self.assertRaises(VtsError):
            vm.jump_tt(100)
        with self.assertRaises(VtsError):
            vm.link_pttn(1, button=64)
        with self.assertRaises(VtsError):
            vm.set_stn()
        with self.assertRaises(VtsError):
            vm.set_stn(audio=g(0), subpicture=2)
        with self.assertRaises(VtsError):
            vm.set_gprm(0, 12, 1)
        with self.assertRaises(VtsError):
            vm.assemble([vm.nop()] * 129)
        with self.assertRaises(VtsError):
            vm.assemble([b"\x30\x01"])

    def test_or(self):
        # if (g0 == 3 or g1 == 4) jump title 2;  as dvdauthor compiles it
        want = ["00a1000000030003", "00b1000100040004", "3003000000020000", "0000000000000000"]
        self.assertEqual(one(vm.Any([compare(g(0), vm.EQ, 3), compare(g(1), vm.EQ, 4)], [vm.jump_vts_tt(2)])),
                         want)

    def test_registers_in_set_system(self):
        self.assertEqual(one(vm.set_stn(audio=g(2))), ["4100008200000000"])
        self.assertEqual(one(vm.set_hl_btnn(g(1))), ["4600000000010000"])
        self.assertEqual(one(vm.set_nvtmr(30, 2)), ["520000" + "1e" + "00020000"])
        self.assertEqual(one(vm.set_gprmmd(3, 10, counter=True)), ["5300000a00830000"])


if __name__ == "__main__":
    unittest.main()
