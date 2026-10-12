"""Menus: a program chain of a menu domain with its buttons.

A menu is a VOB like a title's, with a subpicture overlay (vtskit.spu)
drawn where the buttons light up, and the highlight information (HLI) of
every navigation pack's PCI (https://dvdnav.mplayerhq.hu/dvdinfo/pci_pkt.html)
saying where each button is, which button each arrow key leads to, and the
command a button runs. The colours come from the program chain's palette:
entry 0 is the highlight (a button selected), entry 1 the selection (a
button activated), as dvdauthor and spumux arrange them; the overlay is
invisible until a button is selected. A menu may instead show its buttons
in a `normal` colour all the time (spumux's `image`): then the palette is
normal, highlight, selection, and the overlay draws palette 0.

A menu without a stream is a program chain of commands alone (its `pre`
runs and leaves), as dvdauthor writes a `<pgc>` without a `<vob>`.

A title may carry buttons too, over a stretch of it (TitleButtons): their
overlay is a forced subpicture shown for the stretch (`TitleButtons.cue`,
added to the title's stream with vtskit.subtitles.mux), and the HLI is in
the navigation packs of the VOBUs that play during the stretch, with the
stretch's start and end as its start time (hli_s_ptm), end time (hli_e_ptm)
and the end of the user's selection (btn_sl_e_ptm: "ignore user after
this", dvdinfo); the other VOBUs have none (hli_ss 0: "no highlight
information for this VOBU"). dvdauthor gives a title's buttons the whole
title instead: the HLI in every VOBU, from the first picture, never ending,
which is what a stretch without an end writes.

From dvdauthor's output: the HLI is repeated whole in every VOBU
(HLI_SS 1), its start time is the menu's first picture, its end times are
0xFFFFFFFF (never), the buttons form one group of the normal kind, every
button uses colour table 1, and in that table only the pattern code (1) is
coloured: palette 0 and opaque when selected, palette 1 and opaque when
activated.
"""

import struct
from dataclasses import dataclass, field

from . import spu, subtitles
from .errors import VtsError

YELLOW, RED = (0xD2, 0x92, 0x10), (0x52, 0xF0, 0x5A)      # Y, Cr, Cb


@dataclass
class Button:
    """A button: its rectangle (x0, y0, x1, y1 in the picture, x1 and y1 the
    last column and line + 1), the command it runs (8 bytes), the buttons
    (numbers, from 1) the arrow keys lead to (default: itself), and whether
    it runs as soon as it is selected (`auto`)."""
    rect: tuple
    command: bytes
    up: int = 0
    down: int = 0
    left: int = 0
    right: int = 0
    auto: bool = False


@dataclass
class Menu:
    """A menu: its program stream (ffmpeg -f dvd, no subpictures), its
    buttons, the overlay of pixel codes lighting up the buttons (code 1
    where a button shows when selected; by default the buttons' rectangles),
    the highlight and selection colours (Y, Cr, Cb, and a contrast 0-15,
    15 if left out), the colour the buttons show in otherwise (None: they
    show nothing), where its cells start (seconds, as a title's chapters; a
    loop point), its commands, and which remote key it answers (`entry`:
    "title" in the VMG; "root", "subpicture", "audio", "angle" or "ptt" in
    a titleset; None for a menu reached by commands). `stream` None: a
    program chain of commands only, without pictures or buttons."""
    stream: str | None
    buttons: list = field(default_factory=list)
    overlay: object = None
    highlight: tuple = YELLOW
    select: tuple = RED
    normal: tuple | None = None
    cells: tuple = (0.0,)
    pre: tuple = ()
    post: tuple = ()                # none: Exit, as dvdauthor writes a menu without post commands
    entry: str | None = None
    first_button: int = 0          # the button selected when the menu shows (0: the player's choice)
    button_offset: int = 0         # added to a number key's digit to pick a button
    uops: int = 0                  # user operations the menu forbids (bit n: vtskit.vm.UOP_*)


