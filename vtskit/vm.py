"""The DVD virtual machine's instructions, as the 8-byte commands a program
chain carries (pre, post and cell commands, and a button's command).

The encoding is https://dvdnav.mplayerhq.hu/dvdinfo/vmi.html (types 0 to
3; the combined set-compare-link types 4 to 6 are not written). Each
instruction here is a function returning an `Instruction`; `assemble`
turns a list of instructions and `If` blocks into the commands, resolving
the Goto lines, the way dvdauthor compiles its language (tests/test_vm.py
checks the bytes against dvdauthor's for each form):

* an instruction that can carry a comparison does so (the Link family and
  the Goto with a register or a number; Jump and Call with two registers;
  the Set families with a register or a number);
* otherwise `If` becomes a Goto past the body on the opposite comparison,
  and a Goto needs a line to land on, so a NOP ends a program whose last
  instruction is conditional;
* a Set (of a general register or a system register) followed by a Link
  within the domain (LinkPGCN, LinkCN, LinkTailPGC, RSM, ...), neither
  comparing, becomes the one "set then link" command (`g1 = 0; jump menu
  2;` is one command in dvdauthor's output).

`Label` and `Goto` place a program's own branches (a menu's button
dispatcher).

Registers: `GPRM(n)` is general register n (0-15), `SPRM(n)` system
register n (0-23); a plain int is a number.
"""

import struct
from dataclasses import dataclass

from .errors import VtsError


class Register(int):
    """A register operand; a plain int is an immediate value."""
    def __new__(cls, code):
        return int.__new__(cls, code)

    def __repr__(self):
        return f"SPRM({self - 0x80})" if self >= 0x80 else f"GPRM({int(self)})"


def GPRM(n):
    if not 0 <= n <= 15:
        raise VtsError(f"GPRM {n}: general registers are 0 to 15")
    return Register(n)


def SPRM(n):
    if not 0 <= n <= 23:
        raise VtsError(f"SPRM {n}: system registers are 0 to 23")
    return Register(0x80 + n)


ASTN, SPSTN, AGLN, TTN, VTS_TTN, TT_PGCN, PTTN, HL_BTNN = (SPRM(n) for n in range(1, 9))

# comparisons (vmi.html, "CMP op")
BC, EQ, NE, GE, GT, LE, LT = range(1, 8)
_INVERSE = {EQ: NE, NE: EQ, GE: LT, LT: GE, GT: LE, LE: GT}
# set operations ("SET op")
MOV, SWP, ADD, SUB, MUL, DIV, MOD, RND, AND, OR, XOR = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
# the Link subset (operand 3 of Link, cmd 1)
TOP_CELL, NEXT_CELL, PREV_CELL, TOP_PG, NEXT_PG, PREV_PG = 1, 2, 3, 5, 6, 7
TOP_PGC, NEXT_PGC, PREV_PGC, GOUP_PGC, TAIL_PGC, RSM = 9, 10, 11, 12, 13, 16
# menus, for JumpSS and CallSS
MENU_TITLE, MENU_ROOT, MENU_SUBPICTURE, MENU_AUDIO, MENU_ANGLE, MENU_PTT = 2, 3, 4, 5, 6, 7

# user operations a program chain may forbid (dvdinfo uops.html), as bits
(UOP_TIME_SEARCH, UOP_PTT_SEARCH, UOP_TITLE_PLAY, UOP_STOP, UOP_GO_UP, UOP_TIME_PTT_SEARCH,
 UOP_PREV_TOP_PG, UOP_NEXT_PG, UOP_FORWARD_SCAN, UOP_BACKWARD_SCAN, UOP_MENU_TITLE, UOP_MENU_ROOT,
 UOP_MENU_SUBPICTURE, UOP_MENU_AUDIO, UOP_MENU_ANGLE, UOP_MENU_PTT, UOP_RESUME, UOP_BUTTON,
 UOP_STILL_OFF, UOP_PAUSE_ON, UOP_AUDIO_CHANGE, UOP_SUBPICTURE_CHANGE, UOP_ANGLE_CHANGE,
 UOP_KARAOKE_MIX, UOP_VIDEO_MODE) = (1 << n for n in range(25))


