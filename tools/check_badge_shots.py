"""Read a widget screenshot and say which state the coloured dot is in.

The dot is the only part of the widget that carries state, and a screenshot is
the only proof that survives a browser. This script finds it instead of trusting
a hand-picked coordinate: it scans the bottom-right corner (where the badge is
pinned: `right: 18px; bottom: 18px`) for the pixel closest to each of the three
palette colours, and reports the winner.

The comparison is a distance, not an equality, because an open popup draws its
backdrop over the badge: the same green arrives dimmed. Green stays far from red
either way, so the nearest-colour rule still answers correctly — and it fails
loudly (exit 1) when no pixel is close to any of the three, instead of guessing.

    python tools/check_badge_shots.py docs/proof/*.png --expect ok
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PALETTE = {
    # CSS value -> meaning (see uhb/entrypoints/panel.py, WIDGET_CSS)
    "ok": (0x1F, 0x9D, 0x55),    # .uhb-dot.uhb-ok   — reachable, a usable provider
    "warn": (0xD9, 0x83, 0x24),  # .uhb-dot.uhb-warn — reachable, no key loaded
    "bad": (0xC0, 0x39, 0x2B),   # .uhb-dot.uhb-bad  — unreachable
}
#: The badge lives in this corner; a pixel further than this is not the dot.
CORNER_X, CORNER_Y = 320, 140
#: Beyond this distance the closest palette colour is not credible at all. It is
#: generous on purpose: an open popup draws its backdrop over the badge, and the
#: same red arrives at ~75 from its own CSS value.
MAX_DISTANCE = 100.0
#: The winner must also beat the runner-up by this factor: two plausible palette
#: colours means the screenshot is not a state, it is a coincidence.
MIN_MARGIN = 1.8


def dot_distances(path: Path) -> dict[str, tuple[float, tuple[int, int]]]:
    """Closest pixel to each palette colour in the badge corner, and where it is."""
    from PIL import Image

    image = Image.open(path).convert("RGB")
    width, height = image.size
    pixels = image.load()
    best: dict[str, tuple[float, tuple[int, int]]] = {name: (1e9, (0, 0)) for name in PALETTE}
    for y in range(max(0, height - CORNER_Y), height):
        for x in range(max(0, width - CORNER_X), width):
            red, green, blue = pixels[x, y]
            for name, (pr, pg, pb) in PALETTE.items():
                distance = ((red - pr) ** 2 + (green - pg) ** 2 + (blue - pb) ** 2) ** 0.5
                if distance < best[name][0]:
                    best[name] = (distance, (x, y))
    return best


def classify(path: Path) -> tuple[str, float, float, tuple[int, int]]:
    """``(state, distance, runner_up_distance, where)`` for one screenshot."""
    best = dot_distances(path)
    ranked = sorted((distance, name, where) for name, (distance, where) in best.items())
    distance, state, where = ranked[0]
    runner_up = ranked[1][0]
    return state, round(distance, 1), round(runner_up, 1), where


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shots", nargs="+", type=Path)
    parser.add_argument("--expect", choices=sorted(PALETTE),
                        help="require this state for every screenshot")
    parser.add_argument("--dimmed", action="store_true",
                        help="the popup was open: the backdrop covers the badge, so "
                             "only the nearest colour is required, not the margin")
    args = parser.parse_args(argv)

    failures = 0
    for path in args.shots:
        state, distance, runner_up, where = classify(path)
        if args.dimmed:
            credible = distance <= MAX_DISTANCE
        else:
            credible = distance <= MAX_DISTANCE and runner_up >= distance * MIN_MARGIN
        note = " (dimmed by the open popup)" if args.dimmed else ""
        line = (f"{path.name:<28} dot={state:<4} distance={distance:<5} "
                f"runner-up={runner_up:<6} at={where} [{'ok' if credible else 'INCONCLUSIVE'}]"
                f"{note}")
        if not credible or (args.expect and state != args.expect):
            failures += 1
            print(line + (f"  expected {args.expect}" if args.expect else ""), file=sys.stderr)
        else:
            print(line)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
