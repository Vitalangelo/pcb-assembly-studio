"""Offline regression tests, independent of the GUI and component names."""
import unittest
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

from assembly_geometry import infer_geometry, load_drill_evidence, layer_role


def component(x=0, y=0, rotation=0, footprint='custom', layer='Top'):
    return SimpleNamespace(x=x, y=y, rotation=rotation, footprint=footprint, layer=layer)


def pad(x, y, width=1.8, height=1.8):
    return {'x': x, 'y': y, 'bounds': ((x-width/2,y-height/2),(x+width/2,y+height/2))}


def evidence(points):
    return {'layers': {'copper_top': [pad(x,y) for x,y in points],
                       'copper_bottom': [pad(x,y) for x,y in points]}}


def drills(points, diameter=1, plated=True):
    return {'holes': [{'x': x, 'y': y, 'diameter': diameter, 'plated': plated} for x,y in points]}


class GeometryTests(unittest.TestCase):
    def test_combined_archive_discovers_nested_drills_and_ignores_reports(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'manufacturing.zip'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('board.gtl','G04 artwork*')
                archive.writestr('notes.txt','Some documentation')
                for name,plating in [('PTH.TXT','PLATED'),('NPTH.drl','NON_PLATED')]:
                    archive.writestr('drills/'+name,
                        f'M48\n;FILE_FORMAT=4:4\nMETRIC,LZ\n;TYPE={plating}\nT1C1.000\n%\nT01\nX00010000Y00020000\nM30\n')
            result=load_drill_evidence(path,required=False)
            self.assertEqual(len(result['holes']),2)
            self.assertEqual({h['plated'] for h in result['holes']},{True,False})
            self.assertFalse(result['warnings'])

    def test_archive_without_drills_returns_review_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'gerber.zip'
            with zipfile.ZipFile(path,'w') as archive:
                archive.writestr('board.gtl','G04 artwork*')
            result=load_drill_evidence(path,required=False)
            self.assertFalse(result['holes'])
            self.assertTrue(result['warnings'])

    def test_generic_header_without_part_number(self):
        points = [(-2.54,0),(0,0),(2.54,0)]
        result = infer_geometry({'ANY': component()}, evidence(points), drills(points))
        self.assertEqual(result['ANY']['stage'], 'THT')

    def test_rectangle_pins_are_not_assigned_to_closer_neighbours(self):
        points = [(-1,-4),(1,-4),(-1,4),(1,4)]
        comps = {'CUSTOM': component(), 'SMALL': component(1,5)}
        result = infer_geometry(comps,evidence(points),drills(points))
        self.assertEqual(result['CUSTOM']['stage'], 'THT')
        self.assertNotIn('stage', result['SMALL'])

    def test_npth_unknown_plating_and_small_holes_are_not_tht(self):
        points=[(-1,0),(1,0)]
        for holes in (drills(points,plated=False), drills(points,plated=None), drills(points,diameter=0.3)):
            self.assertNotIn('stage',infer_geometry({'A':component()},evidence(points),holes)['A'])

    def test_requires_both_outer_copper_layers_and_matching_origin(self):
        points=[(-1,0),(1,0)]
        e=evidence(points)
        e['layers'].pop('copper_bottom')
        self.assertNotIn('stage',infer_geometry({'A':component()},e,drills(points))['A'])
        self.assertNotIn('stage',infer_geometry({'A':component(100,100)},evidence(points),drills(points))['A'])

    def test_duplicate_and_contradictory_drills(self):
        points=[(-1,0),(1,0)]
        duplicated={'holes':drills(points)['holes']*2}
        self.assertEqual(infer_geometry({'A':component()},evidence(points),duplicated)['A']['stage'],'THT')
        contradictory={'holes':drills(points)['holes']+drills(points,plated=False)['holes']}
        self.assertNotIn('stage',infer_geometry({'A':component()},evidence(points),contradictory)['A'])

    def test_overlapping_components_are_ambiguous(self):
        points=[(-1,0),(1,0)]
        result=infer_geometry({'A':component(), 'B':component()},evidence(points),drills(points))
        self.assertFalse(any('stage' in info for info in result.values()))

    def test_repeatable_paste_area_is_not_called_body_size(self):
        e={'layers':{'paste_top':[pad(-1,0,0.5,1),pad(1,0,0.5,1),pad(9,0,0.5,1),pad(11,0,0.5,1)]}}
        result=infer_geometry({'A':component(), 'B':component(10)},e)
        self.assertEqual(result['A']['sort_size'],(2.5,1))
        self.assertIn('paste area',result['A']['size_source'])
        self.assertNotIn('stage',result['A'])

    def test_singleton_or_incomplete_paste_is_not_guessed(self):
        e={'layers':{'paste_top':[pad(-1,0),pad(1,0)]}}
        self.assertNotIn('sort_size',infer_geometry({'A':component()},e)['A'])
        e['layers']['paste_top'] += [pad(9,0)]
        result=infer_geometry({'A':component(), 'B':component(10)},e)
        self.assertNotIn('sort_size',result['A'])

    def test_rotation_and_side_are_respected(self):
        e={'layers':{'paste_top':[pad(-1,0,.5,1),pad(1,0,.5,1),pad(10,-1,1,.5),pad(10,1,1,.5)]}}
        result=infer_geometry({'A':component(), 'B':component(10,rotation=90)},e)
        self.assertAlmostEqual(result['B']['sort_size'][0],2.5)
        result=infer_geometry({'A':component(layer='Bottom'), 'B':component(10,rotation=90)},e)
        self.assertNotIn('sort_size',result['A'])

    def test_generic_layer_names_and_attributes(self):
        self.assertEqual(layer_role('board-F_Cu.gbr'),'copper_top')
        self.assertEqual(layer_role('unknown.gbr',{'.FileFunction':('SolderPaste','Bot')}),'paste_bottom')

    def test_real_excellon_units_and_plating(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'holes.drl'
            path.write_text('M48\n;FILE_FORMAT=4:4\nMETRIC,LZ\n;TYPE=PLATED\nT1C1.000\n%\nT01\nX00010000Y00020000\nM30\n')
            result=load_drill_evidence(path)
            self.assertEqual(len(result['holes']),1)
            self.assertEqual((result['holes'][0]['x'],result['holes'][0]['y']),(1,2))
            self.assertTrue(result['holes'][0]['plated'])

    def test_inch_drills_are_converted_to_mm(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'holes.drl'
            path.write_text('M48\n;FILE_FORMAT=2:4\nINCH,LZ\n;TYPE=PLATED\nT1C0.040\n%\nT01\nX010000Y020000\nM30\n')
            hole=load_drill_evidence(path)['holes'][0]
            self.assertAlmostEqual(hole['x'],25.4)
            self.assertAlmostEqual(hole['y'],50.8)
            self.assertAlmostEqual(hole['diameter'],1.016)


if __name__ == '__main__':
    unittest.main()