@dataclass(frozen=True)
class Condition:
    """`a` (a register) compared with `b` (a register or a number)."""
    op: int
    a: Register
    b: int

    def __post_init__(self):
        if self.op not in _INVERSE and self.op != BC:
            raise VtsError(f"comparison {self.op} is not one of BC, EQ, NE, GE, GT, LE, LT")
        if not isinstance(self.a, Register):
            raise VtsError("the first operand of a comparison is a register")
        if not isinstance(self.b, Register) and not 0 <= self.b <= 0xFFFF:
            raise VtsError(f"{self.b} does not fit a 16-bit value")

    @property
    def immediate(self):
        return not isinstance(self.b, Register)

    def inverse(self):
        if self.op == BC:
            raise VtsError("a bitwise comparison has no opposite")
        return Condition(_INVERSE[self.op], self.a, self.b)


def compare(a, op, b):
    return Condition(op, a, b)


# where a type puts its comparison: (byte of cr1, byte of cr2, bytes of the
# immediate, immediate allowed)
_SLOTS = {
    "special": (3, 5, (4, 6), True),      # type 0
    "link": (3, 5, (4, 6), True),         # type 1, Direct 0
    "jump": (6, 7, None, False),          # type 1, Direct 1
    "set": (6, 7, None, False),           # type 2 (SetSystem)
    "gprm": (2, 7, (6, 8), True),         # type 3 (Set)
}


@dataclass(frozen=True)
class Instruction:
    kind: str
    body: bytes             # the 8 bytes without a comparison
    condition: object = None

    def encode(self):
        out = bytearray(self.body)
        c = self.condition
        if c is not None:
            cr1, cr2, imm, allowed = _SLOTS[self.kind]
            out[1] |= c.op << 4
            out[cr1] = c.a
            if c.immediate:
                if not allowed:
                    raise VtsError(f"a {self.kind} instruction compares registers only")
                out[1] |= 0x80
                struct.pack_into(">H", out, imm[0], c.b)
            else:
                out[cr2] = c.b
        return bytes(out)

    def when(self, condition):
        """This instruction made conditional (VtsError if it cannot be)."""
        if self.condition is not None:
            raise VtsError("the instruction already has a comparison")
        if self.kind == "set" and self.body[1] & 0x0F:
            raise VtsError("a set instruction with a link cannot also compare")
        if condition.immediate and not _SLOTS[self.kind][3]:
            raise VtsError(f"a {self.kind} instruction compares registers only")
        return Instruction(self.kind, self.body, condition)


def _ins(kind, *bytes_):
    return Instruction(kind, bytes(bytes_).ljust(8, b"\0"))


def _check(name, value, low, high):
    if not low <= value <= high:
        raise VtsError(f"{name} {value}: {low} to {high}")
    return value


# --- type 0: special --------------------------------------------------------

def nop():
    return _ins("special", 0, 0)


def goto(line):
    """Go to line `line` (1-based) of the same command list."""
    return _ins("special", 0, 1, 0, 0, 0, 0, 0, _check("line", line, 1, 255))


def break_():
    return _ins("special", 0, 2)


# --- type 1, Direct 0: links within the domain -----------------------------

def _button(button):
    return _check("button", button, 0, 63) << 10


def link(subset, button=0):
    """LinkTopCell, LinkNextCell, ... LinkTailPGC, RSM: one of the Link
    subset constants, with a button to highlight (0: none)."""
    if subset not in (TOP_CELL, NEXT_CELL, PREV_CELL, TOP_PG, NEXT_PG, PREV_PG, TOP_PGC,
                      NEXT_PGC, PREV_PGC, GOUP_PGC, TAIL_PGC, RSM):
        raise VtsError(f"{subset} is not a Link subset code")
    return Instruction("link", struct.pack(">BBHHH", 0x20, 1, 0, 0, _button(button) | subset))