@dataclass
class TitleButtons:
    """Buttons over a stretch of a title: from `start` to `end` (seconds
    from the title's first picture; `end` None: to the end, the buttons
    never taken off), the buttons, the entries of the title's palette
    (0-15) and contrasts (0-15) drawing a button selected (`highlight`) and
    activated (`select`) as (entry, contrast), the entry and contrast the
    buttons show in otherwise (`normal`; None: nothing), the overlay (code
    1 where a button lights up; by default the buttons' rectangles), the
    button selected when they show (0: the player's choice) and the number
    added to a number key's digit."""
    start: float
    end: float | None
    buttons: list
    highlight: tuple
    select: tuple
    normal: tuple | None = None
    overlay: object = None
    first_button: int = 0
    button_offset: int = 0

    def cue(self, width, height, length=None):
        """The overlay as a subtitle cue for vtskit.subtitles.mux: forced
        (shown with subtitles off), from `start` to `end` (or `length`
        seconds, the title's, for buttons without an end), transparent or
        in the normal colour."""
        end = self.end if self.end is not None else length
        if end is None:
            raise VtsError("buttons without an end: give the title's length")
        o = self.overlay if self.overlay is not None else \
            spu.Overlay.from_rects(width, height, [b.rect for b in self.buttons])
        if (o.width, o.height) != (width, height):
            raise VtsError("the overlay is not the picture's size")
        box = o.bounds()
        if box is None:
            raise VtsError("the overlay is blank")
        x0, y0, x1, y1 = box
        crop = b"".join(o.pixels[y * width + x0:y * width + x1 + 1] for y in range(y0, y1 + 1))
        entry, contrast = self.normal if self.normal is not None else (0, 0)
        return subtitles.Cue(self.start, end, spu.Overlay(x1 - x0 + 1, y1 - y0 + 1, crop), x0, y0,
                             colours=(0, entry, 0, 0), contrasts=(0, contrast, 0, 0), forced=True)

    def hli(self, s_ptm, e_ptm):
        """The highlight information shown from `s_ptm` to `e_ptm` (the
        stretch's start and end in the VOB's time)."""
        return _hli(self.buttons, s_ptm, e_ptm, self.button_offset, self.first_button,
                    self.highlight + self.select)


def check_title_buttons(tb, width, height, what):
    """Refuse buttons a title cannot carry."""
    for name, pair in (("highlight", tb.highlight), ("selection", tb.select), ("normal", tb.normal)):
        if pair is None and name == "normal":
            continue
        if len(pair) != 2 or not all(isinstance(v, int) and 0 <= v <= 15 for v in pair):
            raise VtsError(f"{what}: the {name} is (palette entry 0-15, contrast 0-15)")
    if tb.start < 0 or tb.end is not None and tb.end <= tb.start:
        raise VtsError(f"{what}: buttons from {tb.start} to {tb.end} s")
    if not tb.buttons:
        raise VtsError(f"{what}: no buttons")
    _check_buttons(tb.buttons, tb.first_button, tb.button_offset, width, height)


ENTRY_TYPES = {"title": 2, "root": 3, "subpicture": 4, "audio": 5, "angle": 6, "ptt": 7}
ENTRY_FLAGS = {"title": 0x80, "root": 0x80, "subpicture": 0x40, "audio": 0x20, "angle": 0x10, "ptt": 0x08}


def _colour(c, what):
    """(Y, Cr, Cb), contrast of a colour given as 3 or 4 numbers."""
    if len(c) not in (3, 4) or not all(isinstance(v, int) for v in c):
        raise VtsError(f"the {what} colour is (Y, Cr, Cb) or (Y, Cr, Cb, contrast)")
    contrast = c[3] if len(c) == 4 else 15
    if not 0 <= contrast <= 15:
        raise VtsError(f"the {what} colour's contrast is 0 to 15")
    return tuple(c[:3]), contrast


def check(menu, width, height):
    if menu.stream is None and menu.buttons:
        raise VtsError("a menu without a stream has no buttons")
    if len(menu.buttons) > 36:
        raise VtsError(f"{len(menu.buttons)} buttons (a menu has at most 36)")
    _colour(menu.highlight, "highlight")
    _colour(menu.select, "selection")
    if menu.normal is not None:
        _colour(menu.normal, "normal")
    if menu.entry is not None and menu.entry not in ENTRY_TYPES:
        raise VtsError(f"{menu.entry!r} is not a menu entry kind")
    _check_buttons(menu.buttons, menu.first_button, menu.button_offset, width, height)


