#!/usr/bin/env python3
"""Generate Camayoc's documentation diagrams as SVG.

Usage: generate-doc-diagrams.py OUTPUT_DIR
Style follows the Quipu README diagrams: dark card, gold and pink accents.
"""
import sys
from html import escape
from pathlib import Path

BG, CARD, EDGE = "#12121f", "#20203a", "#2e2e4d"
INK, BODY, GOLD, PINK, CORAL, DIM = "#ecebf5", "#a7a6c2", "#ffc24d", "#ff4fa0", "#ff8a70", "#6f6e8f"

STYLE = """
    .t{font-family:Inter,Segoe UI,Helvetica,Arial,sans-serif}
    .m{font-family:JetBrains Mono,Menlo,Consolas,monospace}
    .card{fill:#20203a;stroke:#2e2e4d}
    .pipe{stroke:#ff8a70;stroke-width:2.5;fill:none;stroke-dasharray:6 6;opacity:.75;animation:flow 1.2s linear infinite}
    .up{stroke:#ffc24d;stroke-width:2;fill:none;stroke-dasharray:4 5;opacity:.8;animation:flow 1.6s linear infinite}
    .down{stroke:#ff4fa0;stroke-width:2;fill:none;stroke-dasharray:4 5;opacity:.8;animation:flow 1.6s linear infinite reverse}
    @keyframes flow{to{stroke-dashoffset:-24}}
    @media (prefers-reduced-motion: reduce){.pipe,.up,.down{animation:none}}
"""


def wrap(text, width, size, mono=False):
    per = size * (0.6 if mono else 0.53)
    limit = max(4, int(width / per))
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > limit:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return lines


class Svg:
    def __init__(self, w, h, label):
        self.w, self.h, self.label, self.out = w, h, label, []

    def add(self, s):
        self.out.append(s)

    def text(self, x, y, s, size=13, fill=BODY, weight=None, cls="t", anchor=None, spacing=None, italic=False):
        attrs = f'class="{cls}" x="{x}" y="{y}" fill="{fill}" font-size="{size}"'
        if weight:
            attrs += f' font-weight="{weight}"'
        if anchor:
            attrs += f' text-anchor="{anchor}"'
        if spacing:
            attrs += f' letter-spacing="{spacing}"'
        if italic:
            attrs += ' font-style="italic"'
        self.add(f"  <text {attrs}>{escape(s)}</text>")

    def para(self, x, y, s, width, size=12.5, fill=BODY, lead=None, cls="t", weight=None, anchor=None):
        lead = lead or round(size * 1.38, 1)
        for n, line in enumerate(wrap(s, width, size, cls == "m")):
            self.text(x, round(y + n * lead, 1), line, size, fill, weight, cls, anchor)
        return y + len(wrap(s, width, size, cls == "m")) * lead

    def card(self, x, y, w, h, stroke=EDGE, fill=CARD, dash=None, rx=14, sw=1.2):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def path(self, d, cls):
        self.add(f'  <path class="{cls}" d="{d}"/>')

    def head(self, x, y, direction, fill):
        pts = {"right": f"{x},{y} {x-9},{y-5} {x-9},{y+5}", "left": f"{x},{y} {x+9},{y-5} {x+9},{y+5}",
               "up": f"{x},{y} {x-5},{y+9} {x+5},{y+9}", "down": f"{x},{y} {x-5},{y-9} {x+5},{y-9}"}[direction]
        self.add(f'  <polygon points="{pts}" fill="{fill}"/>')

    def render(self):
        body = "\n".join(self.out)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" role="img" '
                f'aria-label="{escape(self.label, quote=True)}">\n  <style>{STYLE}  </style>\n'
                f'  <rect width="{self.w}" height="{self.h}" rx="20" fill="{BG}"/>\n{body}\n</svg>\n')