def link_pgcn(pgcn):
    return Instruction("link", struct.pack(">BBHHH", 0x20, 4, 0, 0, _check("PGC", pgcn, 1, 32767)))


def link_pttn(pttn, button=0):
    return Instruction("link", struct.pack(">BBHHH", 0x20, 5, 0, 0,
                                           _button(button) | _check("PTT", pttn, 1, 99)))


def link_pgn(pgn, button=0):
    return Instruction("link", struct.pack(">BBHHH", 0x20, 6, 0, 0,
                                           _button(button) | _check("program", pgn, 1, 255)))


def link_cn(cn, button=0):
    return Instruction("link", struct.pack(">BBHHH", 0x20, 7, 0, 0,
                                           _button(button) | _check("cell", cn, 1, 255)))


# --- type 1, Direct 1: jumps and calls between domains ----------------------

def exit():
    return _ins("jump", 0x30, 1)


def jump_tt(ttn):
    """To title `ttn` of the disc (from a menu)."""
    return _ins("jump", 0x30, 2, 0, 0, 0, _check("title", ttn, 1, 99))


def jump_vts_tt(ttn):
    """To title `ttn` of this titleset."""
    return _ins("jump", 0x30, 3, 0, 0, 0, _check("title", ttn, 1, 99))


def jump_vts_ptt(ttn, pttn):
    """To chapter `pttn` of title `ttn` of this titleset."""
    return Instruction("jump", struct.pack(">BBHBBH", 0x30, 5, _check("PTT", pttn, 1, 1023), 0,
                                           _check("title", ttn, 1, 99), 0))


def jump_ss_fp():
    """To the first-play program chain."""
    return _ins("jump", 0x30, 6)


def jump_ss_vmgm_menu(menu):
    """To the VMG's menu of this kind (MENU_TITLE)."""
    return _ins("jump", 0x30, 6, 0, 0, 0, 0x40 | _check("menu", menu, 2, 7))


def jump_ss_vmgm_pgc(pgcn):
    """To program chain `pgcn` of the VMG's menus."""
    return Instruction("jump", struct.pack(">BBHBBH", 0x30, 6, _check("PGC", pgcn, 1, 32767),
                                           0, 0xC0, 0))


def jump_ss_vtsm(vts, ttn, menu):
    """To the menu of kind `menu` (MENU_ROOT, ...) of titleset `vts`, for
    its title `ttn`."""
    return Instruction("jump", struct.pack(">BBHBBH", 0x30, 6, _check("title", ttn, 1, 99),
                                           _check("titleset", vts, 1, 99),
                                           0x80 | _check("menu", menu, 2, 7), 0))


def _call(rsm_cell, byte5, pgcn=0):
    return Instruction("jump", struct.pack(">BBHBBH", 0x30, 8, pgcn,
                                           _check("resume cell", rsm_cell, 0, 255), byte5, 0))


def call_ss_fp(rsm_cell=1):
    """To the first-play program chain, to resume at cell `rsm_cell`."""
    return _call(rsm_cell, 0)


def call_ss_vmgm_menu(menu, rsm_cell=1):
    return _call(rsm_cell, 0x40 | _check("menu", menu, 2, 7))


def call_ss_vtsm_menu(menu, rsm_cell=1):
    return _call(rsm_cell, 0x80 | _check("menu", menu, 2, 7))


def call_ss_vmgm_pgc(pgcn, rsm_cell=1):
    return _call(rsm_cell, 0xC0, _check("PGC", pgcn, 1, 32767))


# --- type 2: the system registers -------------------------------------------

