"""Check that a bottom-up run is the exact mirror of a top-down one.

Renders the board both ways with the plugin's own code, then asserts on the
g-code text -- the thing that actually reaches the machine:

  * twist drill: every line identical except X, which is negated
  * end mill: hole centres mirrored, helix identical apart from X (G02 stays G02)
  * outline: same points under X negation, same winding (so the same climb /
    conventional cut), same Z and tab sequence
  * output name carries the _bottom suffix

Needs pcbnew, so run it with KiCad's bundled Python:

  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \\
    scripts/check_mirror.py path/to/board.kicad_pcb

Exits non-zero on the first run that fails any check.
"""
import importlib.util
import os
import re
import sys

PLUGINS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins")
WORD = re.compile(r"([GMXYZIJFST])(-?\d+(?:\.\d+)?)")
HOLE = re.compile(r"hole at X(-?[\d.]+) Y(-?[\d.]+)")
OUTLINE_MARK = "-- outline cutout (LAST"
TOL = 1e-6

import pcbnew  # noqa: E402  (KiCad's Python only)


def load_plugin():
    """Import plugins/ as a package without KiCad's plugin loader."""
    spec = importlib.util.spec_from_file_location(
        "gcode_plugin", os.path.join(PLUGINS, "__init__.py"),
        submodule_search_locations=[PLUGINS])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gcode_plugin"] = mod
    spec.loader.exec_module(mod)
    from gcode_plugin import holes, program
    return holes, program


def code(lines):
    return [l for l in lines if l.strip() and not l.lstrip().startswith("(")]


def words(line):
    return {k: float(v) for k, v in WORD.findall(line)}


def split(text):
    """(holes part, outline part) of a program, split at the cutter's comment."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if OUTLINE_MARK in l:
            return lines[:i], lines[i:]
    return lines, []


def same_except_x(top, bot, negate_x):
    """Line-by-line: same G-code and words; X negated or merely allowed to differ."""
    if len(top) != len(bot):
        return "line counts differ: %d vs %d" % (len(top), len(bot))
    for n, (lt, lb) in enumerate(zip(top, bot), 1):
        wt, wb = words(lt), words(lb)
        if lt.split()[0] != lb.split()[0] or set(wt) != set(wb):
            return "line %d differs: %r vs %r" % (n, lt, lb)
        for k in wt:
            if k == "X" and not negate_x:
                continue
            want = -wt[k] if k == "X" else wt[k]
            if abs(want - wb[k]) > TOL:
                return "line %d, %s: expected %g, got %g" % (n, k, want, wb[k])
    return None


def xy_points(lines):
    pts = []
    for l in code(lines):
        w = words(l)
        if l.startswith(("G00", "G01")) and "X" in w and "Y" in w:
            pts.append((round(w["X"], 3), round(w["Y"], 3)))
    return pts


def first_ring(pts):
    """Points of the first closed pass: up to the first return to the start."""
    for i, p in enumerate(pts[1:], 1):
        if p == pts[0]:
            return pts[:i]
    return pts


def signed_area(pts):
    return sum(x0 * y1 - x1 * y0
               for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])) / 2.0


def z_moves(lines):
    return [words(l)["Z"] for l in code(lines) if l.startswith("G01 Z")]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    holes, program = load_plugin()
    board = pcbnew.LoadBoard(sys.argv[1])
    smallest = min(h.dia for h in holes.collect(board))
    base = dict(outdir="/tmp")
    runs = {
        "end mill": dict(base, hole_dia=smallest),
        "twist drill": dict(base, bit_type=holes.DRILL, cut_tool=50),
        "outline only": dict(base, do_drill=False),
    }
    failures = []

    def check(ok, msg):
        print(("  ok    " if ok is None else "  FAIL  ") + msg
              + ("" if ok is None else ": " + ok))
        if ok is not None:
            failures.append(msg)

    for name, kw in runs.items():
        print(name)
        top_opt = program.Options(**kw)
        bot_opt = program.Options(mirror_x=True, **kw)
        top = program.build(board, top_opt)
        bot = program.build(board, bot_opt)
        top_holes, top_cut = split(top)
        bot_holes, bot_cut = split(bot)

        suffix = os.path.basename(program.output_path(board, bot_opt))
        check(None if "_bottom" in suffix else "no _bottom in %s" % suffix,
              "output name %s" % suffix)

        if kw.get("do_drill", True):
            ct = [tuple(map(float, m.groups())) for m in map(HOLE.search, top_holes) if m]
            cb = [tuple(map(float, m.groups())) for m in map(HOLE.search, bot_holes) if m]
            bad = [(a, b) for a, b in zip(ct, cb)
                   if abs(a[0] + b[0]) > 1e-3 or abs(a[1] - b[1]) > 1e-3]
            check(None if len(ct) == len(cb) and ct and not bad
                  else "%d vs %d holes, first mismatch %s" % (len(ct), len(cb), bad[:1]),
                  "%d hole centres: X negated, Y equal" % len(ct))
            drill = kw.get("bit_type") == holes.DRILL
            check(same_except_x(code(top_holes), code(bot_holes), negate_x=drill),
                  "hole toolpath (%d lines): %s" % (
                      len(code(top_holes)),
                      "every X negated, all else identical" if drill
                      else "helix identical apart from X"))

        pt, pb = xy_points(top_cut), xy_points(bot_cut)
        check(None if sorted((-x, y) for x, y in pt) == sorted(pb) else "point sets differ",
              "outline (%d points): set-equal under X negation" % len(pt))
        at, ab = signed_area(first_ring(pt)), signed_area(first_ring(pb))
        check(None if at * ab > 0 and abs(at - ab) < 0.5
              else "signed area %.1f vs %.1f" % (at, ab),
              "outline winding: signed area %.1f both ways (same cut direction)" % at)
        check(None if z_moves(top_cut) == z_moves(bot_cut) else "Z sequences differ",
              "outline Z and tab steps identical (%d moves)" % len(z_moves(top_cut)))

    print("\n%s" % ("ALL PASS" if not failures else "%d FAILED" % len(failures)))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