def _check_buttons(buttons, first_button, button_offset, width, height):
    if len(buttons) > 36:
        raise VtsError(f"{len(buttons)} buttons (at most 36)")
    for n, b in enumerate(buttons, 1):
        x0, y0, x1, y1 = b.rect
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise VtsError(f"button {n}: {b.rect} is not inside {width}x{height}")
        if len(b.command) != 8:
            raise VtsError(f"button {n}: a command is 8 bytes")
        for k in (b.up, b.down, b.left, b.right):
            if not 0 <= k <= len(buttons):
                raise VtsError(f"button {n} points at button {k}, which is not there")
    if not 0 <= first_button <= len(buttons):
        raise VtsError(f"first button {first_button}: there are {len(buttons)}")
    if not 0 <= button_offset <= 255:
        raise VtsError("the button offset is 0 to 255")


def overlay(menu, width, height):
    """The menu's overlay: as given, or its buttons' rectangles."""
    if menu.overlay is not None:
        if (menu.overlay.width, menu.overlay.height) != (width, height):
            raise VtsError("the overlay is not the picture's size")
        return menu.overlay
    return spu.Overlay.from_rects(width, height, [b.rect for b in menu.buttons])


def palette(menu):
    """The program chain's colours: the normal colour if any, then the
    highlight and the selection; none for a menu without buttons."""
    if not menu.buttons:
        return ()
    colours = [_colour(menu.highlight, "highlight")[0], _colour(menu.select, "selection")[0]]
    if menu.normal is not None:
        colours.insert(0, _colour(menu.normal, "normal")[0])
    return tuple(colours)


def unit(menu, width, height):
    """The menu's subpicture unit: the overlay, shown in the normal colour
    (palette 0, its contrast) or not at all."""
    o = overlay(menu, width, height)
    if menu.normal is None:
        return spu.encode(o)
    return spu.encode(o, colours=(0, 0, 0, 0), contrasts=(0, _colour(menu.normal, "normal")[1], 0, 0))


def hli(menu, s_ptm):
    """The highlight information of the menu's navigation packs (the PCI
    from 0x60 to 0x316), shown from `s_ptm`, the menu's first picture."""
    first = 1 if menu.normal is not None else 0
    return _hli(menu.buttons, s_ptm, 0xFFFFFFFF, menu.button_offset, menu.first_button,
                (first, _colour(menu.highlight, "highlight")[1],
                 first + 1, _colour(menu.select, "selection")[1]))


def _hli(buttons, s_ptm, e_ptm, button_offset, first_button, colours):
    """The highlight information (PCI 0x60 to 0x316): the buttons, shown
    from `s_ptm` to `e_ptm` (hli_s_ptm, hli_e_ptm; the user's selection
    ends with them, btn_sl_e_ptm), drawn selected and activated with the
    palette entries and contrasts `colours` (entry, contrast, entry,
    contrast)."""
    out = bytearray(0x316 - 0x60)
    if not buttons:
        return bytes(out)
    struct.pack_into(">HIIIHBBBBBB", out, 0, 1, s_ptm, e_ptm, e_ptm, 0x1000,
                     button_offset, len(buttons), len(buttons), 0, first_button, 0)
    # colour table 1: the pattern code, the highlight's palette entry and
    # contrast when selected, the selection's when activated; the other
    # codes stay as they are
    hl, hl_c, sel, sel_c = colours
    out[0x16:0x1E] = bytes((0x00, hl << 4, 0x00, hl_c << 4, 0x00, sel << 4, 0x00, sel_c << 4))
    for n, b in enumerate(buttons):
        x0, y0, x1, y1 = b.rect
        e = 0x2E + 18 * n
        out[e] = 1 << 6 | x0 >> 4
        out[e + 1] = (x0 & 0xF) << 4 | x1 >> 8
        out[e + 2] = x1 & 0xFF
        out[e + 3] = (0x40 if b.auto else 0) | y0 >> 4      # auto action mode 01
        out[e + 4] = (y0 & 0xF) << 4 | y1 >> 8
        out[e + 5] = y1 & 0xFF
        out[e + 6:e + 10] = bytes(k or n + 1 for k in (b.up, b.down, b.left, b.right))
        out[e + 10:e + 18] = b.command
    return bytes(out)
