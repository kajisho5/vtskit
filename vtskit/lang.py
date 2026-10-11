"""dvdauthor's command language, compiled to the DVD virtual machine's
commands (vtskit.vm).

dvdauthor describes a disc's navigation in a small language (its manual
documents it): `jump title 2;`, `g1 = 0; call menu entry root;`,
`if (g5 != 0) { button = g5; } else { button = 1024; }`. A tool that
already speaks it, as oncemore does, can keep its text and hand it here. A
`Disc` knows the disc's titles and menus (the numbers a jump resolves to
depend on where the command runs: a title's number inside its titleset or
on the whole disc, a menu's program chain number) and compiles each
command list for the domain it runs in: "fp" (the first-play chain),
"vmgm" (the video manager's menus), "vtsm" (a titleset's menus) or "vts"
(a titleset's titles).

What a form compiles to follows dvdauthor's output for the same text
(tests/test_lang.py reads the commands back from discs dvdauthor
authored):

* in a titleset's menus `jump title N` is JumpVTS_TT, `jump title N
  chapter C` JumpVTS_PTT, `jump menu N` LinkPGCN; in the video manager's
  menus and the first-play chain `jump title N` is JumpTT (the title's
  number on the disc) and `jump menu N` JumpSS to that program chain;
  `jump vmgm menu entry title` is JumpSS to the entry menu, `call menu
  entry root` CallSS with the first cell to resume at;
* `audio = 1; subtitle = 65;` next to each other are one SetSTN; `g1 = 0;
  jump menu 2;` is one command (vtskit.vm folds a Set and a Link), and
  what follows an unconditional jump in a block is left out;
* a menu's button runs one command: a button whose text compiles to more
  sets GPRM 15 to its number and links to the end of the program chain,
  whose post commands start with a dispatcher (`g14 = g15; g15 = 0;` and a
  Goto per such button) and end with each button's code;
* a jump into another titleset (dvdauthor's "jumppad") cannot be one
  command: from the video manager a jump to a chapter sets GPRM 15 to a
  code naming the title and chapter and jumps to the titleset's chapter
  (PTT) menu, whose commands (`vts_pad`) decode it and JumpVTS_PTT; from a
  titleset's menus or titles it goes through a program chain of the video
  manager's menus (`vmgm_pads`) that does the same, or JumpTT for a whole
  title (a menu jumps to that chain, a title calls it). GPRM 13 and 14 are
  the pads' scratch, as dvdauthor keeps them; a disc using this language
  leaves GPRM 13 to 15 alone.
"""

import re
from dataclasses import dataclass, field

from . import vm
from .errors import VtsError

CODE_REG, SCRATCH, SCRATCH2 = 15, 14, 13
PAD_TARGETS = 30           # cross-titleset targets per video manager pad chain (128 commands)

_TOKEN = re.compile(r"\s*(?:(\d+)|([A-Za-z_]\w*)|(==|!=|<=|>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|[-+*/%&|^<>=(){};,]))")
_COMPARE = {"==": vm.EQ, "eq": vm.EQ, "!=": vm.NE, "ne": vm.NE, ">=": vm.GE, "ge": vm.GE,
            ">": vm.GT, "gt": vm.GT, "<=": vm.LE, "le": vm.LE, "<": vm.LT, "lt": vm.LT}
_MIRROR = {vm.EQ: vm.EQ, vm.NE: vm.NE, vm.GE: vm.LE, vm.LE: vm.GE, vm.GT: vm.LT, vm.LT: vm.GT}
_ARITH = {"+": vm.ADD, "-": vm.SUB, "*": vm.MUL, "/": vm.DIV, "%": vm.MOD, "&": vm.AND,
          "|": vm.OR, "^": vm.XOR}
_SYSTEM = {"audio": 1, "subtitle": 2, "angle": 3, "button": 8}
_LINKS = {("next", "pgc"): vm.NEXT_PGC, ("prev", "pgc"): vm.PREV_PGC, ("pgc", "top"): vm.TOP_PGC,
          ("pgc", "tail"): vm.TAIL_PGC, ("up", "pgc"): vm.GOUP_PGC,
          ("next", "program"): vm.NEXT_PG, ("prev", "program"): vm.PREV_PG,
          ("program", "top"): vm.TOP_PG, ("next", "cell"): vm.NEXT_CELL,
          ("prev", "cell"): vm.PREV_CELL, ("cell", "top"): vm.TOP_CELL}
