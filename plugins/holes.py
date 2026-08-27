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


# The two kinds of bit this can drive. They are NOT interchangeable, and the
# difference is not cosmetic -- it decides whether the toolpath moves sideways.
ENDMILL = "endmill"
DRILL = "drill"

BIT_LABEL = {
    ENDMILL: "CORN/FISHTAIL END MILL",
    DRILL: "TWIST DRILL",
}


def describe(bit_type, dia):
    """The text an operator reads off the M6 prompt with a bit in their hand.

    Deliberately shouty and deliberately says what NOT to load. "0.70mm bit" is
    ambiguous between a 0.70mm corn and a 0.70mm twist drill, and loading the
    wrong one of those snaps it on the first revolution.
    """
    if bit_type == ENDMILL:
        return "%.2fmm %s (NOT a twist drill)" % (dia, BIT_LABEL[ENDMILL])
    return "%.2fmm %s (NOT an end mill)" % (dia, BIT_LABEL[DRILL])


def check_endmill(holes, tool_dia):
    """An end mill mills sideways, so it must be no wider than the hole."""
    small = sorted({h.dia for h in holes if h.dia < tool_dia - 1e-6})
    if small:
        raise HoleError(
            "The %.2fmm end mill is bigger than %d hole size(s) on this board: "
            "%s. A hole cannot be milled with a tool wider than the hole. Use a "
            "smaller bit, or exclude those holes."
            % (tool_dia, len(small), ", ".join("%.2fmm" % d for d in small)))


def check_drill(holes, max_changes):
    """A twist drill only makes its own diameter, so every size needs its own bit.

    There is no diameter mismatch to catch here -- the bit size is taken FROM
    the hole. What can go wrong is the operator discovering mid-job that they
    are being asked for eleven bits they may not own, so say it up front.
    """
    sizes = sorted({round(h.dia, 3) for h in holes})
    if len(sizes) > max_changes:
        raise HoleError(
            "Twist drilling this board needs %d different drills (%s), which is "
            "more than the %d tool changes allowed. A twist drill cannot cut "
            "sideways, so it can only make a hole its own size -- either raise "
            "the limit, or switch to an end mill and mill every size with one "
            "bit." % (len(sizes), ", ".join("%.2fmm" % d for d in sizes),
                      max_changes))
    return sizes


def by_diameter(holes):
    """Group holes by diameter, smallest first, for tidy output and reporting."""
    groups = collections.defaultdict(list)
    for h in holes:
        groups[round(h.dia, 3)].append(h)
    return sorted(groups.items())
