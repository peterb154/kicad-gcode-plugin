"""Settings dialog. Uses the wx that KiCad already ships."""
import os

import wx

INTRO = ("Writes ONE Makera Z1 program that makes the board's holes, stops for "
         "a manual tool change, then cuts the outline to size.\n"
         "CHECK THE BIT TYPE below -- an end mill mills holes sideways and a "
         "twist drill cannot, so the wrong bit in the spindle breaks.\n"
         "The outline is cut last on purpose -- it is the op that frees the "
         "part. Everything is referenced to the drill/place origin, the same "
         "datum the LightBurn artwork uses. Hover any field for details.")

TIPS = {
    "outdir":  "Folder the .ngc is written to. The filename always follows the "
               "board: <board name>_z1.ngc.\n\n"
               "Defaults to a Production/ folder beside the .kicad_pcb, created "
               "if it is not there yet. Generated g-code is a build artifact: "
               "keeping it in its own folder means one gitignore line and no "
               "hesitation about deleting it.\n\n"
               "Upload the file to the machine with the Carvera Controller app.",
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
    "drill":   "Make the board's holes with the bit type selected above.",
    "cut":     "Cut the board outline, offset outward by the cutter radius so "
               "the tool rides on the waste side of the line. Internal cutouts "
               "are offset inward automatically.",
    "bit":     "WHICH KIND OF BIT goes in the spindle. These are not "
               "interchangeable and picking wrong destroys the bit.\n\n"
               "CORN / FISHTAIL END MILL -- holes are milled helically, so one "
               "bit makes every hole size on the board. The path moves "
               "sideways, which only an end mill can survive.\n\n"
               "TWIST DRILL -- holes are pecked straight down with no sideways "
               "motion at all. A drill has no side flutes and no radial "
               "stiffness, so one lateral move snaps it. It can only make a "
               "hole its own size, so every distinct diameter needs its own "
               "bit and its own tool change.",
    "peck":    "How deep each peck goes before the drill fully retracts to "
               "clear chips, in millimetres. Twist drill mode only.\n\n"
               "FR4 dust packs a small flute fast; that is what breaks drills "
               "that are otherwise being used correctly.",
    "maxchg":  "Refuse the job if twist drilling would need more bit changes "
               "than this.\n\n"
               "Stops you discovering at the machine that the board wants "
               "eleven drills you may not own. An end mill does every size "
               "with one bit.",
    "dtool":   "Tool number for the hole bit.\n\n"
               "In twist drill mode this is the FIRST number; each additional "
               "diameter takes the next one up.\n\n"
               "Any T1-T99 makes M6 prompt for a manual swap and re-zero the "
               "bit on the setter. Give each physical bit its own number: if an "
               "M6 asks for the number already loaded, the controller may skip "
               "the re-zero.",
    "ddia":    "Diameter of the end mill, in millimetres. END MILL MODE ONLY -- "
               "a twist drill's size is taken from each hole instead.\n\n"
               "Must be no larger than the smallest hole on the board: a hole "
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
    "sheet":   "Also write <board>_z1_setup.txt beside the g-code.\n\n"
               "The tool plan in printable form: which bit goes in for each "
               "tool number, in program order, plus the setup and what the "
               "program does while it runs. The plan is the part you need in "
               "your hand while swapping bits, and a dialog you already "
               "dismissed is not in your hand.",
    "pause":   "Pause after cutting any holes that lie OUTSIDE the board "
               "outline, so you can fit dowel pins before the rest of the job.\n\n"
               "A hole beyond your own Edge.Cuts can only be a fixture hole. On "
               "a ViaGrid blank there usually are none -- the blank arrives "
               "with its mounting holes drilled and the job is to locate to "
               "them, not to make them.",
}


