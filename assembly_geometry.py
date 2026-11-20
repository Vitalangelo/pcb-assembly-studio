"""Conservative, offline assembly evidence from Gerber and Excellon geometry.

No component/manufacturer catalogue is used. Pad envelopes are placement-area
estimates, never measurements of the body. Ambiguous associations are rejected.
"""
import math
import os
import re
import zipfile
from collections import defaultdict


def layer_role(name, attrs=None):
    text = str((attrs or {}).get('.FileFunction', '')).lower()
    side = 'top' if 'top' in text else 'bottom' if 'bot' in text else None
    if side:
        for token, role in [('solderpaste', 'paste'), ('soldermask', 'mask'), ('copper', 'copper')]:
            if token in text:
                return role + '_' + side
    low = os.path.basename(name).lower()
    ext = os.path.splitext(low)[1]
    roles = {'.gtp': 'paste_top', '.gbp': 'paste_bottom', '.gts': 'mask_top',
             '.gbs': 'mask_bottom', '.gtl': 'copper_top', '.gbl': 'copper_bottom'}
    if ext in roles:
        return roles[ext]
    for tokens, role in [(('f_paste', 'f.paste'), 'paste_top'), (('b_paste', 'b.paste'), 'paste_bottom'),
                         (('f_mask', 'f.mask'), 'mask_top'), (('b_mask', 'b.mask'), 'mask_bottom'),
                         (('f_cu', 'f.cu'), 'copper_top'), (('b_cu', 'b.cu'), 'copper_bottom')]:
        if any(token in low for token in tokens):
            return role
    return None


def load_gerber_evidence(path):
    from gerbonara import GerberFile, MM
    from gerbonara.graphic_objects import Flash
    result = {'layers': {}, 'warnings': []}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.lower().endswith(('.gtp', '.gbp', '.gts', '.gbs', '.gtl', '.gbl', '.gbr', '.ger')):
                continue
            try:
                data = archive.read(name).decode('utf-8-sig', errors='replace')
                gf = GerberFile.from_string(data, filename=name)
                attrs = dict(gf.file_attrs)
                for key, value in re.findall(r'TF\.(FileFunction|FilePolarity),([^*%\r\n]+)', data):
                    attrs.setdefault('.' + key, tuple(value.split(',')))
                role = layer_role(name, attrs)
                if not role:
                    continue
                if 'negative' in str(attrs.get('.FilePolarity', '')).lower():
                    # Negative mask artwork is not needed by this inference pass.
                    if not role.startswith('mask_'):
                        result['warnings'].append('Unsupported negative geometry: ' + name)
                    continue
                features = []
                later_clear = []
                for obj in reversed(gf.objects):
                    if not obj.polarity_dark:
                        later_clear.append(obj.bounding_box(unit=MM))
                        continue
                    if not isinstance(obj, Flash):
                        continue
                    obj = obj.converted(MM)
                    bounds = obj.bounding_box(unit=MM)
                    # A later clear operation can remove this pad. Without full
                    # boolean rendering, reject any pad its bounds might touch.
                    if any(bounds[0][0] <= b[1][0] and bounds[1][0] >= b[0][0] and
                           bounds[0][1] <= b[1][1] and bounds[1][1] >= b[0][1] for b in later_clear):
                        continue
                    ref = (obj.attrs.get('.P') or obj.attrs.get('.C') or ())
                    if isinstance(ref, str):
                        ref = (ref,)
                    features.append({'x': obj.x, 'y': obj.y, 'bounds': bounds,
                                     'ref': ref[0] if ref else None})
                result['layers'].setdefault(role, []).extend(features)
            except Exception as exc:
                result['warnings'].append('Could not read %s: %s' % (name, exc))
    return result