def governed_memory():
    s = Svg(960, 560, (
        "How a fact gets into governed memory. On the left, three things arrive: a ruling or a handoff through "
        "cy record, a delivery event through an ingress adapter, and text an agent read through extraction. "
        "Camayoc checks each one in three steps. Map: it is put onto the shared vocabulary, and an unknown name "
        "or event type stops here. Tag: it must say how it was learned, which is declared, observed or "
        "inferred. Write: it goes through Quipu's shapes, and a record that breaks one is refused. Quipu keeps "
        "each kind in its own graph: declared for what a person said, observed for what a tool measured, and "
        "inferred for what an agent concluded, which ranks lowest and never overrides declared. cy recall, an agent's session "
        "start and stored queries read the record back, and each answer says how the fact was learned."))
    s.text(40, 52, "How a fact gets into governed memory", 24, INK, 700)
    s.text(40, 78, "Camayoc checks a record before Quipu stores it. Each fact keeps how it was learned.", 14)

    s.text(30, 122, "WHAT COMES IN", 10, GOLD, 700, spacing="1")
    s.text(290, 122, "CAMAYOC CHECKS IT", 10, GOLD, 700, spacing="1")
    s.text(650, 122, "QUIPU KEEPS IT", 10, GOLD, 700, spacing="1")

    arrivals = [
        ("A ruling or a handoff", "cy record", "From a person or an agent, at the moment it happens."),
        ("A delivery event", "ingress adapters", "A deployment, a change or a review, as CDEvents."),
        ("Text an agent read", "extraction", "Mentions found in prose. Always inferred."),
    ]
    for n, (what, cmd, cap) in enumerate(arrivals):
        y = 136 + n * 128
        s.card(30, y, 220, 110)
        s.text(46, y + 28, what, 15, INK, 700)
        s.text(46, y + 50, cmd, 12, PINK, cls="m")
        s.para(46, y + 74, cap, 196, 12.5)
        s.path(f"M252 {y + 55} H280", "pipe")
        s.head(288, y + 55, "right", CORAL)

    s.card(290, 136, 260, 366, stroke=PINK, sw=1.6)
    steps = [
        ("1", "Map", "Onto the shared vocabulary. An unknown name or event type stops here."),
        ("2", "Tag", "How it was learned: declared, observed or inferred."),
        ("3", "Write", "Through Quipu's shapes. A record that breaks one is refused."),
    ]
    for n, (num, title, cap) in enumerate(steps):
        y = 152 + n * 116
        s.card(306, y, 228, 102, fill=BG, rx=10)
        s.text(322, y + 28, num, 13, GOLD, 700, cls="m")
        s.text(342, y + 28, title, 16, INK, 700)
        s.para(322, y + 50, cap, 200, 12.5)
        if n < 2:
            s.path(f"M420 {y + 104} V{y + 108}", "pipe")
            s.head(420, y + 115, "down", CORAL)

    s.card(650, 136, 280, 250)
    rows = [
        ("Declared", "A person said it."),
        ("Observed", "A tool measured it."),
        ("Inferred", "An agent concluded it. Ranks lowest."),
    ]
    for n, (title, cap) in enumerate(rows):
        y = 152 + n * 76
        s.card(666, y, 248, 62, fill=BG, rx=10)
        s.text(682, y + 25, title, 14, INK, 700)
        s.text(682, y + 46, cap, 12)

    s.path("M552 260 H640", "pipe")
    s.head(648, 260, "right", CORAL)
    s.text(600, 250, "admit", 12, CORAL, cls="m", anchor="middle")

    s.path("M790 388 V402", "up")
    s.head(790, 410, "down", GOLD)
    s.card(650, 412, 280, 90)
    s.text(666, 436, "WHO READS IT BACK", 10, GOLD, 700, spacing="1")
    s.para(666, 458, "cy recall, an agent's session start and stored queries. Each answer says how the fact was learned.",
           252, 12.5)
    s.text(40, 536, "A refused record is reported with the field it lacks. It is never stored quietly.", 12.5, DIM)
    return s.render()


DIAGRAMS = {"governed-memory.svg": governed_memory}

if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for name, build in DIAGRAMS.items():
        (out / name).write_text(build())
        print(f"wrote {out / name}")
