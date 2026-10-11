"""Drive a DVD image with libdvdnav (via ctypes) to check menu behaviour.

libdvdnav is the navigation engine VLC (and others) use to run DVD menus, so
"select button N, activate, see which title/chapter starts" through it is a
faithful automated stand-in for pressing buttons on a remote.

Only used by tests; skipped when libdvdnav is not installed. Only the
public API is used: the function declarations of dvdnav.h.

(From oncemore's tests, by the same author, relicensed under
LGPL-3.0-or-later for vtskit; the angle calls are new here.)
"""

import ctypes
import ctypes.util
import os
import sys

DVDNAV_STATUS_OK = 1

EV_BLOCK_OK = 0
EV_NOP = 1
EV_STILL_FRAME = 2
EV_VTS_CHANGE = 5
EV_CELL_CHANGE = 6
EV_NAV_PACKET = 7
EV_STOP = 8
EV_HIGHLIGHT = 9
EV_WAIT = 13

# dvdnav_audio_stream_format (DVD_AUDIO_FORMAT_*)
AUDIO_AC3, AUDIO_LPCM, AUDIO_DTS = 0, 4, 6

_CANDIDATES = (
    "libdvdnav.so.4", "libdvdnav.so", "libdvdnav.dylib", "libdvdnav.4.dylib",
    "/opt/homebrew/lib/libdvdnav.dylib", "/usr/local/lib/libdvdnav.dylib",
)


def load_library():
    names = []
    found = ctypes.util.find_library("dvdnav")
    if found:
        names.append(found)
    names.extend(_CANDIDATES)
    for n in names:
        try:
            lib = ctypes.CDLL(n)
        except OSError:
            continue
        _declare(lib)
        return lib
    return None


def _declare(lib):
    P = ctypes.c_void_p
    i32p = ctypes.POINTER(ctypes.c_int32)
    sigs = {
        "dvdnav_open": (ctypes.c_int, [ctypes.POINTER(P), ctypes.c_char_p]),
        "dvdnav_close": (ctypes.c_int, [P]),
        "dvdnav_err_to_string": (ctypes.c_char_p, [P]),
        "dvdnav_get_next_block": (ctypes.c_int, [P, ctypes.c_char_p, i32p, i32p]),
        "dvdnav_still_skip": (ctypes.c_int, [P]),
        "dvdnav_wait_skip": (ctypes.c_int, [P]),
        "dvdnav_current_title_info": (ctypes.c_int, [P, i32p, i32p]),
        "dvdnav_current_title_program": (ctypes.c_int, [P, i32p, i32p, i32p]),
        "dvdnav_get_current_nav_dsi": (ctypes.c_void_p, [P]),
        "dvdnav_get_current_nav_pci": (P, [P]),
        "dvdnav_button_select": (ctypes.c_int, [P, P, ctypes.c_int32]),
        "dvdnav_button_activate": (ctypes.c_int, [P, P]),
        "dvdnav_get_current_highlight": (ctypes.c_int, [P, i32p]),
        "dvdnav_upper_button_select": (ctypes.c_int, [P, P]),
        "dvdnav_lower_button_select": (ctypes.c_int, [P, P]),
        "dvdnav_left_button_select": (ctypes.c_int, [P, P]),
        "dvdnav_right_button_select": (ctypes.c_int, [P, P]),
        "dvdnav_is_domain_vts": (ctypes.c_int8, [P]),
        "dvdnav_is_domain_vtsm": (ctypes.c_int8, [P]),
        "dvdnav_is_domain_vmgm": (ctypes.c_int8, [P]),
        "dvdnav_is_domain_fp": (ctypes.c_int8, [P]),
        "dvdnav_get_number_of_titles": (ctypes.c_int, [P, i32p]),
        "dvdnav_get_number_of_parts": (ctypes.c_int, [P, ctypes.c_int32, i32p]),
        "dvdnav_get_active_audio_stream": (ctypes.c_int8, [P]),
        "dvdnav_get_active_spu_stream": (ctypes.c_int8, [P]),
        "dvdnav_audio_stream_to_lang": (ctypes.c_uint16, [P, ctypes.c_uint8]),
        "dvdnav_audio_stream_channels": (ctypes.c_uint16, [P, ctypes.c_uint8]),
        "dvdnav_audio_stream_format": (ctypes.c_uint16, [P, ctypes.c_uint8]),
        "dvdnav_spu_stream_to_lang": (ctypes.c_uint16, [P, ctypes.c_uint8]),
        "dvdnav_get_title_string": (ctypes.c_int, [P, ctypes.POINTER(ctypes.c_char_p)]),
        "dvdnav_menu_call": (ctypes.c_int, [P, ctypes.c_int]),
        "dvdnav_next_pg_search": (ctypes.c_int, [P]),
        "dvdnav_prev_pg_search": (ctypes.c_int, [P]),
        "dvdnav_get_restrictions": (ctypes.c_uint32, [P]),
        "dvdnav_get_number_of_angles": (ctypes.c_int, [P, ctypes.c_int32, i32p]),
        "dvdnav_get_angle_info": (ctypes.c_int, [P, i32p, i32p]),
        "dvdnav_angle_change": (ctypes.c_int, [P, ctypes.c_int32]),
    }
    for name, (res, args) in sigs.items():
        fn = getattr(lib, name)
        fn.restype = res
        fn.argtypes = args