def load_drill_evidence(path, required=True):
    from gerbonara import ExcellonFile, MM
    from gerbonara.graphic_objects import Flash
    holes, warnings = [], []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            sources = [(name, archive.read(name).decode('utf-8-sig', errors='replace'))
                       for name in archive.namelist()
                       if name.lower().endswith(('.txt', '.drl', '.xln', '.exc', '.tap'))]
    else:
        with open(path, encoding='utf-8-sig', errors='replace') as source:
            sources = [(os.path.basename(path), source.read())]
    found = False
    for name, data in sources:
        if not re.search(r'(?m)^\s*M48\b', data):
            continue
        found = True
        try:
            # No guessed zero suppression, units, coordinate format or plating.
            drill = ExcellonFile.from_string(data, filename=name)
            for obj in drill.objects:
                if not isinstance(obj, Flash):
                    warnings.append('Routed slots need review: ' + name)
                    continue
                obj = obj.converted(MM)
                holes.append({'x': obj.x, 'y': obj.y, 'diameter': MM(obj.tool.diameter, obj.tool.unit),
                              'plated': obj.tool.plated})
        except Exception as exc:
            if required:
                raise ValueError('Could not safely read drill file %s: %s' % (name, exc)) from exc
            warnings.append('Could not safely read drill file %s: %s' % (name, exc))
    if not found:
        message = 'No NC drill coordinates found in archive; geometry-based THT detection is unavailable.'
        if required:
            raise ValueError('No supported Excellon drill data found. Load coordinate files, not DRR/LDP reports.')
        warnings.append(message)
    if any(h['plated'] is None for h in holes):
        warnings.append('Unspecified plating: these holes are not used for automatic THT detection.')
    unique = {(round(h['x'],6), round(h['y'],6), round(h['diameter'],6), h['plated']): h for h in holes}
    return {'holes': list(unique.values()), 'warnings': list(dict.fromkeys(warnings))}


def _distance(a, b):
    return math.hypot(a['x'] - b['x'], a['y'] - b['y'])


def _envelope(features, comp):
    angle = math.radians(comp.rotation)
    co, si = math.cos(angle), math.sin(angle)
    points = []
    for feature in features:
        (x0, y0), (x1, y1) = feature['bounds']
        for x, y in ((x0, y0), (x0, y1), (x1, y0), (x1, y1)):
            x, y = x - comp.x, y - comp.y
            points.append((x * co + y * si, -x * si + y * co))
    lo_x, hi_x = min(p[0] for p in points), max(p[0] for p in points)
    lo_y, hi_y = min(p[1] for p in points), max(p[1] for p in points)
    return hi_x - lo_x, hi_y - lo_y, (lo_x + hi_x) / 2, (lo_y + hi_y) / 2


