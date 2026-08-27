"""The Makera Z1 / Carvera g-code dialect, in one place.

Everything the machine is picky about lives here so the op code can stay about
geometry. Facts encoded below are measured, not assumed -- see makera_z1/machine.md:

  * Spindle 0-13000 rpm. Cap every S value.
  * Smoothieware HALTS on G81, G64 and G91.1. Never emit them.
  * G28 is "goto clearance/park", NOT a homing cycle. It belongs just before M2.
  * M30 does nothing on a Carvera. Program end is M2.
  * Manual tool change: any T1-T99 makes M6 prompt for the swap and re-zero the
    tool on the back-right setter. There is no carousel and no tool-number floor
    (an older note here claimed T1-T6 fail -- that was wrong, verified 2026-07-17).
  * The FIRST tool must be declared with an explicit M6 at the top of the file or
    the auto-detect restore has nothing to resolve (the M495 bug).
  * Do not reuse a tool number for a different bit: if an M6 asks for the number
    already loaded, the controller may skip the setter re-zero.

Z heights: ZSAFE must clear the hold-down clamps (7.2 mm) and anything proud of
the work. ZCHANGE sits above ZSAFE for the trip to the setter.
"""

ZSAFE = 15.0      # traverse height -- clears the 7.2mm clamps
ZCHANGE = 20.0    # tool-change height
CLEARANCE = 1.0   # rapid down to this above the work, then feed in
MAX_RPM = 13000
SPINUP_S = 1.5


class GcodeError(RuntimeError):
    pass


def _n(v):
    """Format a number: fixed point, no exponent, trimmed, never negative zero.

    "-0" is legal g-code but reads as a sign error every time a human scans the
    file, and machine.md's rule is to eyeball every NC before running it. Do not
    make that harder.
    """
    s = "%.4f" % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Writer(object):
    """Accumulates lines and tracks Z so air moves can be rapided."""

    def __init__(self, zsafe=ZSAFE, zchange=ZCHANGE, clearance=CLEARANCE,
                 rpm=MAX_RPM):
        if rpm > MAX_RPM:
            raise GcodeError(
                "Spindle speed %d rpm exceeds the Z1's 13000 rpm ceiling." % rpm)
        self.zsafe = zsafe
        self.zchange = zchange
        self.clearance = clearance
        self.rpm = rpm
        self.lines = []
        self.z = None
        self.tool = None

    # -- primitives ------------------------------------------------------
    def raw(self, line=""):
        self.lines.append(line)

    def comment(self, text):
        self.lines.append("( %s )" % text)

    def rule(self):
        self.lines.append("( %s )" % ("=" * 58))

    def msg(self, text):
        """Controller-visible message. Pairs with the M6 prompt."""
        self.lines.append("(MSG, %s)" % text)

    # -- motion ----------------------------------------------------------
    def rapid_z(self, z):
        if self.z is not None and abs(self.z - z) < 1e-9:
            return                      # already there; do not repeat the move
        self.lines.append("G00 Z%s" % _n(z))
        self.z = z

    def rapid_xy(self, x, y):
        self.lines.append("G00 X%s Y%s" % (_n(x), _n(y)))

    def feed_xy(self, x, y, f=None):
        self.lines.append("G01 X%s Y%s%s"
                          % (_n(x), _n(y), " F%s" % _n(f) if f else ""))

    def feed_xyz(self, x, y, z, f=None):
        self.lines.append("G01 X%s Y%s Z%s%s"
                          % (_n(x), _n(y), _n(z), " F%s" % _n(f) if f else ""))
        self.z = z

    def feed_z(self, z, f):
        """Pure vertical feed -- used to step over a holding tab."""
        self.lines.append("G01 Z%s F%s" % (_n(z), _n(f)))
        self.z = z

    def arc(self, x, y, i, j, z=None, ccw=False, f=None):
        """G2/G3. With z, this is a helix -- the linear axis moves during the arc."""
        self.lines.append("%s X%s Y%s%s I%s J%s%s" % (
            "G03" if ccw else "G02", _n(x), _n(y),
            " Z%s" % _n(z) if z is not None else "", _n(i), _n(j),
            " F%s" % _n(f) if f else ""))
        if z is not None:
            self.z = z

    def plunge(self, z, f):
        """Descend to z, rapiding the air and feeding only the last CLEARANCE mm.

        pcb2gcode fed the whole descent from zsafe at the plunge rate, which with
        zsafe=15 is a 15mm crawl on every single hole. Only the part that can
        touch material is fed.
        """
        if self.z is not None and self.z > self.clearance and z < self.clearance:
            self.lines.append("G00 Z%s" % _n(self.clearance))
        self.lines.append("G01 Z%s F%s" % (_n(z), _n(f)))
        self.z = z

    def retract(self):
        self.rapid_z(self.zsafe)

    # -- structure -------------------------------------------------------
    def header(self, title, notes=()):
        self.rule()
        self.comment(title)
        for nline in notes:
            self.comment(nline)
        self.rule()
        self.raw()
        self.raw("G21      ( mm )")
        self.raw("G90      ( absolute )")
        self.raw("G94      ( units per minute )")
        self.raw("G54      ( work coordinate system )")
        self.raw()

    def toolchange(self, tnum, desc, first=False):
        """Manual change. M6 prompts for the swap and re-zeros on the setter."""
        if not 1 <= tnum <= 99:
            raise GcodeError("Tool number T%d is outside the Z1's T1-T99 range."
                             % tnum)
        if tnum == self.tool:
            raise GcodeError(
                "T%d is already loaded; re-requesting the same number can make "
                "the controller skip the setter re-zero. Give each bit its own "
                "tool number." % tnum)
        self.raw()
        self.comment("---- TOOL %s: %s (T%d) ----"
                     % ("SETUP" if first else "CHANGE", desc, tnum))
        if not first:
            self.rapid_z(self.zchange)
            self.raw("M5      ( spindle stop )")
            self.raw("G04 P%s" % _n(SPINUP_S))
        self.msg("Install T%d: %s" % (tnum, desc))
        self.raw("M6 T%d      ( manual change + re-zero on setter )" % tnum)
        self.raw("M3 S%d      ( spindle on )" % self.rpm)
        self.raw("G04 P%s      ( spin up )" % _n(SPINUP_S))
        self.rapid_z(self.zsafe)
        self.raw()
        self.tool = tnum

    def pause(self, message):
        """Stop mid-program so the operator can do something (e.g. fit pins)."""
        self.raw()
        self.rapid_z(self.zchange)
        self.raw("M5      ( spindle stop )")
        self.msg(message)
        self.raw("M0      ( pause -- press play to continue )")
        self.raw("M3 S%d" % self.rpm)
        self.raw("G04 P%s" % _n(SPINUP_S))
        self.rapid_z(self.zsafe)
        self.raw()

    def footer(self):
        self.retract()
        self.raw("M5      ( spindle stop )")
        self.raw("G28      ( goto clearance / park -- NOT a homing cycle )")
        self.raw("M2      ( program end -- M30 does nothing on a Carvera )")

    def text(self):
        return "\n".join(self.lines) + "\n"
