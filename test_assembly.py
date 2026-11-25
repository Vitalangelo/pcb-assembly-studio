"""Regression checks for installation stages, ordering, preview and PDF export."""
import os
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch

import pcb_assembly_studio as app


def part(ref, footprint, side="Bottom"):
    return app.Component(ref, side, 10, 10, 0, footprint=footprint)


def group(*refs, tech="", dni=False):
    return app.ComponentGroup("Part", "", list(refs), len(refs), tech=tech, dni=dni)


class AssemblyTests(unittest.TestCase):
    def test_partial_paste_evidence_does_not_split_identical_bom_parts(self):
        pnp = {d: part(d, 'C0603') for d in ('C19', 'C26', 'C28', 'C54')}
        pnp['C26'].sort_size = (2.1, 0.8)
        pnp['C28'].sort_size = (2.1, 0.8)
        pnp['C54'].sort_size = (2.1001, 0.8)
        gs = app.assembly_groups({'Top': [group(*pnp)]}, pnp)
        self.assertEqual(len(gs['Top']), 1)
        self.assertEqual(gs['Top'][0].designators, list(pnp))
        self.assertEqual(gs['Top'][0].quantity, 4)

    def test_distinct_footprints_still_split_even_with_same_body_size(self):
        pnp = {'C1': part('C1', 'custom-A'), 'C2': part('C2', 'custom-B')}
        gs = app.assembly_groups({'Top': [group('C1', 'C2')]}, pnp)
        self.assertEqual(len(gs['Top']), 2)

    def test_manual_size_and_installation_overrides_still_split(self):
        pnp = {d: part(d, 'C0603') for d in ('C1', 'C2', 'C3')}
        app.apply_size_overrides(pnp, {'C2': [4, 3]})
        gs = app.assembly_groups({'Top': [group(*pnp)]}, pnp, {'C3': 'Manual'})
        self.assertEqual(len(gs['Top']), 3)
        self.assertEqual(next(g for g in gs['Top'] if 'C3' in g.designators).stage, 'Manual')

    def test_legend_uses_pdf_part_number_and_technical_data(self):
        g = group('C1')
        g.comment = ''
        g.description = 'Old preview description'
        g.part_no, g.tech = 'PART-123', 'CAP MLCC 0.1uF 50V 10% 0603'
        self.assertEqual(app.legend_fields(g), (g.part_no, g.tech))
        g.part_no, g.tech = '', ''
        g.comment = 'Fallback value'
        self.assertEqual(app.legend_fields(g), ('Fallback value', g.description))

    def test_native_legend_widgets_show_part_number_and_full_technical_tooltip(self):
        try:
            root = app.tk.Tk()
        except app.tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        a = app.App.__new__(app.App)
        a.root, a.board = root, Mock()
        a.right_panel = app.tk.Frame(root, width=320, height=600)
        g = group('C1', 'C2')
        g.comment, g.part_no, g.tech = '', 'PART-123', 'CAP MLCC 0.1uF 50V 10% 0603'
        a.side, a.view, a.page = 'Top', None, 0
        a.shown, a.preview_groups = {'Top': [g]}, [g]
        a.preview_sheets, a.preview_number, a.preview_start = [None], '1 / 1', 0
        a._build_legend()
        labels = [w for w in a.legend_rows[0].winfo_children() if isinstance(w, app.tk.Label)]
        self.assertIn(g.part_no, [w.cget('text') for w in labels])
        technical = next(w for w in labels if w.cget('text') == g.tech)
        self.assertEqual(int(technical.cget('height')), 2)
        # Board highlighting must not overwrite the full-text tooltip bindings.
        self.assertIn('show', technical.bind('<Enter>'))
        self.assertIn('<lambda>', technical.bind('<Enter>'))
        root.update_idletasks()

    def test_long_technical_description_is_preserved(self):
        description = 'MLCC 22uF 25V X5R 0805, CAP MLCC 22uF 25V 20% 0805, Cap MLCC - SMD/SMT 22uF 25VDC 20% 0805 X5R'
        row = app._bom_row_to_result('GRM21BR61E226ME44K', description, '', 'C1', 'Top')
        self.assertEqual(row['tech'], description)
        row = app._bom_row_to_result('part', '  MLCC\n 22uF\t25V  ', '', 'C1', 'Top')
        self.assertEqual(row['tech'], 'MLCC 22uF 25V')

    def test_technical_data_wraps_and_shrinks_inside_cell(self):
        sheet = app.Sheet('A4', True)
        try:
            g = group('C1')
            g.tech = 'MLCC 22uF 25V X5R 0805, CAP MLCC 22uF 25V 20% 0805, ' * 10
            app.draw_parts_list(sheet, [(g, app.PALETTE[0], 1)], sheet.lay['rowh'])
            x = sheet.lay['parts_list'][0] + app.PARTS_LIST_COLS[3] + 1.4
            header_y = sheet.lay['parts_list'][1] + sheet.lay['rowh'] * app.ROWS_PER_PAGE
            labels = [t for t in sheet.ov.texts if abs(t.get_position()[0]-x) < 1e-6
                      and t.get_position()[1] < header_y]
            self.assertGreater(len(labels), 1)
            self.assertTrue(labels[-1].get_text().endswith('…'))
            self.assertLess(labels[0].get_fontsize(), app.mm_font(app.H_SMALL))
            for label in labels:
                h = label.get_fontsize() / app.mm_font(1)
                self.assertLessEqual(sheet.measure(label.get_text(), h), 43.2 + 1e-6)
        finally:
            app.plt.close(sheet.fig)

    def test_pdf_column_width_transfer(self):
        cols = app.PARTS_LIST_COLS
        self.assertAlmostEqual(cols[2]-cols[1], 58 * 2/3)
        self.assertAlmostEqual(cols[6]-cols[5], 14 + 58/3)
        self.assertAlmostEqual(cols[3]-cols[2], 38)
        self.assertAlmostEqual(cols[4]-cols[3], 46)

    def test_cell_fitting_wraps_and_reduces_before_truncating(self):
        measure = lambda text, font: len(text) * font * 0.5
        text = 'one two three four five'
        lines, font = app.fit_cell_text(text, 12, 4, measure)
        self.assertEqual(' '.join(lines), text)
        self.assertLess(font, app.H_SMALL)
        self.assertTrue(all(measure(line, font) <= 12 for line in lines))
        self.assertLessEqual(len(lines) * font * 1.25, 4)
        lines, font = app.fit_cell_text('superlongwordwithoutspaces' * 10, 12, 4, measure)
        self.assertTrue(lines[-1].endswith('…'))
        self.assertTrue(all(measure(line, font) <= 12 for line in lines))

    def test_remarks_split_only_parts_with_different_notes_and_survive_page_filter(self):
        pnp = {d: part(d, 'C0603') for d in ('C1','C2','C3')}
        notes = {'C1': 'Fit after test', 'C2': 'Fit after test', 'C3': 'Do not bend'}
        gs = app.assembly_groups({'Top':[group(*pnp)]}, pnp, remarks=notes)
        self.assertEqual(len(gs['Top']), 2)
        g = next(g for g in gs['Top'] if 'C1' in g.designators)
        self.assertEqual(g.designators, ['C1','C2'])
        self.assertEqual(g.remarks, 'Fit after test')
        filtered = app.page_groups(gs, ['C2'])
        self.assertEqual(filtered['Top'][0].remarks, 'Fit after test')
        self.assertEqual(app.valid_component_remarks({'C1':' hi\n there ','C2':'','bad':None}),
                         {'C1':'hi\n there'})

    def test_installation_editor_edits_remarks_and_cancel_discards_them(self):
        try:
            root = app.tk.Tk()
        except app.tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        a = app.App.__new__(app.App)
        a.root = root
        a.main_built = False
        a._reset_project()
        a.pnp_parsed = {d:part(d,'C0603') for d in ('C1','C2')}
        a._regroup()

        def descendants(widget):
            return [child for w in widget.winfo_children() for child in [w]+descendants(w)]

        def button(widget, text):
            return next(w for w in descendants(widget) if isinstance(w, app.ttk.Button) and w.cget('text') == text)

        def edit():
            a._assembly_dialog()
            dialog = next(w for w in root.winfo_children() if isinstance(w, app.tk.Toplevel))
            tree = next(w for w in descendants(dialog) if isinstance(w, app.ttk.Treeview))
            self.assertIn('part_no', tree['columns'])
            tree.selection_set('C1')
            button(dialog, 'Edit remarks…').invoke()
            editor = next(w for w in dialog.winfo_children() if isinstance(w, app.tk.Toplevel))
            entry = next(w for w in descendants(editor) if isinstance(w, app.tk.Text))
            entry.insert('1.0', 'Fit after test\nCheck polarity')
            button(editor, 'Save remark').invoke()
            self.assertEqual(tree.set('C1','remarks'), 'Fit after test\nCheck polarity')
            return dialog

        dialog = edit()
        button(dialog, 'Cancel').invoke()
        self.assertEqual(a.component_remarks, {})
        dialog = edit()
        button(dialog, 'Apply').invoke()
        self.assertEqual(a.component_remarks, {'C1':'Fit after test\nCheck polarity'})
        self.assertEqual(next(g for g in a.groups['Bottom'] if 'C1' in g.designators).remarks,
                         'Fit after test\nCheck polarity')
        with tempfile.TemporaryDirectory() as folder:
            a.project_path = os.path.join(folder,'notes.pcbasm')
            self.assertTrue(a._save_project())
            saved = app.read_project(a.project_path)
            self.assertEqual(app.valid_component_remarks(saved['component_remarks']), a.component_remarks)

    def test_export_publishes_finished_file_and_sends_progress(self):
        a = app.App.__new__(app.App)
        a.export_events = queue.Queue()
        with tempfile.TemporaryDirectory() as folder:
            destination = os.path.join(folder, 'drawing.pdf')
            with open(destination, 'wb') as f:
                f.write(b'previous drawing')

            def render(pnp, groups, temporary, **kwargs):
                with open(destination, 'rb') as f:
                    self.assertEqual(f.read(), b'previous drawing')
                with open(temporary, 'wb') as f:
                    f.write(b'%PDF-completed')
                kwargs['progress_callback'](1, 2)
                kwargs['progress_callback'](2, 2)
                return 2

            with patch.object(app, 'generate_pdf', side_effect=render):
                a._export_worker(destination, {}, {}, '', {}, None)
            self.assertEqual(a.export_events.get_nowait(), ('progress', 1, 2))
            self.assertEqual(a.export_events.get_nowait(), ('progress', 2, 2))
            self.assertEqual(a.export_events.get_nowait(), ('done', destination, 2, None))
            with open(destination, 'rb') as f:
                self.assertEqual(f.read(), b'%PDF-completed')
            self.assertEqual(os.listdir(folder), ['drawing.pdf'])

    def test_failed_export_preserves_old_pdf_and_removes_temporary(self):
        a = app.App.__new__(app.App)
        a.export_events = queue.Queue()
        with tempfile.TemporaryDirectory() as folder:
            destination = os.path.join(folder, 'drawing.pdf')
            with open(destination, 'wb') as f:
                f.write(b'previous drawing')
            with patch.object(app, 'generate_pdf', side_effect=RuntimeError('Render failed')):
                a._export_worker(destination, {}, {}, '', {}, None)
            self.assertEqual(a.export_events.get_nowait(), ('done', destination, 0, 'Render failed'))
            with open(destination, 'rb') as f:
                self.assertEqual(f.read(), b'previous drawing')
            self.assertEqual(os.listdir(folder), ['drawing.pdf'])

    def test_empty_export_does_not_publish_empty_file(self):
        a = app.App.__new__(app.App)
        a.export_events = queue.Queue()
        with tempfile.TemporaryDirectory() as folder:
            destination = os.path.join(folder, 'drawing.pdf')
            with patch.object(app, 'generate_pdf', return_value=0):
                a._export_worker(destination, {}, {}, '', {}, None)
            self.assertEqual(a.export_events.get_nowait(), ('done', destination, 0, None))
            self.assertEqual(os.listdir(folder), [])

    def test_export_error_closes_progress_and_restores_export_button(self):
        a = app.App.__new__(app.App)
        a.exporting = True
        a.root, a.status, a.export_btn = Mock(), Mock(), Mock()
        a.export_dialog, a.export_progress = Mock(), Mock()
        dialog = a.export_dialog
        a.export_poll_id = 'timer'
        with patch.object(app, 'messagebox') as messages, patch.object(a, '_set_btn_enabled') as enable:
            a._export_done('drawing.pdf', 0, 'Render failed')
        self.assertFalse(a.exporting)
        self.assertIsNone(a.export_dialog)
        a.export_progress.stop.assert_called_once()
        dialog.grab_release.assert_called_once()
        dialog.destroy.assert_called_once()
        enable.assert_called_once_with(a.export_btn, True)
        a.root.after_cancel.assert_called_once_with('timer')
        messages.showerror.assert_called_once_with('Error', 'Render failed')

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