class SettingsDialog(wx.Dialog):
    def __init__(self, parent, default_dir, default_depth):
        wx.Dialog.__init__(self, parent, title="Z1 drill & cutout g-code",
                           style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.ctrls = {}
        outer = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(self, label=INTRO)
        intro.Wrap(560)
        outer.Add(intro, 0, wx.ALL, 12)

        self.bit = wx.RadioBox(
            self, label="Bit in the spindle for holes",
            choices=["Corn / fishtail END MILL  (one bit, all sizes, helical)",
                     "TWIST DRILL  (one bit per size, pecked, never sideways)"],
            majorDimension=1, style=wx.RA_SPECIFY_COLS)
        self.bit.SetToolTip(TIPS["bit"])
        self.bit.Bind(wx.EVT_RADIOBOX, lambda _e: self._sync())
        outer.Add(self.bit, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        grid = wx.FlexGridSizer(0, 4, 6, 8)
        grid.AddGrowableCol(1, 1)

        self._dir_row(grid, "outdir", "Output folder", default_dir)
        self._num_row(grid, "depth", "Cut depth (mm)", default_depth,
                      "rpm", "Spindle (rpm)", 13000)
        grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1); grid.AddSpacer(1)

        self._num_row(grid, "dtool", "Hole tool #", 1, "ddia", "End mill dia (mm)", 0.7)
        self._num_row(grid, "dfeed", "Hole feed (mm/min)", 60,
                      "dplunge", "Hole plunge (mm/min)", 30)
        self._num_row(grid, "pitch", "Helix pitch (mm/turn)", 0.15,
                      "peck", "Peck depth (mm)", 0.3)
        self._num_row(grid, "maxchg", "Max bit changes", 6, None, None, None)
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
                ("drill", "Make the holes", True),
                ("cut", "Cut outline", True),
                ("pause", "Pause for dowel pins after registration holes", True),
                ("vias", "Also drill vias (NOT for ViaGrid blanks)", False),
                ("sheet", "Write a setup sheet (.txt) next to the g-code", True)):
            cb = wx.CheckBox(self, label=label)
            cb.SetValue(default)
            cb.SetToolTip(TIPS[key])
            self.ctrls[key] = cb
            box.Add(cb, 0, wx.TOP, 4)
        outer.Add(box, 0, wx.ALL, 12)

        self._sync()
        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        outer.Add(btns, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(outer)

    def _sync(self):
        """Grey out whatever the chosen bit type does not use.

        The two modes take different settings, and leaving the unused ones live
        invites tuning a number that is quietly ignored.
        """
        endmill = self.bit.GetSelection() == 0
        for key, on in (("ddia", endmill), ("pitch", endmill),
                        ("peck", not endmill), ("maxchg", not endmill)):
            self.ctrls[key].Enable(on)

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

    def _dir_row(self, grid, key, label, value):
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
        # The folder need not exist yet -- Production/ usually will not, and
        # write() creates it. So do not demand DD_DIR_MUST_EXIST.
        d = wx.DirDialog(self, "Write g-code to", defaultPath=tc.GetValue(),
                         style=wx.DD_DEFAULT_STYLE)
        if d.ShowModal() == wx.ID_OK:
            tc.SetValue(d.GetPath())
        d.Destroy()

    # -- results ---------------------------------------------------------
    def values(self):
        g = lambda k: self.ctrls[k].GetValue()
        num = lambda k: float(g(k))
        return dict(
            outdir=g("outdir").strip(),
            do_drill=g("drill"), do_cut=g("cut"),
            include_vias=g("vias"), pause_for_pins=g("pause"),
            write_sheet=g("sheet"),
            depth=num("depth"), rpm=int(num("rpm")),
            bit_type=("endmill", "drill")[self.bit.GetSelection()],
            hole_tool=int(num("dtool")), hole_dia=num("ddia"),
            hole_feed=num("dfeed"), hole_plunge=num("dplunge"),
            helix_pitch=num("pitch"), peck_depth=num("peck"),
            max_drill_changes=int(num("maxchg")),
            cut_tool=int(num("ctool")), cut_dia=num("cdia"),
            cut_feed=num("cfeed"), cut_plunge=num("cplunge"),
            cut_stepdown=num("step"),
            tab_count=int(num("tabs")), tab_width=num("tabw"),
            tab_z=num("tabz"),
        )
