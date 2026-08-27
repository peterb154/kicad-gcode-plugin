"""Assemble one Z1 program: make every hole, change tool, cut the outline.

One file with in-program M6 changes rather than several, so XY origin is set
once and Z0 carries across each change via the new tool's TLO. Ordering is not
cosmetic: the outline cut goes LAST because it is the op that frees the part.
Anything still to be done to a board that is no longer held down is a mistake.

The job is planned before any g-code is emitted, so the tool plan can be
rendered three ways from one source of truth -- the file header, the dialog
summary, and the setup sheet you take to the machine.
"""
import os

import pcbnew

from . import gcode, geometry, holes, ops, verify

BREAKTHROUGH_MM = 0.2      # go past the far face; a sacrificial layer takes it
# Output is named for what is in it, so a holes-only run and an outline-only
# run can live side by side. Splitting them is the normal shape when something
# else happens to the board in between -- ablation, plating, inspection.
TAGS = {(True, True): "", (True, False): "_holes", (False, True): "_outline"}


class Options(object):
    def __init__(self, outdir,
                 do_drill=True, do_cut=True, include_vias=True,
                 depth=None, rpm=13000,
                 bit_type=holes.ENDMILL,
                 hole_tool=1, hole_dia=0.7,
                 hole_feed=60.0, hole_plunge=30.0,
                 helix_pitch=0.15, peck_depth=0.3, max_drill_changes=6,
                 cut_tool=2, cut_dia=1.2,
                 cut_feed=150.0, cut_plunge=40.0, cut_stepdown=0.4,
                 tab_count=4, tab_width=1.0, tab_z=-1.0,
                 pause_for_pins=True, write_sheet=True):
        for k, v in list(locals().items()):
            if k != "self":
                setattr(self, k, v)
        if bit_type not in (holes.ENDMILL, holes.DRILL):
            raise gcode.GcodeError("Unknown bit type %r." % bit_type)


class Step(object):
    """One tool in the plan: what to install, and what it does once loaded."""

    def __init__(self, tool, dia, bit, role, count, detail):
        self.tool, self.dia, self.bit = tool, dia, bit
        self.role, self.count, self.detail = role, count, detail

    def bit_text(self):
        return holes.describe(self.bit, self.dia)

    def line(self):
        return "T%-3d %6.2f mm  %-26s %s" % (
            self.tool, self.dia, holes.BIT_LABEL[self.bit], self.detail)


class Job(object):
    """Everything decided about a run, before a single line is emitted."""

    def __init__(self, board, opt):
        self.board, self.opt = board, opt
        if not (opt.do_drill or opt.do_cut):
            raise gcode.GcodeError(
                "Nothing selected: pick holes, the outline, or both.")
        self.depth = opt.depth if opt.depth else default_depth(board)
        self.thickness = board_thickness(board)
        if opt.do_cut and opt.tab_z <= -self.depth:
            raise gcode.GcodeError(
                "Tab height %.2fmm is at or below the %.2fmm cut depth, so the "
                "tabs would be cut through and the part would come loose "
                "mid-cut." % (opt.tab_z, self.depth))

        self.all_holes, self.reg, self.inside, self.drills = [], [], [], []
        if opt.do_drill:
            self.all_holes = holes.collect(board, include_vias=opt.include_vias)
            if not self.all_holes:
                raise gcode.GcodeError(
                    "No holes found. This board has no through-hole pads (vias "
                    "are excluded unless you tick 'include vias').")
            self.reg, self.inside = holes.partition(board, self.all_holes)
            if opt.bit_type == holes.ENDMILL:
                holes.check_endmill(self.all_holes, opt.hole_dia)
            else:
                holes.check_drill(self.all_holes, opt.max_drill_changes)
                self.drills = self._drill_plan()

        self.paths, self.dropped = ([], 0)
        if opt.do_cut:
            self.paths, self.dropped = geometry.cut_paths(board, opt.cut_dia)
        self.steps = self._steps()

    def _drill_plan(self):
        """[(tool, diameter, holes, phase)] for twist drilling.

        A twist drill only makes its own diameter, so each distinct size needs
        its own bit and its own tool number. Registration sizes come first so
        the blank can be pinned before anything else is cut. A diameter in both
        phases gets two numbers on purpose: re-requesting the number already
        loaded can make the controller skip the setter re-zero.
        """
        opt = self.opt
        plan, tnum = [], opt.hole_tool
        for phase, group in (("registration", self.reg), ("inside", self.inside)):
            for dia, hs in holes.by_diameter(group):
                plan.append((tnum, dia, hs, phase))
                tnum += 1
        used = [t for t, _, _, _ in plan]
        if opt.do_cut and opt.cut_tool in used:
            raise gcode.GcodeError(
                "Twist drilling needs tool numbers %s, which collides with the "
                "cutter's T%d. Move the cutter to T%d or higher."
                % (", ".join("T%d" % t for t in used), opt.cut_tool,
                   max(used) + 1))
        return plan

    def _steps(self):
        opt, steps = self.opt, []
        if opt.do_drill and opt.bit_type == holes.ENDMILL:
            steps.append(Step(
                opt.hole_tool, opt.hole_dia, holes.ENDMILL, "holes",
                len(self.all_holes),
                "%d hole(s), every size with this one bit, milled helically"
                % len(self.all_holes)))
        elif opt.do_drill:
            for tnum, dia, group, phase in self.drills:
                steps.append(Step(tnum, dia, holes.DRILL, phase, len(group),
                                  "%d %s hole(s), pecked" % (len(group), phase)))
        if opt.do_cut:
            steps.append(Step(
                opt.cut_tool, opt.cut_dia, holes.ENDMILL, "outline",
                len(self.paths),
                "outline %.1fmm, %d path(s), %d tab(s) of %.2fmm at Z%.2f"
                % (sum(geometry.perimeter(p) for p in self.paths),
                   len(self.paths), opt.tab_count, opt.tab_width, opt.tab_z)))
        return steps


