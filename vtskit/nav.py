"""The navigation pack's two packets, PCI and DSI, as bytes.

Layouts from https://dvdnav.mplayerhq.hu/dvdinfo/pci_pkt.html and
dsi_pkt.html. Offsets here are within a packet's data, after its substream
id (the PCI's data is 979 bytes, the DSI's 1017). What those pages leave
open was settled by looking at the bytes dvdauthor writes for the same
stream (tests/test_vob.py checks every byte against it):

* the VOBU search table (VOBU_SRI) is 42 pointers from 0xEA to 0x191:
  the next VOBU with video, 19 forward pointers (far to near: 120, 60, 30,
  10, 7.5, 7, 6.5, 6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5 s, the
  spans the page lists), the next VOBU, the previous VOBU, 19 backward
  pointers (near to far) and the previous VOBU with video; the page's own
  offsets (the table starts at 0xEA, SYNCI at 0x192) leave room for exactly
  that, and its labels drift by one entry;
* a span's pointer is the VOBU that is playing at the VOBU's start time
  plus (minus) the span, within the cell. Bit 30 ("VOBUs lie between this
  pointer and the next shorter span's") is set only on the 10, 30, 60 and
  120 s pointers, the ones more than half a second apart from the next
  shorter span, whatever the VOBUs' lengths. When no VOBU is that far, the
  pointer is 0x7FFFFFFF on those four when the next shorter span's pointer
  is valid, else 0x3FFFFFFF;
* the cell elapsed time counts frames at the frame rate, written as
  hh:mm:ss:ff in BCD with 30 or 25 frames to the second;
* the audio sync pointer (A_SYNCA) is the audio packet whose PTS is the
  last at or before the VOBU's start time.
"""

import struct

from .errors import VtsError

PCI_SIZE = 0x3D4 - 1
DSI_SIZE = 0x3FA - 1

# the spans of the search table, in 90 kHz ticks, far to near
SRI_SPANS = [int(s * 90000) for s in
             (120, 60, 30, 10, 7.5, 7, 6.5, 6, 5.5, 5, 4.5, 4, 3.5, 3, 2.5, 2, 1.5, 1, 0.5)]
SRI_NONE = 0x3FFFFFFF        # no VOBU that far in the cell
SRI_NONE_BEYOND = 0x7FFFFFFF  # none that far, but VOBUs beyond the nearer pointer
SRI_NO_VIDEO = 0xBFFFFFFF    # no VOBU with video that way
SRI_VALID = 0x80000000
SRI_SKIPPED = 0x40000000     # VOBUs lie between this pointer and the nearer one

# frame_rate_code -> (frames per second written in the time code, flag bits)
TIME_CODE_RATE = {1: (30, 0xC0), 4: (30, 0xC0), 3: (25, 0x40)}


def time_code(frames, frame_rate_code):
    """hh:mm:ss:ff in BCD with the frame rate flag, from a frame count."""
    try:
        fps, flag = TIME_CODE_RATE[frame_rate_code]
    except KeyError:
        raise VtsError(f"frame rate code {frame_rate_code} is not a DVD frame rate") from None
    if frames < 0:
        raise VtsError("a negative time")
    ff, s = frames % fps, frames // fps
    ss, m = s % 60, s // 60
    mm, hh = m % 60, m // 60
    if hh > 99:
        raise VtsError("a time of 100 hours or more")
    return bytes([_bcd(hh), _bcd(mm), _bcd(ss), _bcd(ff) | flag])


def _bcd(n):
    return (n // 10) << 4 | n % 10


def pci(lbn, s_ptm, e_ptm, eltm, uop_ctl=0, category=0, se_e_ptm=0):
    """The PCI data of a VOBU without buttons: PCI_GI (the pack's sector,
    the VOBU's start and end presentation times, the cell elapsed time);
    no angles (NSML_AGLI), no highlights (HLI), no recording information."""
    out = bytearray(PCI_SIZE)
    struct.pack_into(">IHxxIIII", out, 0, lbn, category, uop_ctl, s_ptm, e_ptm, se_e_ptm)
    out[0x18:0x1C] = eltm
    return bytes(out)


def dsi(scr, lbn, vobu_ea, ref_ea, vob_id, cell_id, eltm, vob_s_ptm, vob_e_ptm,
        sri, a_synca, sp_synca=None):
    """The DSI data of a VOBU that is not interleaved: DSI_GI, SML_PBI with
    the VOB's first and last presentation times and no angles or gaps, the
    search table `sri` (42 pointers), SYNCI from `a_synca` (8 audio
    pointers) and `sp_synca` (32 subpicture pointers, 0 when absent)."""
    if len(ref_ea) != 3 or len(sri) != 42 or len(a_synca) != 8:
        raise VtsError("a DSI needs 3 reference ends, 42 search pointers, 8 audio pointers")
    out = bytearray(DSI_SIZE)
    struct.pack_into(">IIIIIIHxB", out, 0, scr, lbn, vobu_ea, *ref_ea, vob_id, cell_id)
    out[0x1C:0x20] = eltm
    struct.pack_into(">II", out, 0x2C, vob_s_ptm, vob_e_ptm)      # SML_PBI
    struct.pack_into(">42I", out, 0xEA, *sri)                      # VOBU_SRI
    struct.pack_into(">8H", out, 0x192, *a_synca)                  # SYNCI
    if sp_synca:
        struct.pack_into(">32I", out, 0x1A2, *sp_synca)
    return bytes(out)


def parse_pci(data):
    """The fields of a PCI's data, by name (for tests and tools)."""
    if len(data) != PCI_SIZE:
        raise VtsError(f"a PCI's data is {PCI_SIZE} bytes, not {len(data)}")
    lbn, cat, uop, s, e, se = struct.unpack_from(">IHxxIIII", data, 0)
    return {"nv_pck_lbn": lbn, "vobu_cat": cat, "vobu_uop_ctl": uop, "vobu_s_ptm": s,
            "vobu_e_ptm": e, "vobu_se_e_ptm": se, "c_eltm": data[0x18:0x1C].hex(),
            "vobu_isrc": data[0x1C:0x3C].hex(),
            "nsml_agli": list(struct.unpack_from(">9I", data, 0x3C)),
            "hli": data[0x60:0x316].hex(), "reci": data[0x316:].hex()}


def parse_dsi(data):
    if len(data) != DSI_SIZE:
        raise VtsError(f"a DSI's data is {DSI_SIZE} bytes, not {len(data)}")
    scr, lbn, ea, r1, r2, r3, vob, cell = struct.unpack_from(">IIIIIIHxB", data, 0)
    sri = list(struct.unpack_from(">42I", data, 0xEA))
    return {"nv_pck_scr": scr, "nv_pck_lbn": lbn, "vobu_ea": ea, "vobu_ref_ea": [r1, r2, r3],
            "vobu_vob_idn": vob, "vobu_c_idn": cell, "c_eltm": data[0x1C:0x20].hex(),
            "sml_pbi": data[0x20:0xB4].hex(), "sml_agli": data[0xB4:0xEA].hex(),
            "sri_nvwv": sri[0], "sri_fwd": sri[1:20], "sri_nv": sri[20], "sri_pv": sri[21],
            "sri_bwd": sri[22:41], "sri_pvwv": sri[41],
            "a_synca": list(struct.unpack_from(">8H", data, 0x192)),
            "sp_synca": list(struct.unpack_from(">32I", data, 0x1A2)),
            "reserved": data[0x222:].hex()}