class NavError(RuntimeError):
    pass


class DvdSim:
    def __init__(self, path, lib=None):
        self.lib = lib or load_library()
        if self.lib is None:
            raise NavError("libdvdnav not available")
        self.h = ctypes.c_void_p()
        if self.lib.dvdnav_open(ctypes.byref(self.h), os.fsencode(path)) != DVDNAV_STATUS_OK:
            raise NavError(f"dvdnav_open failed for {path}")
        self.buf = ctypes.create_string_buffer(2048 * 2)
        self.stopped = False
        self.events = 0
        self.cells = []        # (domain, pgcn, cell) at every cell change
        self.stills = []       # (domain, pgcn, cell, length) at every still (255 = held)

    def close(self):
        if self.h:
            self.lib.dvdnav_close(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _err(self):
        return (self.lib.dvdnav_err_to_string(self.h) or b"").decode("utf-8", "replace")

    def step(self):
        ev = ctypes.c_int32()
        ln = ctypes.c_int32()
        if self.lib.dvdnav_get_next_block(self.h, self.buf, ctypes.byref(ev),
                                          ctypes.byref(ln)) != DVDNAV_STATUS_OK:
            raise NavError("get_next_block: " + self._err())
        self.events += 1
        e = ev.value
        if e == EV_STILL_FRAME:
            # (the player's Next / Play: on at once) length 255 = held until then
            length = int.from_bytes(self.buf.raw[:4], "little", signed=True)
            self.stills.append((self.domain(), self.pgcn(), self.cells[-1][2] if self.cells
                                else None, length))
            self.lib.dvdnav_still_skip(self.h)
        elif e == EV_WAIT:
            self.lib.dvdnav_wait_skip(self.h)
        elif e == EV_STOP:
            self.stopped = True
        elif e == EV_CELL_CHANGE:
            cell = int.from_bytes(self.buf.raw[:4], "little", signed=True)
            self.cells.append((self.domain(), self.pgcn(), cell))
        return e

    # -- state ---------------------------------------------------------------
    def domain(self):
        if self.lib.dvdnav_is_domain_vts(self.h):
            return "title"
        if self.lib.dvdnav_is_domain_vtsm(self.h):
            return "vtsm"
        if self.lib.dvdnav_is_domain_vmgm(self.h):
            return "vmgm"
        if self.lib.dvdnav_is_domain_fp(self.h):
            return "fp"
        return "?"

    def title_part(self):
        t, p = ctypes.c_int32(), ctypes.c_int32()
        self.lib.dvdnav_current_title_info(self.h, ctypes.byref(t), ctypes.byref(p))
        return t.value, p.value

    def pgcn(self):
        t, pgc, pg = ctypes.c_int32(), ctypes.c_int32(), ctypes.c_int32()
        if self.lib.dvdnav_current_title_program(self.h, ctypes.byref(t), ctypes.byref(pgc),
                                                 ctypes.byref(pg)) != DVDNAV_STATUS_OK:
            return None
        return pgc.value

    def restrictions(self):
        """The user operations (UOP numbers 0-24) the current program chain
        and VOBU prohibit. (libdvdnav returns its user_ops_t bit-field
        struct, whose first-declared field is the lowest bit on a
        little-endian machine: UOP n is bit 31 - n there.)"""
        v = self.lib.dvdnav_get_restrictions(self.h)
        if sys.byteorder == "little":
            return {n for n in range(25) if v >> (31 - n) & 1}
        return {n for n in range(25) if v >> n & 1}

    def highlight(self):
        b = ctypes.c_int32()
        self.lib.dvdnav_get_current_highlight(self.h, ctypes.byref(b))
        return b.value

    def num_titles(self):
        n = ctypes.c_int32()
        self.lib.dvdnav_get_number_of_titles(self.h, ctypes.byref(n))
        return n.value

    def num_parts(self, title):
        n = ctypes.c_int32()
        self.lib.dvdnav_get_number_of_parts(self.h, title, ctypes.byref(n))
        return n.value

    def vob_cell(self):
        """(VOB number, cell number) of the current navigation pack, from its
        DSI (dsi_t: the general information first: SCR, LBN, four end
        addresses, then VOB_IDN at 24 and C_IDN at 27, in the host's order)."""
        dsi = self.lib.dvdnav_get_current_nav_dsi(self.h)
        if not dsi:
            return None
        raw = ctypes.string_at(dsi, 28)
        return int.from_bytes(raw[24:26], sys.byteorder), raw[27]

    def num_angles(self, title):
        """The angles of a title, as the player counts them."""
        n = ctypes.c_int32()
        if self.lib.dvdnav_get_number_of_angles(self.h, title, ctypes.byref(n)) \
                != DVDNAV_STATUS_OK:
            raise NavError("get_number_of_angles: " + self._err())
        return n.value

    def angle(self):
        """(current angle, angles) where playback is."""
        cur, n = ctypes.c_int32(), ctypes.c_int32()
        if self.lib.dvdnav_get_angle_info(self.h, ctypes.byref(cur), ctypes.byref(n)) \
                != DVDNAV_STATUS_OK:
            raise NavError("get_angle_info: " + self._err())
        return cur.value, n.value

    def change_angle(self, angle):
        """The remote's Angle key: False when the player refuses."""
        return self.lib.dvdnav_angle_change(self.h, angle) == DVDNAV_STATUS_OK

    def active_audio(self):
        return self.lib.dvdnav_get_active_audio_stream(self.h)

    def active_subtitle(self):
        """(stream, shown): physical subpicture stream and whether it is displayed."""
        v = self.lib.dvdnav_get_active_spu_stream(self.h) & 0xFF
        return (v & 0x7F, not v & 0x80)

    @staticmethod
    def _lang(code):
        return None if code in (0, 0xFFFF) else chr(code >> 8) + chr(code & 0xFF)

    def audio_lang(self, stream):
        return self._lang(self.lib.dvdnav_audio_stream_to_lang(self.h, stream))

    def audio_channels(self, stream):
        return self.lib.dvdnav_audio_stream_channels(self.h, stream)

    def audio_format(self, stream):
        """DVD_AUDIO_FORMAT_*: AUDIO_AC3, AUDIO_LPCM, AUDIO_DTS ..."""
        return self.lib.dvdnav_audio_stream_format(self.h, stream)

    def subtitle_lang(self, stream):
        return self._lang(self.lib.dvdnav_spu_stream_to_lang(self.h, stream))

    # -- driving ---------------------------------------------------------------
    def run_until(self, pred, max_events=2_000_000):
        for _ in range(max_events):
            e = self.step()
            if pred(e):
                return e
            if self.stopped:
                raise NavError("playback stopped before condition was met")
        raise NavError("condition not met within event budget")

    def wait_menu(self, max_events=200_000):
        """Advance until a menu NAV packet (buttons available) is current."""
        def ready(e):
            return e == EV_NAV_PACKET and self.domain() in ("vtsm", "vmgm") and \
                self.button_count() > 0
        self.run_until(ready, max_events)
        return self.pgcn()

    def wait_title(self, max_events=200_000):
        self.run_until(lambda e: e == EV_NAV_PACKET and self.domain() == "title", max_events)
        return self.title_part()

    def button_count(self):
        pci = self.lib.dvdnav_get_current_nav_pci(self.h)
        if not pci:
            return 0
        cur = self.highlight()
        n = 0
        for i in range(1, 37):
            if self.lib.dvdnav_button_select(self.h, pci, i) != DVDNAV_STATUS_OK:
                break
            n = i
        if cur > 0 and n:
            self.lib.dvdnav_button_select(self.h, pci, cur)
        return n

    def button_numbers(self):
        """(button offset number, numerically selectable buttons) of the
        current navigation pack's highlight (pci_t: general 60 bytes, angle
        info 36, then the highlight's general info: BTN_OFN at 16, NSL_BTN_NS
        at 18)."""
        pci = self.lib.dvdnav_get_current_nav_pci(self.h)
        raw = ctypes.string_at(pci, 116)
        return raw[112], raw[114]

    def select(self, button):
        pci = self.lib.dvdnav_get_current_nav_pci(self.h)
        if self.lib.dvdnav_button_select(self.h, pci, button) != DVDNAV_STATUS_OK:
            raise NavError(f"cannot select button {button}: " + self._err())

    def move(self, direction):
        pci = self.lib.dvdnav_get_current_nav_pci(self.h)
        fn = {"up": self.lib.dvdnav_upper_button_select,
              "down": self.lib.dvdnav_lower_button_select,
              "left": self.lib.dvdnav_left_button_select,
              "right": self.lib.dvdnav_right_button_select}[direction]
        fn(self.h, pci)
        return self.highlight()

    def activate(self, button=None):
        if button is not None:
            self.select(button)
        pci = self.lib.dvdnav_get_current_nav_pci(self.h)
        if self.lib.dvdnav_button_activate(self.h, pci) != DVDNAV_STATUS_OK:
            raise NavError("button_activate failed: " + self._err())

    def trace_until_menu(self, max_events=4_000_000):
        """Play on; return every (title, chapter) seen until a menu shows,
        and that menu's PGC number."""
        seen = []

        def step(e):
            if e != EV_NAV_PACKET:
                return False
            if self.domain() == "title":
                tp = self.title_part()
                if not seen or seen[-1] != tp:
                    seen.append(tp)
                return False
            return self.domain() in ("vtsm", "vmgm") and self.button_count() > 0
        self.run_until(step, max_events)
        return seen, self.pgcn()

    MENU_TITLE, MENU_ROOT = 2, 3

    def menu_key(self, which=None):
        """Press the remote's Menu key (or Title key with MENU_TITLE)."""
        if self.lib.dvdnav_menu_call(self.h, which or self.MENU_ROOT) != DVDNAV_STATUS_OK:
            raise NavError("menu_call failed: " + self._err())

    def title_key(self):
        self.menu_key(self.MENU_TITLE)

    def next_chapter(self):
        """The remote's Next key (returns False when the player refuses)."""
        return self.lib.dvdnav_next_pg_search(self.h) == DVDNAV_STATUS_OK

    def prev_chapter(self):
        return self.lib.dvdnav_prev_pg_search(self.h) == DVDNAV_STATUS_OK

    def play_to_end_of_title(self, max_events=2_000_000):
        """Play until the current title ends (another title starts or a menu
        shows up). Returns ("title", (title, part)) or ("menu", pgcn)."""
        start = self.title_part()[0]

        def changed(e):
            if e != EV_NAV_PACKET:
                return False
            if self.domain() == "title":
                return self.title_part()[0] != start
            return self.domain() in ("vtsm", "vmgm") and self.button_count() > 0
        self.run_until(changed, max_events)
        if self.domain() == "title":
            return ("title", self.title_part())
        return ("menu", self.pgcn())