def board_thickness(board):
    try:
        t = pcbnew.ToMM(board.GetDesignSettings().GetBoardThickness())
    except Exception:
        t = 0.0
    return t if t > 0 else 1.6


def default_depth(board):
    """Board thickness plus a breakthrough allowance."""
    return round(board_thickness(board) + BREAKTHROUGH_MM, 2)


def emit(job, log=None):
    """Render the planned job as g-code. Verifies before handing it back."""
    log = log or (lambda m: None)
    opt = job.opt
    if job.dropped:
        log("WARNING: %d internal cutout(s) too small for the %.2fmm cutter "
            "and NOT machined." % (job.dropped, opt.cut_dia))

    w = gcode.Writer(rpm=opt.rpm)
    notes = [
        "Generated by kicad-gcode-plugin from %s"
        % os.path.basename(job.board.GetFileName() or "(unsaved)"),
        "Origin = KiCad drill/place (aux) origin. Set XY once; do not move it.",
        "Cut depth %.2fmm (board %.2f + %.2f breakthrough) -- USE A "
        "SACRIFICIAL LAYER." % (job.depth, job.thickness, BREAKTHROUGH_MM),
        "",
        "TOOLS, in program order:",
    ]
    notes += ["  " + s.line() for s in job.steps]
    notes += ["",
              "SETUP: install the first tool, Change tool to register it,",
              "  set XY origin, Auto Z Probe ON, then Run."]
    w.header("Makera Z1 -- holes + outline cutout", notes)

    first = True
    if opt.do_drill and opt.bit_type == holes.ENDMILL:
        w.toolchange(opt.hole_tool, holes.describe(holes.ENDMILL, opt.hole_dia),
                     first=True)
        first = False
        if job.reg:
            w.comment("-- holes outside the outline, cut first%s --"
                      % (", so the blank can be pinned"
                         if (opt.pause_for_pins and job.inside) else ""))
            ops.drill_all(w, job.reg, opt.hole_dia, job.depth, opt.hole_feed,
                          opt.hole_plunge, opt.helix_pitch)
            if opt.pause_for_pins and job.inside:
                w.pause("Fit the dowel pins, then press play")
        if job.inside:
            w.comment("-- through-holes --")
            ops.drill_all(w, job.inside, opt.hole_dia, job.depth, opt.hole_feed,
                          opt.hole_plunge, opt.helix_pitch)
    elif opt.do_drill:
        paused = False
        for tnum, dia, group, phase in job.drills:
            if phase == "inside" and not paused and job.reg and opt.pause_for_pins:
                w.pause("Fit the dowel pins, then press play")
                paused = True
            w.toolchange(tnum, holes.describe(holes.DRILL, dia), first=first)
            first = False
            w.comment("-- %s: %.2fmm x %d --" % (phase, dia, len(group)))
            ops.peck_all(w, group, job.depth, opt.hole_feed, opt.peck_depth)

    if opt.do_cut:
        w.toolchange(opt.cut_tool, holes.describe(holes.ENDMILL, opt.cut_dia),
                     first=first)
        w.comment("-- outline cutout (LAST: this frees the part) --")
        ops.cut_outline(w, job.paths, job.depth, opt.cut_stepdown, opt.cut_feed,
                        opt.cut_plunge, opt.tab_count, opt.tab_width, opt.tab_z)

    w.footer()
    text = w.text()
    # In drill mode the program must contain NO arcs: the outline is emitted as
    # G1 segments (Clipper polygonises the curves), so a helix is the only thing
    # that can produce a G2/G3. A stray arc means a drill is about to go sideways.
    stats = verify.check(text, job.depth,
                         no_arcs=(opt.do_drill and opt.bit_type == holes.DRILL))
    for s in job.steps:
        log("T%d %.2fmm %s: %s"
            % (s.tool, s.dia, holes.BIT_LABEL[s.bit], s.detail))
    log("Verified: %d lines, deepest Z %.3fmm." % (stats["lines"], stats["zmin"]))
    return text


