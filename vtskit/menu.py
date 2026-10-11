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

From dvdauthor's output: the HLI is repeated whole in every VOBU
(HLI_SS 1), its start time is the menu's first picture, its end times are
0xFFFFFFFF (never), the buttons form one group of the normal kind, every
button uses colour table 1, and in that table only the pattern code (1) is
coloured: palette 0 and opaque when selected, palette 1 and opaque when
activated.
"""

import struct
from dataclasses import dataclass, field

from . import spu
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
    for n, b in enumerate(menu.buttons, 1):
        x0, y0, x1, y1 = b.rect
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise VtsError(f"button {n}: {b.rect} is not inside {width}x{height}")
        if len(b.command) != 8:
            raise VtsError(f"button {n}: a command is 8 bytes")
        for k in (b.up, b.down, b.left, b.right):
            if not 0 <= k <= len(menu.buttons):
                raise VtsError(f"button {n} points at button {k}, which the menu has not")
    if not 0 <= menu.first_button <= len(menu.buttons):
        raise VtsError(f"first button {menu.first_button}: the menu has {len(menu.buttons)}")
    if not 0 <= menu.button_offset <= 255:
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
    buttons = menu.buttons
    out = bytearray(0x316 - 0x60)
    if not buttons:
        return bytes(out)
    struct.pack_into(">HIIIHBBBBBB", out, 0, 1, s_ptm, 0xFFFFFFFF, 0xFFFFFFFF, 0x1000,
                     menu.button_offset, len(buttons), len(buttons), 0, menu.first_button, 0)
    # colour table 1: the pattern code, the highlight's palette entry and
    # contrast when selected, the selection's when activated; the other
    # codes stay as they are
    first = 1 if menu.normal is not None else 0
    out[0x16:0x1E] = bytes((0x00, first << 4, 0x00, _colour(menu.highlight, "highlight")[1] << 4,
                            0x00, (first + 1) << 4, 0x00, _colour(menu.select, "selection")[1] << 4))
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
