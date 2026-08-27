"""Settings dialog. Uses the wx that KiCad already ships."""
import os

import wx

INTRO = ("Writes ONE Makera Z1 program that drills the board's holes, stops for "
         "a manual tool change, then cuts the outline to size.\n"
         "The outline is cut last on purpose -- it is the op that frees the "
         "part. Everything is referenced to the drill/place origin, the same "
         "datum the LightBurn artwork uses. Hover any field for details.")

TIPS = {
    "outfile": "Where the .ngc is written.\n\n"
               "Defaults next to the .kicad_pcb. Upload it to the machine with "
               "the Carvera Controller app.",
    "depth":   "How far below the top surface the tool goes, in millimetres.\n\n"
               "Defaults to the board thickness from Board Setup plus 0.2mm of "
               "breakthrough. Through-cuts on this machine run ~0.2mm past, so "
               "a SACRIFICIAL LAYER is required -- without one you are cutting "
               "into the fixture.",
    "rpm":     "Spindle speed. The Z1 tops out at 13000 rpm and the generator "
               "refuses anything higher.",
    "vias":    "Drill vias as well as through-hole pads.\n\n"
               "OFF by default, and that default is deliberate. On a ViaGrid "
               "blank the vias are already in the blank -- drilling them "
               "destroys the one feature you bought the blank for. On plain "
               "stock a 0.2mm grid via is far below the smallest bit in the "
               "kit anyway. Turn this on only for a board that is neither.",
    "drill":   "Drill the holes. Holes larger than the bit are milled "
               "helically; holes the same size as the bit are plunged.",
    "cut":     "Cut the board outline, offset outward by the cutter radius so "
               "the tool rides on the waste side of the line. Internal cutouts "
               "are offset inward automatically.",
    "dtool":   "Tool number for the drilling bit.\n\n"
               "Any T1-T99 makes M6 prompt for a manual swap and re-zero the "
               "bit on the setter. Give each physical bit its own number: if an "
               "M6 asks for the number already loaded, the controller may skip "
               "the re-zero.",
    "ddia":    "Diameter of the drilling bit, in millimetres.\n\n"
               "Must be no larger than the smallest hole on the board -- a hole "
               "cannot be milled with a bit wider than the hole. The generator "
               "checks this and refuses rather than making a wrong hole.",
    "ctool":   "Tool number for the outline cutter. Must differ from the "
               "drilling tool number.",
    "cdia":    "Diameter of the outline cutter, in millimetres.\n\n"
               "A stiffer bit than the drill resists deflection on the long "
               "perimeter cut. It also sets the offset: the path is grown by "
               "half this value.",
    "pitch":   "How far a helical hole descends per full turn, in millimetres.",
    "step":    "Depth of cut per pass on the outline, in millimetres.",
    "tabs":    "Number of holding tabs left around the outline.\n\n"
               "Without tabs the part comes loose on the final pass and gets "
               "thrown by the cutter.",
    "tabw":    "Width of each tab along the outline, in millimetres.",
    "tabz":    "Z the tool lifts to while crossing a tab.\n\n"
               "The difference between this and the cut depth is how much "
               "material holds the part. -1.0 against a 1.8mm cut leaves 0.6mm "
               "on a 1.6mm board -- enough to hold, thin enough to snap off.",
    "pause":   "Pause after cutting any holes that lie OUTSIDE the board "
               "outline, so you can fit dowel pins before the rest of the job.\n\n"
               "A hole beyond your own Edge.Cuts can only be a fixture hole. On "
               "a ViaGrid blank there usually are none -- the blank arrives "
               "with its mounting holes drilled and the job is to locate to "
               "them, not to make them.",
}


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, default_file, default_depth):
        wx.Dialog.__init__(self, parent, title="Z1 drill & cutout g-code",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.ctrls = {}
        outer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(self, label=INTRO)
        intro.Wrap(560)
        outer.Add(intro, 0, wx.ALL, 12)

        grid = wx.FlexGridSizer(0, 4, 6, 8)
        grid.AddGrowableCol(1, 1)

        self._path_row(grid, "outfile", "Output file", default_file)
        self._num_row(grid, "depth", "Cut depth (mm)", default_depth,
                      "rpm", "Spindle (rpm)", 13000)
        grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1)

        self._num_row(grid, "dtool", "Drill tool #", 1, "ddia", "Drill dia (mm)", 0.7)
        self._num_row(grid, "dfeed", "Drill feed (mm/min)", 60,
                      "dplunge", "Drill plunge (mm/min)", 30)
        self._num_row(grid, "pitch", "Helix pitch (mm/turn)", 0.15,
                      None, None, None)
        grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1)

        self._num_row(grid, "ctool", "Cut tool #", 2, "cdia", "Cutter dia (mm)", 1.2)
        self._num_row(grid, "cfeed", "Cut feed (mm/min)", 150,
                      "cplunge", "Cut plunge (mm/min)", 40)
        self._num_row(grid, "step", "Cut stepdown (mm)", 0.4,
                      "tabs", "Tabs", 4)
        self._num_row(grid, "tabw", "Tab width (mm)", 1.0,
                      "tabz", "Tab Z (mm)", -1.0)
        outer.Add(grid, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        box = wx.BoxSizer(wx.VERTICAL)
        for key, label, default in (
                ("drill", "Drill holes", True),
                ("cut", "Cut outline", True),
                ("pause", "Pause for dowel pins after registration holes", True),
                ("vias", "Also drill vias (NOT for ViaGrid blanks)", False)):
            cb = wx.CheckBox(self, label=label)
            cb.SetValue(default)
            cb.SetToolTip(TIPS[key])
            self.ctrls[key] = cb
            box.Add(cb, 0, wx.TOP, 4)
        outer.Add(box, 0, wx.ALL, 12)

        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        outer.Add(btns, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(outer)

    # -- row helpers -----------------------------------------------------
    def _labelled(self, grid, key, label, value):
        st = wx.StaticText(self, label=label)
        tc = wx.TextCtrl(self, value=str(value), size=(90, -1))
        tip = TIPS.get(key)
        if tip:
            st.SetToolTip(tip)
            tc.SetToolTip(tip)
        grid.Add(st, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(tc, 1, wx.EXPAND)
        self.ctrls[key] = tc

    def _num_row(self, grid, k1, l1, v1, k2, l2, v2):
        self._labelled(grid, k1, l1, v1)
        if k2 is None:
            grid.AddSpacer(1)
            grid.AddSpacer(1)
        else:
            self._labelled(grid, k2, l2, v2)

    def _path_row(self, grid, key, label, value):
        st = wx.StaticText(self, label=label)
        st.SetToolTip(TIPS[key])
        tc = wx.TextCtrl(self, value=value)
        tc.SetToolTip(TIPS[key])
        btn = wx.Button(self, label="Browse...")
        btn.Bind(wx.EVT_BUTTON, lambda _e: self._browse(tc))
        grid.Add(st, 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(tc, 1, wx.EXPAND)
        grid.Add(btn, 0)
        grid.AddSpacer(1)
        self.ctrls[key] = tc

    def _browse(self, tc):
        d = wx.FileDialog(self, "Write g-code to",
                          defaultDir=os.path.dirname(tc.GetValue()),
                          defaultFile=os.path.basename(tc.GetValue()),
                          wildcard="G-code (*.ngc)|*.ngc|All files|*",
                          style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)
        if d.ShowModal() == wx.ID_OK:
            tc.SetValue(d.GetPath())
        d.Destroy()

    # -- results ---------------------------------------------------------
    def values(self):
        g = lambda k: self.ctrls[k].GetValue()
        num = lambda k: float(g(k))
        return dict(
            outfile=g("outfile").strip(),
            do_drill=g("drill"), do_cut=g("cut"),
            include_vias=g("vias"), pause_for_pins=g("pause"),
            depth=num("depth"), rpm=int(num("rpm")),
            drill_tool=int(num("dtool")), drill_dia=num("ddia"),
            drill_feed=num("dfeed"), drill_plunge=num("dplunge"),
            helix_pitch=num("pitch"),
            cut_tool=int(num("ctool")), cut_dia=num("cdia"),
            cut_feed=num("cfeed"), cut_plunge=num("cplunge"),
            cut_stepdown=num("step"),
            tab_count=int(num("tabs")), tab_width=num("tabw"),
            tab_z=num("tabz"),
        )