_ENTRIES = {"title": vm.MENU_TITLE, "root": vm.MENU_ROOT, "subtitle": vm.MENU_SUBPICTURE,
            "audio": vm.MENU_AUDIO, "angle": vm.MENU_ANGLE, "ptt": vm.MENU_PTT}


# --- the language -------------------------------------------------------------

def tokens(text):
    out, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise VtsError(f"cannot read the command at {text[pos:pos + 20]!r}")
        pos = m.end()
        if m.group(1):
            out.append(int(m.group(1)))
        else:
            out.append(m.group(2) or m.group(3))
    return out


@dataclass
class Reg:
    code: int           # a vm.Register


@dataclass
class Num:
    value: int


@dataclass
class Random:
    value: int


@dataclass
class Arith:
    left: object
    op: str
    right: object


@dataclass
class Assign:
    target: object      # ("g", n) or a system register's name
    op: str             # "=", "+=", ...
    value: object


@dataclass
class Compare:
    left: object
    op: int
    right: object


@dataclass
class Junction:
    kind: str           # "and" / "or"
    parts: list


@dataclass
class If:
    condition: object
    then: list
    orelse: list


@dataclass
class Jump:
    words: list         # the words after "jump" or "call", numbers as ints


@dataclass
class Call:
    words: list
    resume: int


@dataclass
class Word:
    name: str           # "resume", "exit", "break"


class _Parser:
    def __init__(self, text):
        self.t = tokens(text)
        self.i = 0

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def take(self, want=None):
        tok = self.peek()
        if tok is None or (want is not None and tok != want):
            raise VtsError(f"expected {want!r}, found {tok!r}" if want else "the command ends early")
        self.i += 1
        return tok

    def program(self):
        out = []
        while self.peek() is not None:
            out.append(self.statement())
        return out

    def block(self):
        if self.peek() == "{":
            self.take("{")
            out = []
            while self.peek() != "}":
                out.append(self.statement())
            self.take("}")
            return out
        return [self.statement()]

    def statement(self):
        tok = self.take()
        if tok == "if":
            self.take("(")
            cond = self.condition()
            self.take(")")
            then = self.block()
            orelse = []
            if self.peek() == "else":
                self.take("else")
                orelse = self.block()
            return If(cond, then, orelse)
        if tok in ("jump", "call"):
            words = []
            while self.peek() not in (";", None):
                words.append(self.take())
            self.take(";")
            if tok == "call":
                resume = 1
                if len(words) >= 2 and words[-2] == "resume":
                    resume, words = words[-1], words[:-2]
                return Call(words, resume)
            return Jump(words)
        if tok in ("resume", "exit", "break"):
            self.take(";")
            return Word(tok)
        if isinstance(tok, str) and (tok in _SYSTEM or re.fullmatch(r"g\d+", tok)):
            target = ("g", int(tok[1:])) if tok[0] == "g" and tok not in _SYSTEM else tok
            op = self.take()
            if op not in ("=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^="):
                raise VtsError(f"{op!r} is not an assignment")
            value = self.expression()
            self.take(";")
            return Assign(target, op, value)
        raise VtsError(f"cannot read the statement starting {tok!r}")

    def condition(self):
        parts = [self.conjunction()]
        while self.peek() in ("or", "||"):
            self.take()
            parts.append(self.conjunction())
        return parts[0] if len(parts) == 1 else Junction("or", parts)

    def conjunction(self):
        parts = [self.comparison()]
        while self.peek() in ("and", "&&"):
            self.take()
            parts.append(self.comparison())
        return parts[0] if len(parts) == 1 else Junction("and", parts)

    def comparison(self):
        if self.peek() == "(":
            self.take("(")
            c = self.condition()
            self.take(")")
            return c
        left = self.expression()
        op = self.take()
        if op not in _COMPARE:
            raise VtsError(f"{op!r} is not a comparison")
        right = self.expression()
        return Compare(left, _COMPARE[op], right)

    def expression(self):
        left = self.term()
        while self.peek() in _ARITH:
            op = self.take()
            left = Arith(left, op, self.term())
        return left

    def term(self):
        tok = self.take()
        if isinstance(tok, int):
            return Num(tok)
        if tok == "random":
            self.take("(")
            n = self.take()
            self.take(")")
            return Random(n)
        if tok in _SYSTEM:
            return Reg(vm.SPRM(_SYSTEM[tok]))
        m = re.fullmatch(r"([gs])(\d+)", tok or "")
        if m:
            return Reg(vm.GPRM(int(m.group(2))) if m.group(1) == "g" else vm.SPRM(int(m.group(2))))
        raise VtsError(f"{tok!r} is not a value")


