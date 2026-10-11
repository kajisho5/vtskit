"""Regenerate the vtskit logo files (see docs/DESIGN.md, Brand).

The mark is drawn from geometry: a disc (the ring the oncemore family
shares) with a path that branches in two: the navigation it writes inside. The wordmark is "vtskit" set in Outfit
SemiBold (SIL Open Font License, Google Fonts) and converted to outlines,
so no font is needed to show it. Only this script needs fontTools and the
font file; vtskit itself does not:

    pip install fonttools
    python docs/images/logo/make_logo.py path/to/Outfit-SemiBold.ttf

Writes mark.svg, icon.svg, lockup-dark.svg, wordmark-light.svg and
wordmark-ink.svg next to this script.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "vtskit"
# the brand colours (the oncemore family's): highlight, ink, paper
HIGHLIGHT, INK, PAPER = "#ffd400", "#181b22", "#f2f2f2"


def svg(w, h, body, title, view=None):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view or f"0 0 {w:g} {h:g}"}" '
            f'width="{w:g}" height="{h:g}" role="img" aria-label="{title}"><title>{title}</title>'
            f'{body}</svg>\n')


def ring():
    """The disc every mark of the family is drawn on, on a 100 x 100 grid."""
    return f'<circle cx="50" cy="50" r="34" fill="none" stroke="{HIGHLIGHT}" stroke-width="12"/>'


def motif():
    """A path that branches in two: the navigation it writes."""
    import math
    out = (f'<path d="M33 50 H45" fill="none" stroke="{HIGHLIGHT}" stroke-width="6" '
           f'stroke-linecap="round"/>')
    for ty in (36, 64):
        # a branch from the fork to an arrowhead pointing along it
        x0, y0, x1, y1 = 45, 50, 60, ty + (4 if ty < 50 else -4)
        dx, dy = x1 - x0, y1 - y0
        n = math.hypot(dx, dy)
        ux, uy = dx / n, dy / n
        tip = (x1 + ux * 8, y1 + uy * 8)
        b1 = (x1 - uy * 6.5, y1 + ux * 6.5)
        b2 = (x1 + uy * 6.5, y1 - ux * 6.5)
        out += (f'<path d="M{x0} {y0} L{x1:.1f} {y1:.1f}" fill="none" stroke="{HIGHLIGHT}" '
                f'stroke-width="6" stroke-linecap="round"/>'
                f'<path d="M{tip[0]:.1f} {tip[1]:.1f} L{b1[0]:.1f} {b1[1]:.1f} '
                f'L{b2[0]:.1f} {b2[1]:.1f} Z" fill="{HIGHLIGHT}" stroke="{HIGHLIGHT}" '
                f'stroke-width="2" stroke-linejoin="round"/>')
    return out


def mark():
    return ring() + motif()


def wordmark(font_path, size, x0, baseline, track=-0.01):
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.pens.transformPen import TransformPen
    from fontTools.ttLib import TTFont
    font = TTFont(font_path)
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    s = size / font["head"].unitsPerEm
    x, parts = x0, []
    for ch in NAME:
        g = cmap[ord(ch)]
        pen = SVGPathPen(gs)
        gs[g].draw(TransformPen(pen, (s, 0, 0, -s, x, baseline)))
        parts.append(pen.getCommands())
        x += hmtx[g][0] * s + track * size
    return " ".join(parts), x - track * size - x0


def main(font_path):
    def save(name, text):
        with open(os.path.join(HERE, name), "w") as f:
            f.write(text)
    m = mark()
    save("mark.svg", svg(100, 100, m, NAME))
    tile = f'<rect x="100" y="100" width="824" height="824" rx="185" fill="{INK}"/>'
    save("icon.svg", svg(64, 64, tile + f'<g transform="translate(212 212) scale(6)">{m}</g>',
                         NAME, view="100 100 824 824"))
    wm, width = wordmark(font_path, 64, 112, 66)
    w = round(112 + width + 8)
    save("lockup-dark.svg", svg(w, 100, f'<rect width="{w}" height="100" fill="{INK}"/>' + m +
                                f'<path d="{wm}" fill="{PAPER}"/>', NAME))
    for tone, fill in (("light", PAPER), ("ink", INK)):
        wm, width = wordmark(font_path, 96, 0, 96 * 0.78)
        save(f"wordmark-{tone}.svg",
             svg(round(width), 96, f'<path d="{wm}" fill="{fill}"/>', NAME))


if __name__ == "__main__":
    main(sys.argv[1])
