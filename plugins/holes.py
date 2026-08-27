"""Hole collection, in machine coordinates.

Vias are EXCLUDED by default, deliberately. On a ViaGrid blank the vias are
already in the blank -- drilling them would be destroying the one feature you
bought the blank for. And on plain stock a 0.2mm grid via is far below the
smallest tool in the kit anyway, so there is no case where blindly drilling
vias is right. The option exists for boards that are neither.

Holes OUTSIDE the board outline are treated as registration features rather than
part fixings: a hole beyond your own Edge.Cuts can only be a fixture hole. They
get cut first so the blank can be pinned before anything else happens. On a
ViaGrid blank there usually are none -- the blank arrives with its mounting
holes already drilled, and the job is to locate to them, not to make them.
"""
import collections

import pcbnew

from . import geometry

Hole = collections.namedtuple("Hole", "x y dia kind")


class HoleError(RuntimeError):
    pass


def collect(board, include_vias=False):
    """Every drillable hole, in machine mm relative to the aux origin."""
    origin = geometry.aux_origin_iu(board)
    holes = []

    if include_vias:
        for t in board.GetTracks():
            if isinstance(t, pcbnew.PCB_VIA):
                p = t.GetPosition()
                x, y = geometry.to_machine(p.x, p.y, origin)
                holes.append(Hole(x, y, pcbnew.ToMM(t.GetDrillValue()), "via"))

    slots = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            attr = pad.GetAttribute()
            if attr not in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                continue
            d = pad.GetDrillSize()
            dx, dy = pcbnew.ToMM(d.x), pcbnew.ToMM(d.y)
            if dx <= 0 or dy <= 0:
                continue
            p = pad.GetPosition()
            x, y = geometry.to_machine(p.x, p.y, origin)
            if abs(dx - dy) > 0.001:
                slots.append("%s pad %s (%.2f x %.2f mm) at %.2f, %.2f"
                             % (fp.GetReference(), pad.GetNumber(), dx, dy, x, y))
                continue
            kind = "npth" if attr == pcbnew.PAD_ATTRIB_NPTH else "pth"
            holes.append(Hole(x, y, dx, kind))

    if slots:
        raise HoleError(
            "This board has %d oval/slotted drill(s). A slot has to be routed, "
            "not drilled -- putting one round hole in the middle of it would "
            "look right in the preview and be wrong on the board. Slots are not "
            "supported yet; convert them to round holes or remove them:\n  %s"
            % (len(slots), "\n  ".join(slots)))
    return holes


def partition(board, holes):
    """Split into (registration, inside) by the board outline."""
    polys = geometry.outline_points(board)
    reg, inside = [], []
    for h in holes:
        (inside if geometry.contains(polys, h.x, h.y) else reg).append(h)
    return reg, inside


def check_tool(holes, tool_dia):
    """Refuse holes the tool cannot make. Milling needs tool <= hole."""
    small = sorted({h.dia for h in holes if h.dia < tool_dia - 1e-6})
    if small:
        raise HoleError(
            "The %.2fmm tool is bigger than %d hole size(s) on this board: %s. "
            "A hole cannot be milled with a tool wider than the hole. Use a "
            "smaller bit, or exclude those holes."
            % (tool_dia, len(small), ", ".join("%.2fmm" % d for d in small)))


def by_diameter(holes):
    """Group holes by diameter, smallest first, for tidy output and reporting."""
    groups = collections.defaultdict(list)
    for h in holes:
        groups[round(h.dia, 3)].append(h)
    return sorted(groups.items())