def parse(text):
    """The statements of a command text."""
    return _Parser(text).program()


# --- the disc ----------------------------------------------------------------

@dataclass
class Menus:
    """A menu domain: how many program chains its own menus take, in
    order, and the number of each entry menu ("title", "root", "ptt",
    ...)."""
    count: int = 0
    entries: dict = field(default_factory=dict)


class Disc:
    """The disc a command text is compiled for: `titles` {(titleset, title
    number in it): title number on the disc}, `vmgm` the video manager's
    Menus, `titlesets` {titleset: Menus}."""

    def __init__(self, titles, vmgm=None, titlesets=None):
        self.titles = dict(titles)
        self.by_number = {n: key for key, n in self.titles.items()}
        self.vmgm = vmgm or Menus()
        self.titlesets = dict(titlesets or {})
        self.vmgm_targets = []          # (titleset, title, chapter) reached through the VMGM pads
        self.vts_targets = {}           # titleset -> {(title, chapter)} decoded by its PTT pad

    # -- resolving --------------------------------------------------------------
    def _menus(self, vts):
        if vts not in self.titlesets:
            raise VtsError(f"titleset {vts} is not on the disc")
        return self.titlesets[vts]

    def _global(self, vts, ttn):
        if (vts, ttn) not in self.titles:
            raise VtsError(f"title {ttn} of titleset {vts} is not on the disc")
        return self.titles[(vts, ttn)]

    def _entry(self, menus, kind, where):
        if kind not in _ENTRIES:
            raise VtsError(f"{kind!r} is not a menu entry")
        if kind not in menus.entries:
            raise VtsError(f"{where} has no {kind} menu")
        return menus.entries[kind]

    def _vts_code(self, vts, ttn, pttn):
        self.vts_targets.setdefault(vts, set()).add((ttn, pttn))
        return 0x8000 | ttn << 8 | pttn

    def _vmgm_pad(self, target):
        """(code, PGC number) of the video manager pad that reaches `target`."""
        if target not in self.vmgm_targets:
            self.vmgm_targets.append(target)
        k = self.vmgm_targets.index(target)
        return k % PAD_TARGETS + 1, self.vmgm.count + 1 + k // PAD_TARGETS

    def _to_title(self, vts, ttn, pttn, domain, here):
        """Instructions jumping to chapter `pttn` (0: the start) of title
        `ttn` of titleset `vts`, from `domain` (in titleset `here`)."""
        if domain in ("vts", "vtsm"):
            if vts == here:
                return [vm.jump_vts_ptt(ttn, pttn) if pttn else vm.jump_vts_tt(ttn)]
            # another titleset: through a pad chain of the video manager (a
            # title cannot JumpSS: it calls the chain, which jumps on)
            self._global(vts, ttn)
            code, pgcn = self._vmgm_pad((vts, ttn, pttn))
            go = vm.jump_ss_vmgm_pgc(pgcn) if domain == "vtsm" else vm.call_ss_vmgm_pgc(pgcn)
            return [vm.set_gprm(CODE_REG, vm.MOV, code), go]
        number = self._global(vts, ttn)
        if not pttn:
            return [vm.jump_tt(number)]
        self._menus(vts)
        return [vm.set_gprm(CODE_REG, vm.MOV, self._vts_code(vts, ttn, pttn)),
                vm.jump_ss_vtsm(vts, 1, vm.MENU_PTT)]

    def _jump(self, words, domain, here):
        w = list(words)

        def number(what):
            if not w or not isinstance(w[0], int):
                raise VtsError(f"jump {' '.join(map(str, words))}: a {what} number is missing")
            return w.pop(0)

        def chapter():
            if w[:1] == ["chapter"]:
                w.pop(0)
                return number("chapter")
            return 0

        def title(vts):
            ttn = number("title")
            out = self._to_title(vts, ttn, chapter(), domain, here)
            return out

        if not w:
            raise VtsError("jump what?")
        head = w.pop(0)
        if head == "title":
            if domain in ("vmgm", "fp"):
                n = number("title")
                if n not in self.by_number:
                    raise VtsError(f"title {n} is not on the disc")
                vts, ttn = self.by_number[n]
                out = self._to_title(vts, ttn, chapter(), domain, here)
            else:
                out = title(here)
        elif head == "titleset":
            vts = number("titleset")
            if w[:1] == ["title"]:
                w.pop(0)
                out = title(vts)
            elif w[:1] == ["menu"]:
                w.pop(0)
                kind = vm.MENU_ROOT
                if w[:1] == ["entry"]:
                    w.pop(0)
                    kind = _ENTRIES.get(w.pop(0) if w else None)
                    if kind is None:
                        raise VtsError("jump titleset N menu entry what?")
                out = [vm.jump_ss_vtsm(vts, 1, kind)]
            else:
                raise VtsError("jump titleset N title K or menu")
        elif head == "menu":
            if domain == "vts":
                raise VtsError("a title cannot jump to a menu: call it")
            menus = self.vmgm if domain in ("vmgm", "fp") else self._menus(here)
            if w[:1] == ["entry"]:
                w.pop(0)
                pgcn = self._entry(menus, w.pop(0) if w else None,
                                   "the video manager" if domain in ("vmgm", "fp") else f"titleset {here}")
            elif w and isinstance(w[0], int):
                pgcn = w.pop(0)
            else:
                pgcn = self._entry(menus, "root" if domain == "vtsm" else "title", "the menu domain")
            out = [vm.link_pgcn(pgcn) if domain == "vtsm" else vm.jump_ss_vmgm_pgc(pgcn)]
        elif head == "vmgm":
            if w[:1] != ["menu"]:
                raise VtsError("jump vmgm menu ...")
            w.pop(0)
            if w[:1] == ["entry"]:
                w.pop(0)
                kind = _ENTRIES.get(w.pop(0) if w else None)
                if kind is None:
                    raise VtsError("jump vmgm menu entry what?")
                out = [vm.jump_ss_vmgm_menu(kind)]
            elif w and isinstance(w[0], int):
                out = [vm.jump_ss_vmgm_pgc(w.pop(0))]
            else:
                out = [vm.jump_ss_vmgm_menu(vm.MENU_TITLE)]
        elif head == "fpc":
            out = [vm.jump_ss_fp()]
        elif head == "cell":
            out = [vm.link_cn(number("cell"))]
        elif head == "program":
            out = [vm.link_pgn(number("program"))]
        elif head == "chapter":
            out = [vm.link_pttn(number("chapter"))]
        elif (head, w[0] if w else None) in _LINKS:
            out = [vm.link(_LINKS[(head, w.pop(0))])]
        else:
            raise VtsError(f"cannot jump to {' '.join(map(str, words))!r}")
        if w:
            raise VtsError(f"jump {' '.join(map(str, words))}: {' '.join(map(str, w))!r} left over")
        return out

    def _call(self, words, resume, domain, here):
        w = list(words)
        if domain not in ("vts",):
            # (dvdauthor allows CallSS from titles; a menu "calls" nothing to resume)
            raise VtsError("call is for a title's commands")
        if w[:1] == ["vmgm"]:
            w.pop(0)
            if w[:1] != ["menu"]:
                raise VtsError("call vmgm menu ...")
            w.pop(0)
            kind = vm.MENU_TITLE
            if w[:1] == ["entry"]:
                w.pop(0)
                kind = _ENTRIES.get(w.pop(0) if w else None)
            elif w and isinstance(w[0], int):
                return [vm.call_ss_vmgm_pgc(w.pop(0), resume)]
            if kind is None or w:
                raise VtsError(f"cannot call {' '.join(map(str, words))!r}")
            return [vm.call_ss_vmgm_menu(kind, resume)]
        if w[:1] == ["menu"]:
            w.pop(0)
            kind = vm.MENU_ROOT
            if w[:1] == ["entry"]:
                w.pop(0)
                kind = _ENTRIES.get(w.pop(0) if w else None)
            if kind is None or w:
                raise VtsError(f"cannot call {' '.join(map(str, words))!r}")
            return [vm.call_ss_vtsm_menu(kind, resume)]
        if w == ["fpc"]:
            return [vm.call_ss_fp(resume)]
        raise VtsError(f"cannot call {' '.join(map(str, words))!r}")

    # -- compiling --------------------------------------------------------------
    def _value(self, e, what):
        if isinstance(e, Num):
            return e.value
        if isinstance(e, Reg):
            return e.code
        raise VtsError(f"{what}: a number or a register")

    def _assign(self, a):
        if a.target in _SYSTEM:
            raise VtsError("system registers are set in _system")
        n = a.target[1]
        if a.op != "=":
            return vm.set_gprm(n, _ARITH[a.op[0]], self._value(a.value, f"g{n} {a.op}"))
        if isinstance(a.value, Random):
            return vm.set_gprm(n, vm.RND, a.value.value)
        if isinstance(a.value, Arith):
            left, op, right = a.value.left, a.value.op, a.value.right
            if not (isinstance(left, Reg) and left.code == vm.GPRM(n)):
                raise VtsError(f"g{n} = {op}: only g{n} = g{n} {op} value is compiled (no scratch registers)")
            return vm.set_gprm(n, _ARITH[op], self._value(right, f"g{n} = g{n} {op}"))
        return vm.set_gprm(n, vm.MOV, self._value(a.value, f"g{n} ="))

    def _system(self, assigns):
        """One SetSTN for a run of audio / subtitle / angle assignments, or
        a SetHL_BTNN."""
        if assigns[0].target == "button":
            a = assigns[0]
            if a.op != "=":
                raise VtsError("button = value")
            return vm.set_hl_btnn(self._value(a.value, "button ="))
        fields = {}
        for a in assigns:
            if a.op != "=":
                raise VtsError(f"{a.target} = value")
            fields[a.target] = self._value(a.value, f"{a.target} =")
        return vm.set_stn(fields.get("audio"), fields.get("subtitle"), fields.get("angle"))

    def _condition(self, c):
        if isinstance(c, Junction):
            raise VtsError("and / or inside a comparison")
        left, right = c.left, c.right
        op = c.op
        if isinstance(left, Num) and isinstance(right, Reg):
            left, right, op = right, left, _MIRROR[op]
        if not isinstance(left, Reg):
            raise VtsError("a comparison needs a register")
        return vm.compare(left.code, op, self._value(right, "a comparison"))

    def _statements(self, stmts, domain, here):
        out = []
        i = 0
        while i < len(stmts):
            s = stmts[i]
            if isinstance(s, Assign) and s.target in _SYSTEM and s.target != "button":
                run = []
                while i < len(stmts) and isinstance(stmts[i], Assign) and \
                        stmts[i].target in ("audio", "subtitle", "angle") and \
                        stmts[i].target not in [r.target for r in run]:
                    run.append(stmts[i])
                    i += 1
                out.append(self._system(run))
                continue
            i += 1
            if isinstance(s, Assign):
                out.append(self._system([s]) if s.target == "button" else self._assign(s))
            elif isinstance(s, If):
                then = self._statements(s.then, domain, here)
                orelse = self._statements(s.orelse, domain, here)
                c = s.condition
                if isinstance(c, Junction) and c.kind == "or":
                    out.append(vm.Any([self._condition(p) for p in c.parts], then, orelse))
                elif isinstance(c, Junction):
                    out.append(vm.If([self._condition(p) for p in c.parts], then, orelse))
                else:
                    out.append(vm.If(self._condition(c), then, orelse))
            elif isinstance(s, Jump):
                out.extend(self._jump(s.words, domain, here))
            elif isinstance(s, Call):
                out.extend(self._call(s.words, s.resume, domain, here))
            elif isinstance(s, Word):
                out.append({"resume": vm.link(vm.RSM), "exit": vm.exit(), "break": vm.break_()}[s.name])
            else:
                raise VtsError(f"{s!r} is not a statement")
            # what follows an unconditional transfer never runs: dvdauthor leaves it out
            if out and vm._transfers(out[-1]):
                break
        return out

    def _check_domain(self, domain):
        if domain not in ("fp", "vmgm", "vtsm", "vts"):
            raise VtsError(f"{domain!r} is not a domain (fp, vmgm, vtsm, vts)")

    def instructions(self, text, domain, vts=0):
        """The vm instructions of a command text."""
        self._check_domain(domain)
        if domain in ("vtsm", "vts") and vts not in self.titlesets:
            raise VtsError(f"titleset {vts} is not on the disc")
        return self._statements(parse(text), domain, vts)

    def compile(self, text, domain, vts=0):
        """The commands (8 bytes each) of a command text run in `domain`
        (of titleset `vts` for "vtsm" and "vts")."""
        return vm.assemble(self.instructions(text, domain, vts))

    def menu(self, buttons, post, domain="vtsm", vts=0):
        """A menu's buttons and post commands: ([one command a button],
        post commands). A button whose text takes several commands runs
        them from the post, through a dispatcher, as dvdauthor arranges."""
        self._check_domain(domain)
        if domain not in ("vtsm", "vmgm"):
            raise VtsError("buttons belong to menus")
        commands, deferred = [], []
        for n, text in enumerate(buttons, 1):
            items = self.instructions(text, domain, vts)
            code = vm.assemble(items) if items else []
            if len(code) == 1:
                commands.append(code[0])
            else:
                deferred.append((n, items))
                commands.append(vm.assemble([vm.set_gprm(CODE_REG, vm.MOV, n), vm.link(vm.TAIL_PGC)])[0])
        post_items = self.instructions(post, domain, vts) if post else [vm.exit()]
        if not deferred:
            return commands, vm.assemble(post_items)
        if not (post_items and vm._transfers(post_items[-1])):
            raise VtsError("a menu with multi-command buttons needs post commands that leave the chain")
        items = [vm.set_gprm(SCRATCH, vm.MOV, vm.GPRM(CODE_REG)), vm.set_gprm(CODE_REG, vm.MOV, 0)]
        folded = None
        if len(deferred) == 1 and len(post_items) == 1 and isinstance(post_items[0], vm.Instruction):
            # one such button and a post of one command: the command runs
            # unless the button was pressed (as dvdauthor folds it)
            try:
                folded = post_items[0].when(vm.compare(vm.GPRM(SCRATCH), vm.NE, deferred[0][0]))
            except VtsError:
                pass
        if folded is not None:
            items.append(folded)
        else:
            items += [vm.Goto(("button", n), vm.compare(vm.GPRM(SCRATCH), vm.EQ, n)) for n, _ in deferred]
            items += post_items
        for n, body in deferred:
            items += [vm.Label(("button", n))] + body
        return commands, vm.assemble(items)

    # -- the pads ---------------------------------------------------------------
    def vmgm_pads(self):
        """The pre commands of each pad program chain the video manager's
        menus need, after its own `vmgm.count` chains, in order."""
        pads = []
        for first in range(0, len(self.vmgm_targets), PAD_TARGETS):
            items = [vm.set_gprm(SCRATCH, vm.MOV, vm.GPRM(CODE_REG)), vm.set_gprm(CODE_REG, vm.MOV, 0)]
            for k, (vts, ttn, pttn) in enumerate(self.vmgm_targets[first:first + PAD_TARGETS], 1):
                items.append(vm.set_gprm(SCRATCH2, vm.MOV, k))
                cond = vm.compare(vm.GPRM(SCRATCH), vm.EQ, vm.GPRM(SCRATCH2))
                if pttn:
                    items.append(vm.If(cond, [vm.set_gprm(CODE_REG, vm.MOV, self._vts_code(vts, ttn, pttn)),
                                              vm.jump_ss_vtsm(vts, 1, vm.MENU_PTT)]))
                else:
                    items.append(vm.If(cond, [vm.jump_tt(self._global(vts, ttn))]))
            # nothing matched: the title menu, else title 1
            if "title" in self.vmgm.entries:
                items.append(vm.jump_ss_vmgm_pgc(self.vmgm.entries["title"]))
            else:
                items.append(vm.jump_tt(1))
            pads.append(vm.assemble(items))
        return pads

    def vts_pad(self, vts):
        """The pre commands of titleset `vts`'s chapter (PTT) menu chain,
        decoding the code a pad jump left in GPRM 15, or None when nothing
        jumps into the titleset that way. Ends at the root menu."""
        targets = sorted(self.vts_targets.get(vts, ()))
        if not targets:
            return None
        items = [vm.set_gprm(SCRATCH, vm.MOV, vm.GPRM(CODE_REG)), vm.set_gprm(CODE_REG, vm.MOV, 0)]
        for ttn, pttn in targets:
            items.append(vm.set_gprm(SCRATCH2, vm.MOV, 0x8000 | ttn << 8 | pttn))
            items.append(vm.If(vm.compare(vm.GPRM(SCRATCH), vm.EQ, vm.GPRM(SCRATCH2)),
                               [vm.jump_vts_ptt(ttn, pttn)]))
        menus = self._menus(vts)
        items.append(vm.link_pgcn(menus.entries["root"]) if "root" in menus.entries else vm.exit())
        return vm.assemble(items)
