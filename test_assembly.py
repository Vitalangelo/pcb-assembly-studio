"""Regression checks for installation stages, ordering, preview and PDF export."""
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import pcb_assembly_studio as app


def part(ref, footprint, side="Bottom"):
    return app.Component(ref, side, 10, 10, 0, footprint=footprint)


def group(*refs, tech="", dni=False):
    return app.ComponentGroup("Part", "", list(refs), len(refs), tech=tech, dni=dni)


class AssemblyTests(unittest.TestCase):
    def test_real_reported_packages_and_unknown_size(self):
        diode = part("D1", "POWERMITE")
        trimmer = part("R95", "3224W")
        # No manufacturer catalogue: names alone do not supply body geometry.
        self.assertEqual(diode.size_source, "unknown")
        self.assertEqual(trimmer.size_source, "unknown")
        pnp = {"R1": part("R1", "unknown"), "R2": part("R2", "RES2512")}
        gs = app.assembly_groups({"Top": [group("R1"), group("R2")]}, pnp)
        self.assertEqual(gs["Top"][0].designators, ["R2"])
        self.assertEqual(part("L1", "WE-PD 1210").size_source, "unknown")

    def test_jumper_header_and_solder_bridge_mount(self):
        g = group("J2")
        g.comment = "Pin Header & Jumper"
        g.description = "3-pin"
        self.assertEqual(app.component_stage(part("J2", "61300311121"), g), "THT")
        self.assertEqual(app.component_stage(part("J2", "Header_SMD"), g), "SMT")
        self.assertEqual(app.component_stage(part("S1", "DIP4SW"), group("S1")), "THT")
        g.comment = "Solder jumper"
        self.assertEqual(app.component_stage(part("J2", "SolderJumper_2"), g), "SMT")

    def test_exclusion_and_restore_preserve_other_components(self):
        pnp = {d: part(d, "0402") for d in ("J1", "R1")}
        original = {"Top": [group("J1", "R1")]}
        gs = app.assembly_groups(original, pnp, excluded={"J1"})
        self.assertEqual(gs["Top"][0].designators, ["R1"])
        self.assertEqual(gs["Top"][0].quantity, 1)
        restored = app.assembly_groups(original, pnp)
        self.assertEqual({d for g in restored["Top"] for d in g.designators}, {"J1", "R1"})
        self.assertEqual(app.assembly_sections(app.assembly_groups(original, pnp, excluded=pnp)), [])

    def test_size_override_validation_and_sorting(self):
        pnp = {"R1": part("R1", "unknown"), "R2": part("R2", "RES0603")}
        sizes = {"R1": [5, 4], "bad": [-1, 2], "nan": [float("nan"), 2], "bad2": "oops"}
        self.assertEqual(app.valid_size_overrides(sizes), {"R1": [5, 4]})
        app.apply_size_overrides(pnp, sizes)
        self.assertEqual((pnp["R1"].w, pnp["R1"].h, pnp["R1"].size_source), (5, 4, "manual"))
        gs = app.assembly_groups({"Top": [group("R1"), group("R2")]}, pnp)
        self.assertEqual(gs["Top"][0].designators, ["R2"])

    def test_size_order_interleaves_types_and_splits_mixed_sizes(self):
        pnp = {d: part(d, fp) for d, fp in
               [("R1", "R_0603"), ("C1", "C_0402"), ("C2", "C_0805"), ("R2", "R_0402")]}
        gs = app.assembly_groups({"Bottom": [group("R1", "R2"), group("C1", "C2")]}, pnp)
        self.assertEqual([g.designators for g in gs["Bottom"]], [["C1"], ["R2"], ["R1"], ["C2"]])

    def test_footprint_beats_value_and_pnp_preserves_package(self):
        c = part("C1", "C_0805")
        c.description = "0402 alternative"
        c._estimate_size()
        self.assertEqual((c.w, c.h), (2, 1.25))
        c = part("U1", "USON_2.5x1.0mm")
        self.assertEqual((c.w, c.h), (2.5, 1))
        parsed = app._parse_pnp_delimited([
            "Designator,Layer,Center-X,Center-Y,Footprint", "C1,Bottom,1,2,C_0603"], ",", 1)
        self.assertEqual(parsed["C1"].footprint, "C_0603")

    def test_mount_detection_respects_explicit_smt_connector(self):
        c = part("J1", "Connector_SMD")
        g = group("J1")
        g.description = "Connector"
        self.assertEqual(app.component_stage(c, g), "SMT")
        self.assertEqual(app.component_stage(part("J1", "Connector_THT"), g), "THT")
        self.assertEqual(app.component_stage(c, group("J1", tech="Through Hole")), "THT")

    def test_deferred_part_keeps_board_side_and_is_not_duplicated(self):
        pnp = {d: part(d, "0402") for d in ("C1", "C2")}
        gs = app.assembly_groups({"Bottom": [group("C1", "C2")]}, pnp, {"C1": "Manual"})
        sections = app.assembly_sections(gs)
        self.assertEqual([s for s, _ in sections], ["SMT", "THT / Manual"])
        self.assertEqual(sections[-1][1][0][0], "Bottom")
        self.assertEqual([d for _, sheets in sections for _, _, gwc in sheets
                          for g, _, _ in gwc for d in g.designators], ["C2", "C1"])

    def test_manual_order_splits_only_moved_part(self):
        pnp = {d: part(d, "0402") for d in ("R1", "R2", "R3", "C1")}
        source = {"Bottom": [group("R1", "R2", "R3"), group("C1")]}
        gs = app.assembly_groups(source, pnp, order=["R1", "R2", "C1", "R3"])
        self.assertEqual([g.designators for g in gs["Bottom"]], [["R1", "R2"], ["C1"], ["R3"]])

    def fixture(self):
        pnp = {}
        source = {"Bottom": [], "Top": []}
        for side in source:
            for i in range(app.ROWS_PER_PAGE + 1):
                d = f"{'R' if side == 'Bottom' else 'C'}{i+1}"
                pnp[d] = part(d, "0402", side)
                source[side].append(group(d))
            for suffix, tech, dni in (("1", "THT", False), ("2", "SMT", True)):
                d = side + suffix
                pnp[d] = part(d, "DIP-8" if tech == "THT" else "0402", side)
                source[side].append(group(d, tech=tech, dni=dni))
        return pnp, app.assembly_groups(source, pnp)

    def test_sections_have_separate_dni_sheets_and_match_preview(self):
        pnp, gs = self.fixture()
        sections = app.assembly_sections(gs)
        self.assertEqual([len(sheets) for _, sheets in sections], [3, 3, 2])
        a = app.App.__new__(app.App)
        a.root = Mock()
        a._reset_project()
        a.main_built = True
        a.pnp, a.groups = pnp, gs
        a.board = Mock()
        a.status = Mock()
        a._build_left = Mock()
        a._build_legend = Mock()
        for stage, side, page, expected in (("SMT", "Bottom", 2, "3 / 3"),
                                             ("THT / Manual", "Top", 0, "2 / 2")):
            a.stage, a.side, a.page = stage, side, page
            a._refresh()
            self.assertEqual(a.preview_number, expected)
            self.assertEqual(a.board.load.call_args.args[3],
                             {d for g in a.preview_groups for d in g.designators})

    def test_pdf_numbers_restart_and_real_pdf_is_written(self):
        pnp, gs = self.fixture()
        calls = []
        original = app.draw_title_block

        def capture(sheet, meta, page, total, subtitle, scale):
            calls.append((page, total, subtitle))
            return original(sheet, meta, page, total, subtitle, scale)

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "assembly.pdf")
            with patch.object(app, "draw_title_block", side_effect=capture):
                self.assertEqual(app.generate_pdf(pnp, gs, path), 8)
            with open(path, "rb") as f:
                self.assertEqual(f.read(4), b"%PDF")
            self.assertEqual([(p, t) for p, t, _ in calls],
                             [(1, 3), (2, 3), (3, 3), (1, 3), (2, 3), (3, 3), (1, 2), (2, 2)])

    def test_project_round_trip_preserves_installation_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "board.pcbasm")
            data = {"format": 1, "files": {}, "mount_overrides": {"C1": "Manual"},
                    "assembly_order": ["R2", "C1", "R1"]}
            app.write_project(path, data)
            self.assertEqual(app.read_project(path), data)


if __name__ == "__main__":
    unittest.main()
