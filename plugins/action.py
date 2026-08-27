"""KiCad action plugin entry point."""
import os
import traceback

import pcbnew
import wx

from . import dialog, gcode, geometry, holes, program, verify

IDENTIFIER = "com.github.peterb154.kicad-gcode-plugin"

# Anything the generator raises on purpose. These carry an explanation written
# for the operator, so they are shown as-is rather than as a stack trace.
EXPECTED = (gcode.GcodeError, geometry.GeometryError, holes.HoleError,
            verify.VerifyError)


def _find_icon():
    """Locate icon.png across dev-symlink and PCM install layouts."""
    here = os.path.dirname(os.path.abspath(__file__))
    real = os.path.dirname(os.path.realpath(__file__))
    for cand in (
        os.path.join(os.path.dirname(real), "resources", "icon.png"),
        os.path.join(os.path.dirname(here), "resources", "icon.png"),
        os.path.join(here, "icon.png"),
        os.path.join(os.path.dirname(os.path.dirname(here)),
                     "resources", IDENTIFIER, "icon.png"),
    ):
        if os.path.isfile(cand):
            return cand
    return ""


class Z1GcodePlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Z1 drill & cutout g-code"
        self.category = "Fabrication"
        self.description = ("Generate Makera Z1 g-code to drill holes and cut "
                            "the board outline")
        self.show_toolbar_button = True
        self.icon_file_name = _find_icon()

    def Run(self):
        board = pcbnew.GetBoard()
        if board is None:
            wx.MessageBox("No board is open. Open a .kicad_pcb first.",
                          "Nothing to generate", wx.OK | wx.ICON_WARNING)
            return

        src = board.GetFileName() or ""
        default = os.path.join(os.path.dirname(src) or os.path.expanduser("~"),
                               (os.path.splitext(os.path.basename(src))[0]
                                or "board") + "_z1.ngc")
        dlg = dialog.SettingsDialog(None, default, program.default_depth(board))
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        vals = dlg.values()
        dlg.Destroy()

        lines = []
        try:
            path = program.write(board, program.Options(**vals),
                                 log=lines.append)
        except EXPECTED as e:
            wx.MessageBox("%s\n\nNothing was written." % e,
                          "Cannot generate", wx.OK | wx.ICON_ERROR)
            return
        except Exception:
            wx.MessageBox(traceback.format_exc(), "Unexpected error",
                          wx.OK | wx.ICON_ERROR)
            return

        wx.MessageBox(
            "\n".join(lines)
            + "\n\nWrote: %s\n\nUSE A SACRIFICIAL LAYER. Set XY origin to the "
              "drill/place origin, register the first tool, Auto Z Probe ON."
            % os.path.basename(path),
            "G-code written", wx.OK | wx.ICON_INFORMATION)