def set_stn(audio=None, subpicture=None, angle=None):
    """SetSTN: the audio stream (0-7), subpicture stream (0-63, +64 hidden) and angle
    (1-9) to play, each a number or a GPRM, those given."""
    values = (audio, subpicture, angle)
    if all(v is None for v in values):
        raise VtsError("SetSTN sets at least one of audio, subpicture and angle")
    regs = [isinstance(v, Register) for v in values if v is not None]
    if any(regs) and not all(regs):
        raise VtsError("SetSTN takes all registers or all numbers")
    direct = not regs[0]
    limits = (("audio", 0, 7), ("subpicture", 0, 127), ("angle", 1, 9))   # bit 6: do not show
    out = bytearray(((0x50 if direct else 0x40) | 1, 0, 0, 0, 0, 0, 0, 0))
    for k, (v, (name, low, high)) in enumerate(zip(values, limits)):
        if v is None:
            continue
        if isinstance(v, Register):
            if v >= 0x80:
                raise VtsError("SetSTN reads general registers only")
            out[3 + k] = 0x80 | v
        else:
            out[3 + k] = 0x80 | _check(name, v, low, high)
    return Instruction("set", bytes(out))


def set_hl_btnn(value):
    """SetHL_BTNN: the highlighted button, as button number * 1024, a number
    or a GPRM."""
    if isinstance(value, Register):
        if value >= 0x80:
            raise VtsError("SetHL_BTNN reads general registers only")
        return _ins("set", 0x46, 0, 0, 0, 0, value)
    return Instruction("set", struct.pack(">BBHHH", 0x56, 0, 0, _check("value", value, 0, 0xFFFF), 0))


def set_nvtmr(seconds, pgcn):
    """SetNVTMR: the navigation timer, a number of seconds or a GPRM, and
    the program chain to jump to when it runs out."""
    pgcn = _check("PGC", pgcn, 1, 32767)
    if isinstance(seconds, Register):
        return Instruction("set", struct.pack(">BBBBHH", 0x42, 0, 0, seconds, pgcn, 0))
    return Instruction("set", struct.pack(">BBHHH", 0x52, 0, _check("seconds", seconds, 0, 0xFFFF),
                                          pgcn, 0))


def set_gprmmd(gprm, value, counter=False):
    """SetGPRMMD: general register `gprm` set to a number or a register, in
    counter mode (counting seconds) if `counter`."""
    mode = (0x80 if counter else 0) | _check("GPRM", gprm, 0, 15)
    if isinstance(value, Register):
        return Instruction("set", struct.pack(">BBBBBBH", 0x43, 0, 0, value, 0, mode, 0))
    return Instruction("set", struct.pack(">BBHBBH", 0x53, 0, _check("value", value, 0, 0xFFFF),
                                          0, mode, 0))


# --- type 3: arithmetic on the general registers ----------------------------

def set_gprm(gprm, op, value):
    """Set: `gprm` op= `value`, a number or a register (MOV, SWP, ADD, SUB,
    MUL, DIV, MOD, RND, AND, OR, XOR)."""
    _check("GPRM", gprm, 0, 15)
    if not MOV <= op <= XOR:
        raise VtsError(f"{op} is not a set operation")
    if isinstance(value, Register):
        return Instruction("gprm", struct.pack(">BBHBBH", 0x60 | op, 0, gprm, 0, value, 0))
    return Instruction("gprm", struct.pack(">BBHHH", 0x70 | op, 0, gprm,
                                           _check("value", value, 0, 0xFFFF), 0))


# --- programs ---------------------------------------------------------------

@dataclass
class If:
    """`then` (instructions and Ifs) when `condition` holds, else `orelse`.
    `condition` is a Condition, or a list of them that must all hold."""
    condition: object
    then: list
    orelse: list = ()


@dataclass
class Any:
    """`then` when any of the `conditions` holds, else `orelse`."""
    conditions: list
    then: list
    orelse: list = ()


