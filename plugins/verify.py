"""Re-read the generated g-code and refuse to ship it if it is wrong.

machine.md says to eyeball every posted NC before running it: sane tool numbers,
S <= 13000, no G64/G81/G91.1, ends M5 -> G28 -> M2, sane coordinate ranges and
depths. That is a checklist, and checklists belong in code. A generator bug that
produces plausible-looking g-code costs a broken cutter at best; these checks are
cheap and they run on the actual output text, not on the intent behind it.
"""
import re

# Smoothieware halts on these. See machine.md sec 2.
FORBIDDEN = {
    "G64": "path blending -- Smoothieware halts before the spindle starts",
    "G81": "canned drill cycle -- unsupported, must be expanded to G0/G1",
    "G91.1": "arc distance mode -- unsupported",
    "M30": "does nothing on a Carvera; program end must be M2",
}
MAX_RPM = 13000

WORD = re.compile(r"(?<![A-Za-z0-9.])([GMSXYZIJFPT])(-?\d+(?:\.\d+)?)")
CODE = re.compile(r"\b(G\d+(?:\.\d+)?|M\d+)\b")


class VerifyError(RuntimeError):
    pass


def _strip(line):
    """Drop comments; ( ... ) and anything after a ;."""
    return re.sub(r"\([^)]*\)", " ", line).split(";")[0]


def check(text, max_depth, travel_limit=400.0, no_arcs=False):
    """Raise VerifyError on anything that would misbehave on the machine.

    no_arcs is the twist-drill guard. The outline cut is emitted as straight
    segments, so a helical hole is the only thing in this generator that can
    produce a G2/G3 -- which means "contains no arcs" is a complete, checkable
    proof that nothing asks a twist drill to move sideways. A drill has no side
    flutes and no radial stiffness; one lateral move snaps it.
    """
    problems = []
    lines = text.splitlines()
    saw_m6 = False
    saw_motion_before_m6 = False
    zmin = 0.0

    for n, raw in enumerate(lines, 1):
        line = _strip(raw)
        if not line.strip():
            continue
        for code in CODE.findall(line):
            if code in FORBIDDEN:
                problems.append("line %d: %s -- %s" % (n, code, FORBIDDEN[code]))
        words = dict((w, float(v)) for w, v in WORD.findall(line))

        if "M" in line and re.search(r"\bM6\b", line):
            saw_m6 = True
        elif not saw_m6 and re.match(r"\s*G0?[0123]\b", line):
            saw_motion_before_m6 = True

        if "S" in words and words["S"] > MAX_RPM:
            problems.append("line %d: S%g exceeds the 13000 rpm ceiling"
                            % (n, words["S"]))
        if "T" in words and re.search(r"\bM6\b", line):
            t = words["T"]
            if not (1 <= t <= 99):
                problems.append("line %d: T%g is outside T1-T99" % (n, t))
        if "Z" in words:
            zmin = min(zmin, words["Z"])
        for ax in ("X", "Y"):
            if ax in words and abs(words[ax]) > travel_limit:
                problems.append("line %d: %s%g is beyond +/-%gmm -- almost "
                                "certainly a units or origin bug"
                                % (n, ax, words[ax], travel_limit))
        if re.match(r"\s*G0?[23]\b", line):
            if no_arcs:
                problems.append(
                    "line %d: arc (G2/G3) in a TWIST DRILL program -- a drill "
                    "cannot cut sideways and would snap here" % n)
            elif not ("I" in words or "J" in words):
                problems.append("line %d: arc with no I/J centre offset" % n)

    if saw_motion_before_m6:
        problems.append(
            "motion before the first M6: the first tool must be declared with an "
            "explicit M6 at the top or the auto-detect restore has nothing to "
            "resolve (the M495 bug, machine.md sec 5)")
    if zmin < -abs(max_depth) - 1e-6:
        problems.append("Z reaches %.3fmm, deeper than the %.3fmm cut depth"
                        % (zmin, -abs(max_depth)))

    tail = [l.strip() for l in (_strip(x) for x in lines) if l.strip()][-3:]
    if not (len(tail) == 3
            and tail[0].startswith("M5")
            and tail[1].startswith("G28")
            and tail[2].startswith("M2")):
        problems.append("program must end M5 -> G28 (park) -> M2; got %r" % (tail,))

    if problems:
        raise VerifyError(
            "The generated g-code failed %d safety check(s), so nothing was "
            "written:\n  - %s" % (len(problems), "\n  - ".join(problems)))
    return {"lines": len(lines), "zmin": zmin}
