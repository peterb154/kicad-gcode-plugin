"""Toolpaths: helically-milled holes and a tabbed outline cut."""
import math

from . import geometry

# Below this much radial room a helix is pointless -- just plunge.
MIN_HELIX_R = 0.025


def order_nearest(holes):
    """Greedy nearest-neighbour travel order. Not optimal, just not silly."""
    if not holes:
        return []
    todo = list(holes)
    out = [todo.pop(0)]
    while todo:
        cx, cy = out[-1].x, out[-1].y
        i = min(range(len(todo)),
                key=lambda k: (todo[k].x - cx) ** 2 + (todo[k].y - cy) ** 2)
        out.append(todo.pop(i))
    return out


def _revolution(w, cx, cy, r, z_end=None, feed=None):
    """One full turn about (cx, cy), starting and ending at (cx + r, cy).

    Emitted as two half-circles on purpose: a G2 whose start and end coincide is
    ambiguous -- some controllers read it as a full turn, others as no motion.
    z_end, if given, is reached at the end of the turn (a helix).
    """
    z_mid = None if z_end is None else (w.z + z_end) / 2.0
    w.arc(cx - r, cy, -r, 0.0, z=z_mid, f=feed)
    w.arc(cx + r, cy, r, 0.0, z=z_end, f=feed)


def mill_hole(w, hole, tool_dia, depth, feed, plunge_feed, pitch):
    """One hole, helically milled if there is radial room, else plunged."""
    r = (hole.dia - tool_dia) / 2.0
    w.comment("%.2fmm %s hole at X%.3f Y%.3f" % (hole.dia, hole.kind, hole.x, hole.y))

    if r < MIN_HELIX_R:
        # Tool is (near enough) the hole size: it is a drill, so drill it.
        w.rapid_xy(hole.x, hole.y)
        w.plunge(-depth, plunge_feed)
        w.retract()
        return

    w.rapid_xy(hole.x + r, hole.y)
    w.plunge(0.0, plunge_feed)
    z = 0.0
    while z > -depth + 1e-9:
        z = max(-depth, z - pitch)
        _revolution(w, hole.x, hole.y, r, z_end=z, feed=feed)
    _revolution(w, hole.x, hole.y, r, feed=feed)   # flat finishing turn
    w.retract()


def drill_all(w, holes, tool_dia, depth, feed, plunge_feed, pitch):
    for hole in order_nearest(holes):
        mill_hole(w, hole, tool_dia, depth, feed, plunge_feed, pitch)


# ---------------------------------------------------------------- outline ----

def tab_spans(path, count, width):
    """Arc-length spans for `count` tabs of `width`, evenly spaced.

    Centres sit at (k + 0.5) of each share so no tab lands on the seam where the
    path starts and ends, which would split it in two.
    """
    total = geometry.perimeter(path)
    if count <= 0 or width <= 0 or total <= 0:
        return []
    if count * width >= total:
        raise geometry.GeometryError(
            "%d tabs of %.2fmm need %.2fmm but this outline is only %.2fmm "
            "around. Use fewer or narrower tabs."
            % (count, width, count * width, total))
    return [((k + 0.5) * total / count - width / 2.0,
             (k + 0.5) * total / count + width / 2.0) for k in range(count)]


def _walk(path, spans):
    """Yield (x, y, in_tab), inserting a point at every tab boundary."""
    bounds = sorted(b for span in spans for b in span)
    in_span = lambda s: any(a <= s <= b for a, b in spans)
    s = 0.0
    yield path[0][0], path[0][1], in_span(0.0)
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        for b in [b for b in bounds if s < b < s + seg]:
            t = (b - s) / seg
            bx, by = x0 + t * (x1 - x0), y0 + t * (y1 - y0)
            # Nudge off the boundary to decide which side we are landing on.
            yield bx, by, in_span(b + 1e-6)
        s += seg
        yield x1, y1, in_span(s)


def cut_path(w, path, depth, stepdown, feed, plunge_feed, spans, tab_z):
    """Contour one closed path to depth, lifting over tabs on breakthrough passes."""
    w.rapid_xy(path[0][0], path[0][1])
    z = 0.0
    while z > -depth + 1e-9:
        z = max(-depth, z - stepdown)
        w.plunge(z, plunge_feed)
        if z >= tab_z or not spans:
            for x, y in path[1:]:
                w.feed_xy(x, y, feed)
            continue
        # This pass would cut through the tabs, so step over them.
        was_in = None
        for x, y, in_tab in _walk(path, spans):
            if was_in is None:
                was_in = in_tab
                continue
            if in_tab != was_in:
                w.feed_xy(x, y, feed)               # arrive at the boundary
                w.feed_z(tab_z if in_tab else z, plunge_feed)
                was_in = in_tab
                continue
            w.feed_xy(x, y, feed)
        if was_in:
            w.feed_z(z, plunge_feed)
    w.retract()


def cut_outline(w, paths, depth, stepdown, feed, plunge_feed,
                tab_count, tab_width, tab_z):
    for i, path in enumerate(paths):
        w.comment("outline path %d of %d (%.1fmm around)"
                  % (i + 1, len(paths), geometry.perimeter(path)))
        cut_path(w, path, depth, stepdown, feed, plunge_feed,
                 tab_spans(path, tab_count, tab_width), tab_z)