def infer_geometry(components, evidence, drills=None):
    """Return per-reference evidence; reject rather than force uncertain matches.

    Coordinates must already agree. No automatic translation, mirroring or
    scale correction is attempted. Manual settings are applied by the caller.
    """
    layers = (evidence or {}).get('layers', {})
    decisions = {d: {'review': []} for d in components}
    all_holes = (drills or {}).get('holes', [])
    nonplated = [h for h in all_holes if h['plated'] is False]
    unique = {(round(h['x'],4),round(h['y'],4),round(h['diameter'],4)): h
              for h in all_holes if h['plated'] is True}
    holes = [h for h in unique.values() if not any(_distance(h, n) <= 0.025 for n in nonplated)]
    # Match PTH holes to annular copper pads on BOTH outer layers. Small holes
    # are deliberately left unclassified: diameter alone never proves a via.
    eligible = []
    for hole in holes:
        if hole['diameter'] < 0.5:
            continue
        matches = []
        for side in ('top', 'bottom'):
            pads = [p for p in layers.get('copper_' + side, []) if _distance(hole, p) <= 0.025]
            pads = [p for p in pads if min(p['bounds'][1][0] - p['bounds'][0][0],
                                          p['bounds'][1][1] - p['bounds'][0][1]) > hole['diameter'] + 0.1]
            matches.append(pads)
        if all(matches):
            eligible.append(hole)

    candidates = {}
    for d, c in components.items():
        center = {'x': c.x, 'y': c.y}
        nearby = [i for i, h in enumerate(eligible) if _distance(h, center) <= 12]
        selected = set()
        for i in nearby:
            h = eligible[i]
            reflected = {'x': 2 * c.x - h['x'], 'y': 2 * c.y - h['y']}
            partners = [j for j in nearby if i != j and _distance(eligible[j], reflected) <= 0.06]
            if len(partners) == 1:
                selected.update((i, partners[0]))
        if len(selected) < 2:
            continue
        # Add a pin at the exact placement center, e.g. a 3-pin header.
        selected.update(i for i in nearby if _distance(eligible[i], center) <= 0.025)
        coords = [(eligible[i]['x'], eligible[i]['y']) for i in selected]
        # Accept only a straight row or rectangular perimeter, in local axes.
        angle = math.radians(c.rotation)
        local = [((x-c.x)*math.cos(angle)+(y-c.y)*math.sin(angle),
                  -(x-c.x)*math.sin(angle)+(y-c.y)*math.cos(angle)) for x,y in coords]
        xs, ys = [p[0] for p in local], [p[1] for p in local]
        x0,x1,y0,y1 = min(xs),max(xs),min(ys),max(ys)
        row = x1-x0 < 0.1 or y1-y0 < 0.1
        rectangle = len(selected) >= 4 and all(min(abs(x-x0), abs(x-x1), abs(y-y0), abs(y-y1)) < 0.06 for x,y in local)
        if not (row or rectangle):
            continue
        # A nearby unrelated SMT part must not inherit this group. A second
        # component center inside the envelope makes association ambiguous.
        if any(other != d and min(x for x,y in coords)-0.05 <= oc.x <= max(x for x,y in coords)+0.05
               and min(y for x,y in coords)-0.05 <= oc.y <= max(y for x,y in coords)+0.05
               for other, oc in components.items() if oc.layer == c.layer):
            decisions[d]['review'].append('Ambiguous drill group')
            continue
        candidates[d] = selected
    for d, indices in candidates.items():
        if any(indices & other for ref, other in candidates.items() if ref != d):
            decisions[d]['review'].append('Drill group shared by components')
            continue
        decisions[d].update(stage='THT', mount_source='PTH group + copper (inferred)')

    # Paste envelopes require an unambiguous nearest assignment, a plausible
    # center, and repeatability across instances of the same footprint.
    footprints = defaultdict(list)
    for side in ('Top', 'Bottom'):
        comps = [(d,c) for d,c in components.items() if c.layer == side]
        owned, ambiguous = defaultdict(list), set()
        for pad in layers.get('paste_' + side.lower(), []):
            distances = sorted((math.hypot(pad['x']-c.x, pad['y']-c.y), d) for d,c in comps)
            if not distances:
                continue
            if len(distances) > 1 and distances[1][0] - distances[0][0] < 0.3:
                ambiguous.add(distances[0][1])
                continue
            owned[distances[0][1]].append(pad)
        for d,c in comps:
            pads = owned[d]
            if d in ambiguous or len(pads) < 2 or not c.footprint:
                continue
            w,h,dx,dy = _envelope(pads,c)
            if not (0.1 < min(w,h) and max(w,h) < 30 and math.hypot(dx,dy) <= 0.25*min(w,h)):
                continue
            if any(other != d and all(min(p['bounds'][0][axis] for p in pads) <= value <= max(p['bounds'][1][axis] for p in pads)
                                     for axis,value in enumerate((oc.x,oc.y)))
                   for other,oc in comps):
                continue
            footprints[c.footprint.casefold()].append((d,w,h,len(pads)))
    for samples in footprints.values():
        if len(samples) < 2:
            continue
        w,h,n = samples[0][1:]
        if any(count != n or abs(sw-w) > 0.1 or abs(sh-h) > 0.1 for _,sw,sh,count in samples):
            continue
        for d,sw,sh,_ in samples:
            if decisions[d].get('stage') != 'THT':
                decisions[d].update(sort_size=(sw,sh), size_source='paste area (inferred)')
    return decisions