def setup_sheet(job, gcode_path):
    """The sheet you take to the machine. Plain text, printable, no jargon.

    It exists because the tool plan is the part you need in your hand while
    swapping bits, and a dialog you dismissed is not in your hand.
    """
    opt, b = job.opt, job.board
    name = os.path.basename(b.GetFileName() or "(unsaved)")
    rule = "=" * 64
    L = [rule,
         "Makera Z1 setup sheet -- %s" % os.path.splitext(name)[0],
         rule,
         "",
         "G-code    %s" % os.path.basename(gcode_path),
         "Board     %s  (%.2fmm thick)" % (name, job.thickness),
         "Origin    KiCad drill/place (aux) origin",
         "Depth     %.2fmm  (%.2f board + %.2f breakthrough)"
         % (job.depth, job.thickness, BREAKTHROUGH_MM),
         "Spindle   %d rpm" % opt.rpm,
         "",
         "TOOLS, in program order",
         "-" * 64]
    for s in job.steps:
        L.append("  " + s.line())
    L += ["", "You will be asked to install:", "-" * 64]
    for s in job.steps:
        L.append("  T%-3d %s" % (s.tool, s.bit_text()))
    L += ["",
          "BEFORE YOU START",
          "-" * 64,
          "  * USE A SACRIFICIAL LAYER. Cuts go ~0.2mm past the far face.",
          "  * Install T%d, then Tool dropdown -> Change tool -> T%d"
          % (job.steps[0].tool, job.steps[0].tool),
          "    (registers the tool and sets its TLO).",
          "  * Set XY origin to the drill/place origin. Do not move it again.",
          "  * Run the first tool with Auto Z Probe ON. Z0 is tool-independent,",
          "    so later tools need no re-probe -- the M6 re-zeros them on the",
          "    back-right setter.",
          "",
          "DURING THE RUN",
          "-" * 64]
    if opt.pause_for_pins and job.reg and job.inside:
        L.append("  * After the registration holes the program PAUSES (M0):")
        L.append("    fit the dowel pins, then press play.")
    L += ["  * Each M6 stops and prompts for the swap, then re-zeros on the",
          "    setter. Do not skip the re-zero."]
    if opt.do_cut:
        L += ["  * The outline is cut LAST: it is what frees the part. It stays",
              "    held by %d tab(s) of %.2fmm until you snap them."
              % (opt.tab_count, opt.tab_width)]
    L.append("")
    if job.dropped:
        L += ["WARNING", "-" * 64,
              "  %d internal cutout(s) are too small for the %.2fmm cutter and"
              % (job.dropped, opt.cut_dia),
              "  were NOT machined.", ""]
    return "\n".join(x for x in L if x is not None) + "\n"


def output_path(board, opt, sheet=False):
    """<outdir>/<board>_z1[_holes|_outline][.ngc|_setup.txt]."""
    stem = os.path.splitext(os.path.basename(board.GetFileName() or ""))[0]
    return os.path.join(
        os.path.abspath(os.path.expanduser(opt.outdir.strip())),
        "%s_z1%s%s" % (stem or "board", TAGS[(bool(opt.do_drill), bool(opt.do_cut))],
                       "_setup.txt" if sheet else ".ngc"))


def build(board, opt, log=None):
    """Plan and render, without writing anything."""
    return emit(Job(board, opt), log=log)


def write(board, opt, log=None):
    """Build, then write the g-code and its setup sheet. Returns the paths.

    The job is planned and rendered FIRST: if the board cannot produce valid
    g-code, no directory gets created for output that never arrives.
    """
    log = log or (lambda m: None)
    if not (opt.outdir or "").strip():
        raise gcode.GcodeError("No output folder given.")
    job = Job(board, opt)
    text = emit(job, log=log)

    path = output_path(board, opt)
    folder = os.path.dirname(path)
    if os.path.isfile(folder):
        raise gcode.GcodeError(
            "%s is a file, not a folder. Pick a folder for the g-code." % folder)
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError as e:
            raise gcode.GcodeError("Could not create %s: %s" % (folder, e))
        log("Created %s" % folder)

    written = []
    for target, body in ((path, text),
                         (output_path(board, opt, sheet=True),
                          setup_sheet(job, path) if opt.write_sheet else None)):
        if body is None:
            continue
        try:
            with open(target, "w") as fh:
                fh.write(body)
        except OSError as e:
            raise gcode.GcodeError("Could not write %s: %s" % (target, e))
        written.append(target)
    return written