@dataclass(frozen=True)
class Label:
    """A place a Goto lands on; `name` is any hashable."""
    name: object


@dataclass(frozen=True)
class Goto:
    """Go to the Label `name`, when `condition` holds (always if None)."""
    name: object
    condition: object = None


def _transfers(ins):
    """Whether this instruction always leaves the command list."""
    return isinstance(ins, Instruction) and ins.condition is None and \
        (ins.kind == "jump" or (ins.kind in ("link", "set", "gprm") and ins.body[1] & 0x0F))


def _fold(placed):
    """A Set followed by a Link, neither comparing and nothing landing
    between them, as one command."""
    out = []
    for p in placed:
        prev = out[-1] if out else None
        if isinstance(p, Instruction) and p.kind == "link" and p.condition is None \
                and isinstance(prev, Instruction) and prev.kind in ("set", "gprm") \
                and prev.condition is None and not prev.body[1] & 0x0F:
            body = bytearray(prev.body)
            body[1] |= p.body[1] & 0x0F
            body[6:8] = p.body[6:8]
            out[-1] = Instruction(prev.kind, bytes(body))
        else:
            out.append(p)
    return out


def _conditions(c):
    return list(c) if isinstance(c, (list, tuple)) else [c]


def _flatten(items, out):
    """Place instructions and Ifs into `out`: [Instruction | ("goto", target
    list id, inverse)]; returns the list."""
    for item in items:
        if isinstance(item, (If, Any)):
            conds = _conditions(item.conditions if isinstance(item, Any) else item.condition)
            body = []
            _flatten(item.then, body)
            orelse = []
            _flatten(item.orelse, orelse)
            # one plain instruction that can take the comparison: fold it in
            if len(conds) == 1 and len(body) == 1 and not orelse and \
                    isinstance(body[0], Instruction) and body[0].condition is None:
                try:
                    out.append(body[0].when(conds[0]))
                    continue
                except VtsError:
                    pass
            end = object()
            else_start = object()
            start = object()
            skip = else_start if orelse else end
            if isinstance(item, Any):
                for c in conds[:-1]:
                    out.append(("goto", start, c))
                out.append(("goto", skip, conds[-1].inverse()))
                out.append(("label", start))
            else:
                for c in conds:
                    out.append(("goto", skip, c.inverse()))
            out.extend(body)
            if orelse:
                if not (body and _transfers(body[-1])):
                    out.append(("goto", end, None))
                out.append(("label", else_start))
                out.extend(orelse)
            out.append(("label", end))
        elif isinstance(item, Instruction):
            out.append(item)
        elif isinstance(item, Label):
            out.append(("label", item.name))
        elif isinstance(item, Goto):
            out.append(("goto", item.name, item.condition))
        else:
            raise VtsError(f"{item!r} is not an instruction")
    return out


def assemble(items):
    """The commands (8 bytes each) of a list of instructions and Ifs."""
    placed = _fold(_flatten(items, []))
    lines = {}
    n = 0
    for p in placed:
        if isinstance(p, tuple) and p[0] == "label":
            lines[p[1]] = n + 1
        else:
            n += 1
    for p in placed:
        if isinstance(p, tuple) and p[0] == "goto" and p[1] not in lines:
            raise VtsError(f"Goto {p[1]!r}: no such Label")
    needs_landing = any(isinstance(p, tuple) and p[0] == "goto" and lines[p[1]] == n + 1
                        for p in placed)
    if needs_landing:
        placed.append(nop())
        n += 1
    if n > 128:
        raise VtsError(f"{n} commands (a program chain holds at most 128)")
    out = []
    for p in placed:
        if isinstance(p, Instruction):
            out.append(p.encode())
        elif p[0] == "goto":
            ins = goto(lines[p[1]])
            out.append((ins.when(p[2]) if p[2] is not None else ins).encode())
    return out
