"""Board geometry -> machine coordinates.

Two conversions happen here and nowhere else, because getting either one wrong
produces g-code that looks completely plausible and ruins a board:

1. ORIGIN. Everything is referenced to the aux (drill/place) origin -- the same
   datum the LightBurn artwork uses. That shared datum is the whole contract
   between the laser and the mill.

2. Y SIGN. KiCad's Y axis points DOWN; the machine's points UP. Every coordinate
   leaving this module is negated in Y. Skip it and the board comes out mirrored
   top-to-bottom, which on a symmetric outline is invisible until the holes miss.

Outline offsetting uses SHAPE_POLY_SET.Inflate (Clipper, built into KiCad), so
there is no pcb2gcode and no Gerber round-trip. Inflate grows *material*, which
is exactly right for both cases at once: the board's outer edge moves outward by
the cutter radius, and any internal cutout's edge moves inward by the same -- in
both cases putting the tool on the waste side of the line.
"""
import pcbnew

# Clipper arc tolerance when polygonising offset curves.
MAX_ERROR_IU = pcbnew.FromMM(0.005)


class GeometryError(RuntimeError):
    pass


def aux_origin_iu(board):
    """Aux (drill/place) origin in KiCad internal units."""
    ds = board.GetDesignSettings()
    for attr in ("GetAuxOrigin", "m_AuxOrigin"):
        o = getattr(ds, attr, None)
        if o is None:
            continue
        o = o() if callable(o) else o
        return o.x, o.y
    return 0, 0


def to_machine(x_iu, y_iu, origin):
    """KiCad internal units -> machine mm, relative to the aux origin.

    The Y negation is the KiCad-down / machine-up flip. See the module docstring.
    """
    ox, oy = origin
    return pcbnew.ToMM(x_iu - ox), -pcbnew.ToMM(y_iu - oy)


def board_outline(board):
    """Edge.Cuts as a SHAPE_POLY_SET. Raises if there is no closed outline."""
    ps = pcbnew.SHAPE_POLY_SET()
    ok = board.GetBoardPolygonOutlines(ps, False)
    if not ok or ps.OutlineCount() == 0:
        raise GeometryError(
            "Edge.Cuts has no closed outline, so there is nothing to cut and no "
            "way to tell which holes are inside the board. Draw a closed board "
            "outline first.")
    return ps


def _chain_points(chain, origin):
    pts = [to_machine(chain.CPoint(i).x, chain.CPoint(i).y, origin)
           for i in range(chain.PointCount())]
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])          # close it
    return pts


def outline_points(board):
    """The un-offset board outline, as closed polylines in machine mm."""
    origin = aux_origin_iu(board)
    ps = board_outline(board)
    return [_chain_points(ps.Outline(i), origin)
            for i in range(ps.OutlineCount())]


def cut_paths(board, cutter_dia_mm):
    """Closed polylines for the outline cut, offset to the waste side.

    Returns (paths, dropped) where dropped counts internal cutouts too small for
    the cutter to enter -- they are silently unmachinable otherwise.
    """
    if cutter_dia_mm <= 0:
        raise GeometryError("Cutter diameter must be positive.")
    origin = aux_origin_iu(board)
    ps = board_outline(board)

    before = sum(ps.HoleCount(i) for i in range(ps.OutlineCount()))
    ps.Inflate(int(pcbnew.FromMM(cutter_dia_mm / 2.0)),
               pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, MAX_ERROR_IU, True)
    if ps.OutlineCount() == 0:
        raise GeometryError(
            "Offsetting the outline by the %.2fmm cutter radius collapsed the "
            "board to nothing. The cutter is too big for this outline."
            % (cutter_dia_mm / 2.0))

    paths = []
    after = 0
    for i in range(ps.OutlineCount()):
        paths.append(_chain_points(ps.Outline(i), origin))
        after += ps.HoleCount(i)
        for j in range(ps.HoleCount(i)):
            paths.append(_chain_points(ps.Hole(i, j), origin))
    return paths, max(0, before - after)


def perimeter(pts):
    """Arc length of a polyline."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        total += ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
    return total


def contains(polys, x, y):
    """Even-odd point-in-polygon against a list of closed polylines."""
    inside = False
    for pts in polys:
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            if (y0 > y) != (y1 > y):
                xi = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                if x < xi:
                    inside = not inside
    return inside
