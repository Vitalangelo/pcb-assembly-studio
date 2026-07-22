#!/usr/bin/env python3
"""
PCB Assembly Studio
───────────────────────────────────────────────
Assembly drawing generator for Altium Designer / KiCad projects.
Standalone GUI - packaged to .exe via PyInstaller.
"""

import csv
import io
import math
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle, Circle
import matplotlib.patheffects as pe


# ═══════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

PALETTE = [
    "#FF0000",  # red
    "#FFD700",  # yellow
    "#00BB00",  # green
    "#00DDBB",  # cyan
    "#0055FF",  # blue
    "#FF00FF",  # magenta
    "#990000",  # dark red
]

DNI_COLOR = "#e8e8e8"   # fill for "do not install" parts

ROWS_PER_PAGE = 7
LABEL_PREFIXES = {"U", "J", "X", "XO", "T", "F", "VT", "S"}
LABEL_MIN_SIZE = 4.0

PREFIX_ORDER = {
    "R": 0, "C": 1, "L": 2, "FB": 3, "LED": 4,
    "D": 10, "VD": 11, "VT": 12,
    "U": 20, "XO": 21, "T": 22,
    "F": 30, "J": 31, "X": 32, "P": 33, "S": 40, "SW": 40,
}

APP_VERSION = "1.0"

# ---------------------------------------------------------------------------
#  Output localisation
#  Only strings that end up INSIDE the generated PDF live here, so a drawing
#  can be produced in the shop-floor language. The GUI itself is English-only.
#  Set LANG = "ru" for bilingual Russian/English drawing headers.
# ---------------------------------------------------------------------------
LANG = "en"

_STRINGS = {
    "en": {
        "cell": "Cell",
        "refdes": "Ref. Des",
        "description": "Description",
        "qty": "Qty.",
        "note": "Note",
        "key": "Key",
        "dni_short": "DNI",
        "dni_page": "- DO NOT INSTALL / DNI",
    },
    "ru": {
        "cell": "\u042f\u0447\u0435\u0439\u043a\u0430 / Cell",
        "refdes": "\u041f\u043e\u0437. \u043e\u0431\u043e\u0437\u043d\u0430\u0447\u0435\u043d\u0438\u0435 / Ref. Des",
        "description": "\u041e\u043f\u0438\u0441\u0430\u043d\u0438\u0435 / Description",
        "qty": "\u041a\u043e\u043b. / Qty.",
        "note": "\u041f\u0440\u0438\u043c. / Note",
        "key": "\u041a\u043b\u044e\u0447 / Key",
        "dni_short": "\u041d\u0415 \u0423\u0421\u0422\u0410\u041d.",
        "dni_page": "\u00b7 \u041d\u0415 \u0423\u0421\u0422\u0410\u041d\u0410\u0412\u041b\u0418\u0412\u0410\u0422\u042c / DNI",
    },
}
STR = _STRINGS[LANG]

# Do-not-install markers. NOTE: the Cyrillic alternatives are intentional -
# they match text found in real Russian-language BOM exports. Do not remove.
DNI_PATTERNS = re.compile(
    r"\b(DNI|DNP|DO NOT (?:PLACE|INSTALL|POPULATE|FIT)|NOT FITTED|NO ?POP"
    r"|НЕ УСТАНАВЛИВАТЬ|НЕ СТАВИТЬ)\b", re.I)

# Holes / fiducials are not assembly steps. Cyrillic alternatives are
# intentional - they match Russian-language BOM descriptions.
HOLE_PATTERNS = re.compile(
    r"\b(HOLE|MOUNT(?:ING)?|FIDUCIAL|ОТВЕРСТ\w*|КРЕП\w*|РЕПЕР\w*)\b", re.I)

SKIP_PREFIXES = {"TP", "MH", "FID"}


def normalize_layer(raw):
    """'BottomLayer' / 'B' / 'B.Cu' / 'bottom side' -> Bottom, else Top."""
    return "Bottom" if str(raw).strip().lower().startswith("b") else "Top"


def is_skippable(prefix, text=""):
    """Test points, fiducials and mounting hardware are not assembly steps.
    'S' is skipped ONLY when the description says it is a hole: in Altium S1
    defaults to a switch, which must stay in the assembly drawing."""
    if prefix in SKIP_PREFIXES:
        return True
    if prefix == "S" and HOLE_PATTERNS.search(text or ""):
        return True
    return False


def _read_text(filepath):
    """Read a text file: utf-8 (with BOM) -> cp1251 -> utf-8 with replacement.
    cp1251 matters: Russian Description fields from older Altium/Excel exports."""
    with open(filepath, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _coord(val, default_factor=1.0):
    """'10.0000' / '10.0mm' / '393.7mil' -> mm. A unit suffix on the value
    overrides the default factor (which comes from the file header)."""
    v = str(val).strip().strip('"')
    low = v.lower()
    if low.endswith("mil"):
        return float(v[:-3]) * 0.0254
    if low.endswith("mm"):
        return float(v[:-2])
    if low.endswith("in"):
        return float(v[:-2]) * 25.4
    return float(v) * default_factor


# ═══════════════════════════════════════════════════════════════════════
#  DATA MODEL
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class Component:
    designator: str
    layer: str
    x: float
    y: float
    rotation: float
    comment: str = ""
    description: str = ""
    prefix: str = ""
    w: float = 1.6
    h: float = 0.8
    shape: str = "rect"
    dni: bool = False

    def __post_init__(self):
        self.prefix = extract_prefix(self.designator)
        self._estimate_size()

    def _estimate_size(self):
        desc = (self.description + " " + self.comment).upper()
        prefix = self.prefix
        if prefix == "TP":
            self.w, self.h, self.shape = 1.0, 1.0, "circle"; return
        if prefix in ("MH", "FID"):
            self.w, self.h, self.shape = 2.5, 2.5, "circle"; return
        if prefix == "S" and HOLE_PATTERNS.search(desc):
            self.w, self.h, self.shape = 2.5, 2.5, "circle"; return
        pkg = {
            "0201": (0.6, 0.3), "0402": (1.0, 0.5), "0603": (1.6, 0.8),
            "0805": (2.0, 1.25), "1206": (3.2, 1.6), "1210": (3.2, 2.5),
            "2512": (6.3, 3.2), "1812": (4.5, 3.2), "2010": (5.0, 2.5),
        }
        if prefix in ("R", "C", "L", "FB"):
            for code, (w, h) in pkg.items():
                if code in desc:
                    self.w, self.h = w, h; return
        ic = {
            "FBGA": (23.0, 23.0), "896-PIN": (23.0, 23.0),
            "BGA": (12.0, 12.0),
            "TQFP": (10.0, 10.0), "LQFP": (10.0, 10.0), "QFP": (10.0, 10.0),
            "WQFN-20": (4.0, 4.0), "VQFN-20": (5.0, 5.0),
            "QFN": (5.0, 5.0), "DFN": (3.0, 3.0), "B3QFN": (10.0, 10.0),
            "TSSOP": (5.0, 4.4), "SSOP": (5.5, 4.0),
            "SOIC": (5.0, 4.0), "SOP": (5.0, 4.0), "MSOP": (3.0, 3.0),
            "SOT-23": (2.9, 1.6), "SOT23": (2.9, 1.6),
            "SOT-223": (6.5, 3.5), "SOT223": (6.5, 3.5),
            "SOT-363": (2.0, 1.25),
        }
        for pat, (w, h) in ic.items():
            if pat in desc:
                self.w, self.h, self.shape = w, h, "ic"; return
        m = re.search(r'(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\s*mm', desc, re.I)
        if m:
            self.w, self.h = float(m.group(1)), float(m.group(2))
            if prefix == "U": self.shape = "ic"
            return
        defaults = {
            "R": (1.0, 0.5), "C": (1.0, 0.5), "L": (3.2, 3.2),
            "FB": (1.6, 0.8), "F": (5.0, 5.0),
            "U": (5.0, 5.0), "D": (1.6, 0.8), "VD": (2.0, 1.0),
            "VT": (2.9, 1.6), "LED": (1.6, 0.8),
            "J": (8.0, 5.0), "X": (6.0, 4.0), "XO": (3.2, 2.5),
            "T": (4.0, 3.0), "P": (5.0, 3.0),
            "S": (6.0, 6.0), "SW": (6.0, 6.0),
        }
        if prefix in defaults:
            self.w, self.h = defaults[prefix]
            if prefix == "U": self.shape = "ic"
        else:
            self.w, self.h = 2.0, 1.5


@dataclass
class ComponentGroup:
    comment: str
    description: str
    designators: List[str]
    quantity: int
    note: str = ""
    dni: bool = False


# ═══════════════════════════════════════════════════════════════════════
#  PARSING
# ═══════════════════════════════════════════════════════════════════════

def extract_prefix(d):
    m = re.match(r'^([A-Za-z]+)', d)
    return m.group(1).upper() if m else ""

def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def _norm_cell(s):
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

# Map logical Pick&Place columns by normalised name.
_PNP_CELL_MATCH = {
    "designator": lambda n: n in ("designator", "refdes", "ref", "reference"),
    "layer":      lambda n: n in ("layer", "side") or n.endswith("layer"),
    "x":          lambda n: n in ("midx", "posx") or n.startswith("centerx"),
    "y":          lambda n: n in ("midy", "posy") or n.startswith("centery"),
    "rotation":   lambda n: n in ("rot",) or n.startswith("rotation"),
    "comment":    lambda n: n in ("comment", "val", "value"),
    "description": lambda n: n.startswith("desc"),
    "footprint":  lambda n: n in ("footprint", "package", "pattern"),
}


def parse_pick_place(filepath):
    """Pick & Place: Altium .txt (fixed width), Altium .csv, KiCad .pos.
    Units: mm by default; mil/inch are detected from the file header,
    the column header, or a suffix on the value."""
    content = _read_text(filepath)
    lines = content.splitlines()

    # Table header
    header_idx = None
    for i, line in enumerate(lines):
        low = line.lower()
        has_ref = "designator" in low or re.search(r'(^|\W)ref(\W|$)', low)
        if has_ref and ("layer" in low or "side" in low):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(
            "Pick & Place header row not found.\n"
            "First 5 lines:\n" + "\n".join(lines[:5]))

    hdr = lines[header_idx]
    hdr_low = hdr.lower()

    # Default units for the file
    default_factor = 1.0
    if "(mil" in hdr_low or " mil" in hdr_low:
        default_factor = 0.0254
    for line in lines[:header_idx]:                       # KiCad: "## Unit = in"
        if re.search(r'unit\s*=\s*(in|inch|inches)\b', line.lower()):
            default_factor = 25.4
            break

    # Delimiter: CSV (Altium comma / Excel ;) or fixed column positions
    delim = None
    if hdr.count(",") >= 3:
        delim = ","
    elif hdr.count(";") >= 3:
        delim = ";"

    if delim:
        components = _parse_pnp_delimited(lines[header_idx:], delim, default_factor)
    else:
        components = _parse_pnp_fixed(lines, header_idx, hdr, default_factor)

    if not components:
        sample = "\n".join(lines[header_idx:header_idx + 5])
        raise ValueError(
            "Pick & Place: no components found.\n\nData:\n" + sample)
    return components


def _parse_pnp_delimited(lines, delim, default_factor):
    """Altium CSV: "Designator","Footprint","Mid X",...,"Layer","Rotation"."""
    reader = csv.reader(io.StringIO("\n".join(lines)), delimiter=delim,
                        quotechar='"')
    rows = list(reader)
    if not rows:
        return {}
    cols = {}
    for j, cell in enumerate(rows[0]):
        n = _norm_cell(cell)
        for key, match in _PNP_CELL_MATCH.items():
            if key not in cols and match(n):
                cols[key] = j
    need = ("designator", "layer", "x", "y")
    if any(k not in cols for k in need):
        return {}

    components = {}
    for row in rows[1:]:
        try:
            def cell(key):
                j = cols.get(key)
                return row[j].strip().strip('"') if j is not None and j < len(row) else ""
            desig = cell("designator")
            layer_raw = cell("layer")
            if not desig or not layer_raw:
                continue
            x = _coord(cell("x"), default_factor)
            y = _coord(cell("y"), default_factor)
            rot = float(cell("rotation") or 0)
            components[desig] = Component(
                designator=desig, layer=normalize_layer(layer_raw),
                x=x, y=y, rotation=rot,
                comment=cell("comment"), description=cell("description"))
        except (ValueError, IndexError):
            continue
    return components


def _parse_pnp_fixed(lines, header_idx, hdr, default_factor):
    """Altium .txt (fixed width) and KiCad .pos (aligned columns)."""
    col_searches = {
        "designator": ["Designator", "Ref Des", "RefDes", "Ref"],
        "layer": ["Layer", "Side"],
        "x": ["Center-X", "Mid X", "Pos X", "PosX"],
        "y": ["Center-Y", "Mid Y", "Pos Y", "PosY"],
        "rotation": ["Rotation", "Rot"],
        "comment": ["Comment", "Val"],
        "description": ["Description"],
        "footprint": ["Footprint", "Package"],
    }
    col_positions = {}
    for key, patterns in col_searches.items():
        for pat in patterns:
            idx = hdr.find(pat)
            if idx == -1:
                idx = hdr.lower().find(pat.lower())
            if idx >= 0:
                col_positions[key] = idx
                break

    all_starts = sorted(set(col_positions.values()))

    def get_end(start):
        for p in all_starts:
            if p > start:
                return p
        return None

    col_ranges = {k: (s, get_end(s)) for k, s in col_positions.items()}

    # KiCad .pos: the header starts with "# " but data starts at column 0;
    # anchor the first column to the start of the line
    if hdr.lstrip().startswith("#") and col_ranges:
        first_key = min(col_ranges, key=lambda k: col_ranges[k][0])
        col_ranges[first_key] = (0, col_ranges[first_key][1])

    need = ["designator", "layer", "x", "y"]
    if any(n not in col_ranges for n in need):
        return _parse_pnp_simple(lines, header_idx)

    def get_field(line, field):
        if field not in col_ranges:
            return ""
        start, end = col_ranges[field]
        return (line[start:end] if end else line[start:]).strip().strip('"')

    components = {}
    for line in lines[header_idx + 1:]:
        if not line.strip() or line.lstrip().startswith(("#", "=")):
            continue
        try:
            desig = get_field(line, "designator")
            layer_raw = get_field(line, "layer")
            x_str = get_field(line, "x")
            y_str = get_field(line, "y")
            rot_str = get_field(line, "rotation") or "0"
            if not desig or not layer_raw or not x_str:
                continue
            components[desig] = Component(
                designator=desig, layer=normalize_layer(layer_raw),
                x=_coord(x_str, default_factor),
                y=_coord(y_str, default_factor),
                rotation=float(rot_str),
                comment=get_field(line, "comment"),
                description=get_field(line, "description"))
        except (ValueError, IndexError):
            continue
    return components


def _parse_pnp_simple(lines, header_idx):
    """Fallback: simple whitespace-separated format."""
    components = {}
    for line in lines[header_idx + 1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            desig = parts[0]
            layer = normalize_layer(parts[1])
            x, y, rot = float(parts[2]), float(parts[3]), float(parts[4])
        except (ValueError, IndexError):
            continue
        components[desig] = Component(designator=desig, layer=layer,
                                      x=x, y=y, rotation=rot)
    if not components:
        raise ValueError("Pick & Place: could not parse any components.")
    return components


# ═══════════════════════════════════════════════════════════════════════
#  BOARD ARTWORK (DXF / Gerber ZIP)
# ═══════════════════════════════════════════════════════════════════════

def load_board_drawing(filepath):
    """Load board drawing from DXF, DWG, or Gerber ZIP.
    Returns dict: {top: [...], bottom: [...], outline: [...]}
    Each entry is a list of (type, data) tuples for matplotlib rendering.
    """
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".zip":
        return _load_gerbers(filepath)
    elif ext in (".dxf", ".dwg"):
        return _load_dxf(filepath)
    else:
        raise ValueError(f"Unsupported format: {ext}\nSupported: .zip (Gerber), .dxf, .dwg")


def _load_gerbers(zip_path):
    """Load Gerber files from ZIP, return classified entities."""
    try:
        from gerbonara import GerberFile
    except ImportError:
        raise ImportError(
            "Gerber support requires the gerbonara package (pip install gerbonara).\n"
            "It is already bundled in the built .exe - rebuild via build_exe.bat.")

    import zipfile, tempfile, shutil

    # Altium/Protel extensions -> layer categories
    ext_to_layer = {
        '.gto': 'top', '.gbo': 'bottom',           # silk/overlay
        '.gts': 'top', '.gbs': 'bottom',           # solder mask
        '.gm1': 'outline', '.gm2': 'outline',      # mechanical
        '.gm3': 'outline', '.gm4': 'outline',
        '.gml': 'outline',
        '.gko': 'outline',                          # keepout
    }
    # KiCad encodes layers in the filename (board-F_Silkscreen.gbr etc.)
    name_to_layer = [
        ("edge_cuts", "outline"), ("edge.cuts", "outline"), ("outline", "outline"),
        ("f_silkscreen", "top"), ("f_silks", "top"), ("f.silks", "top"),
        ("b_silkscreen", "bottom"), ("b_silks", "bottom"), ("b.silks", "bottom"),
        ("f_mask", "top"), ("f.mask", "top"),
        ("b_mask", "bottom"), ("b.mask", "bottom"),
    ]

    result = {"top": [], "bottom": [], "outline": []}
    tmp = tempfile.mkdtemp()

    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                ext = os.path.splitext(name)[1].lower()
                layer_key = ext_to_layer.get(ext)
                if layer_key is None and ext in (".gbr", ".ger"):
                    low = os.path.basename(name).lower()
                    for token, lk in name_to_layer:
                        if token in low:
                            layer_key = lk
                            break
                if layer_key is None:
                    continue

                # Extract with clean ASCII name to avoid encoding issues
                clean_name = f"layer{len(result[layer_key])}{ext}"
                outpath = os.path.join(tmp, clean_name)
                with open(outpath, 'wb') as f:
                    f.write(z.read(name))

                try:
                    gf = GerberFile.open(outpath)
                    prims = _gerber_to_primitives(gf)
                    result[layer_key].extend(prims)
                except Exception:
                    continue
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return result


def _gerber_to_primitives(gerber_file):
    """Convert GerberFile objects to simple (type, data) tuples.
    Everything is converted to mm (gerbonara returns file units),
    and gerbonara gives arc centres RELATIVE to the start point."""
    from gerbonara.graphic_objects import Line as GLine, Arc as GArc, Flash, Region
    from gerbonara.apertures import CircleAperture, RectangleAperture, ObroundAperture
    from gerbonara import MM

    def to_mm(value, unit):
        try:
            return MM(value, unit)
        except Exception:
            return float(value)

    primitives = []

    for obj in gerber_file.objects:
        try:
            try:
                obj = obj.converted(MM)     # coordinates -> mm
            except Exception:
                pass

            if isinstance(obj, GLine):
                x1, y1 = float(obj.x1), float(obj.y1)
                x2, y2 = float(obj.x2), float(obj.y2)
                w = 0.1
                ap = obj.aperture
                if ap is not None:
                    if isinstance(ap, CircleAperture):
                        w = to_mm(ap.diameter, ap.unit)
                    elif isinstance(ap, (RectangleAperture, ObroundAperture)):
                        w = to_mm(ap.w, ap.unit)
                primitives.append(("line", (x1, y1, x2, y2, w)))

            elif isinstance(obj, GArc):
                x1, y1 = float(obj.x1), float(obj.y1)
                x2, y2 = float(obj.x2), float(obj.y2)
                cx = x1 + float(obj.cx)     # centre is relative to the start point!
                cy = y1 + float(obj.cy)
                w = 0.1
                if obj.aperture and isinstance(obj.aperture, CircleAperture):
                    w = to_mm(obj.aperture.diameter, obj.aperture.unit)
                primitives.append(("garc", (x1, y1, x2, y2, cx, cy, obj.clockwise, w)))

            elif isinstance(obj, Flash):
                x, y = float(obj.x), float(obj.y)
                ap = obj.aperture
                if isinstance(ap, CircleAperture):
                    r = to_mm(ap.diameter, ap.unit) / 2
                    primitives.append(("circle", (x, y, r)))
                elif isinstance(ap, (RectangleAperture, ObroundAperture)):
                    w = to_mm(ap.w, ap.unit)
                    h = to_mm(ap.h, ap.unit)
                    primitives.append(("rect", (x - w/2, y - h/2, w, h)))

            elif isinstance(obj, Region):
                pts = []
                for seg in obj.outline:
                    if isinstance(seg, (GLine, GArc)):
                        pts.append((float(seg.x1), float(seg.y1)))
                if pts:
                    primitives.append(("polygon", pts))

        except (AttributeError, TypeError, ValueError):
            continue

    return primitives


def _load_dxf(filepath):
    """Load DXF/DWG → classified entities."""
    try:
        import ezdxf
    except ImportError:
        raise ImportError("pip install ezdxf")

    ext = os.path.splitext(filepath)[1].lower()
    dxf_path = filepath

    if ext == ".dwg":
        dxf_path = _convert_dwg(filepath)

    doc = ezdxf.readfile(dxf_path)
    msp = doc.modelspace()
    entities_by_layer = defaultdict(list)

    for entity in msp:
        layer = entity.dxf.layer
        etype = entity.dxftype()
        try:
            if etype == "LINE":
                s, e = entity.dxf.start, entity.dxf.end
                entities_by_layer[layer].append(("line", (s.x, s.y, e.x, e.y, 0.1)))
            elif etype == "CIRCLE":
                c = entity.dxf.center
                entities_by_layer[layer].append(("circle", (c.x, c.y, entity.dxf.radius)))
            elif etype == "ARC":
                c = entity.dxf.center
                entities_by_layer[layer].append(("arc", (
                    c.x, c.y, entity.dxf.radius,
                    entity.dxf.start_angle, entity.dxf.end_angle)))
            elif etype in ("LWPOLYLINE", "POLYLINE"):
                if etype == "LWPOLYLINE":
                    pts = [(p[0], p[1]) for p in entity.get_points(format="xy")]
                    closed = entity.closed
                else:
                    pts = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                    closed = entity.is_closed
                if len(pts) >= 2:
                    entities_by_layer[layer].append(("polyline", (pts, closed)))
        except Exception:
            continue

    if ext == ".dwg" and dxf_path != filepath:
        try: os.remove(dxf_path)
        except: pass

    return _classify_dxf_layers(entities_by_layer)


def _convert_dwg(dwg_path):
    """Convert DWG → DXF."""
    try:
        from ezdxf.addons import odafc
        dxf_path = dwg_path.rsplit(".", 1)[0] + "_converted.dxf"
        odafc.convert(dwg_path, dxf_path)
        if os.path.exists(dxf_path):
            return dxf_path
    except Exception:
        pass

    oda_locs = [
        os.path.expandvars(r"%ProgramFiles%\ODA\ODAFileConverter\ODAFileConverter.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\ODA\ODAFileConverter\ODAFileConverter.exe"),
    ]
    for oda in oda_locs:
        if os.path.isfile(oda):
            import subprocess, tempfile
            out_dir = tempfile.mkdtemp()
            try:
                subprocess.run([oda, os.path.dirname(dwg_path), out_dir,
                                "ACAD2018", "DXF", "0", "1",
                                os.path.basename(dwg_path)],
                               timeout=30, capture_output=True)
                result = os.path.join(out_dir,
                    os.path.basename(dwg_path).rsplit(".", 1)[0] + ".dxf")
                if os.path.exists(result):
                    return result
            except Exception:
                continue

    raise ValueError(
        "Could not convert the DWG file.\n\n"
        "Options:\n"
        "- Use a Gerber ZIP archive (recommended)\n"
        "- Export DXF from Altium: File -> Export -> DXF/DWG -> DXF format\n"
        "- Install ODA File Converter:\n"
        "  https://www.opendesign.com/guestfiles/oda_file_converter")


def _classify_dxf_layers(entities_by_layer):
    """Classify DXF layers into top/bottom/outline."""
    top, bottom, outline = [], [], []
    for layer, ents in entities_by_layer.items():
        low = layer.lower()
        if any(k in low for k in ["keepout", "keep out", "mechanical4",
                                   "mechanical 4", "mech4", "outline", "edge"]):
            outline.extend(ents)
        elif any(k in low for k in ["bottomoverlay", "bottomsolder",
                                     "bottom overlay", "bottom solder"]):
            bottom.extend(ents)
        elif any(k in low for k in ["topoverlay", "topsolder",
                                     "top overlay", "top solder"]):
            top.extend(ents)
        elif "bottom" in low:
            bottom.extend(ents)
        elif "top" in low:
            top.extend(ents)
    return {"top": top, "bottom": bottom, "outline": outline}


def render_board_entities(ax, entities, color="#555555", linewidth=0.3,
                          alpha=0.7, mirror_x=0):
    """Render board entities (from Gerber or DXF) on matplotlib axes."""
    import matplotlib.patches as mpatches

    def mx(x):
        return (mirror_x - x) if mirror_x > 0 else x

    for item in entities:
        etype = item[0]
        data = item[1]
        try:
            if etype == "line":
                if len(data) == 5:
                    x1, y1, x2, y2, w = data
                else:
                    x1, y1, x2, y2 = data; w = 0.1
                lw = max(0.1, min(w * 0.8, 1.5))
                ax.plot([mx(x1), mx(x2)], [y1, y2], color=color,
                       linewidth=lw, alpha=alpha, zorder=1, solid_capstyle="round")

            elif etype == "circle":
                cx, cy, r = data
                ax.add_patch(Circle((mx(cx), cy), r, facecolor=color,
                    edgecolor=color, linewidth=0.1, alpha=alpha * 0.5, zorder=1))

            elif etype == "rect":
                rx, ry, rw, rh = data
                ax.add_patch(Rectangle((mx(rx + rw) if mirror_x > 0 else rx, ry),
                    rw, rh, facecolor=color, edgecolor=color,
                    linewidth=0.1, alpha=alpha * 0.5, zorder=1))

            elif etype == "arc":
                cx, cy, r, sa, ea = data
                if mirror_x > 0:
                    sa, ea = 180 - ea, 180 - sa
                ax.add_patch(mpatches.Arc((mx(cx), cy), 2*r, 2*r,
                    angle=0, theta1=sa, theta2=ea, edgecolor=color,
                    linewidth=linewidth, alpha=alpha, zorder=1))

            elif etype == "garc":
                x1, y1, x2, y2, cx, cy, cw, w = data
                r = math.sqrt((x1 - cx)**2 + (y1 - cy)**2)
                if r < 0.001: continue
                is_full = abs(x1-x2) < 0.001 and abs(y1-y2) < 0.001
                lw = max(0.1, min(w * 0.8, 1.5))
                if is_full:
                    # Full circle
                    from matplotlib.patches import Circle as MplCircle
                    ax.add_patch(MplCircle((mx(cx), cy), r, facecolor=color,
                        edgecolor=color, linewidth=lw, alpha=alpha, zorder=1))
                else:
                    sa = math.atan2(y1 - cy, x1 - cx)
                    ea = math.atan2(y2 - cy, x2 - cx)
                    if cw and ea > sa: ea -= 2 * math.pi
                    if not cw and ea < sa: ea += 2 * math.pi
                    n = max(8, int(abs(ea - sa) * r * 2))
                    angles = [sa + (ea - sa) * i / n for i in range(n + 1)]
                    xs = [mx(cx + r * math.cos(a)) for a in angles]
                    ys = [cy + r * math.sin(a) for a in angles]
                    ax.plot(xs, ys, color=color, linewidth=lw,
                           alpha=alpha, zorder=1, solid_capstyle="round")

            elif etype == "polyline":
                pts, closed = data
                xs = [mx(p[0]) for p in pts]
                ys = [p[1] for p in pts]
                if closed: xs.append(xs[0]); ys.append(ys[0])
                ax.plot(xs, ys, color=color, linewidth=linewidth,
                       alpha=alpha, zorder=1)

            elif etype == "polygon":
                pts = [(mx(p[0]), p[1]) for p in data]
                from matplotlib.patches import Polygon as MplPoly
                ax.add_patch(MplPoly(pts, closed=True, facecolor=color,
                    edgecolor=color, linewidth=0.05, alpha=alpha * 0.4, zorder=1))

            elif etype == "point":
                px, py = data
                ax.plot(mx(px), py, ".", color=color, markersize=0.5,
                       alpha=alpha, zorder=1)

        except Exception:
            continue


def _find_bom_header(rows_iter, max_scan=20):
    """Find the BOM header row within the first max_scan lines.
    Returns (index, {column_name_lower: index}) or (None, None)."""
    for i, row in enumerate(rows_iter[:max_scan]):
        lower = [str(c or "").strip().lower() for c in row]
        if "designator" in lower and (
                "comment" in lower or any(c.startswith("desc") for c in lower)
                or "value" in lower or "val" in lower):
            return i, {c: j for j, c in enumerate(lower) if c}
    return None, None


def _bom_cols(header):
    """Logical BOM column indices. Missing ones -> None."""
    def col(*names, contains=None):
        for n in names:
            if n in header:
                return header[n]
        if contains:
            for k, v in header.items():
                if contains in k:
                    return v
        return None
    return {
        "comment": col("comment"),
        "desc": col(contains="desc"),
        "desig": col("designator"),
        "layer": col("layer", "side"),
        "val": col("val", "value"),
    }


def _bom_row_to_result(comment, desc, val, desig_str, layer_raw):
    desigs = [d.strip() for d in re.split(r"[,;]", desig_str) if d.strip()]
    if not desigs:
        return None
    layer = normalize_layer(layer_raw) if layer_raw else "Top"
    blob = f"{comment} {val} {desc}"
    is_dni = bool(DNI_PATTERNS.search(blob))
    display = val if (val and not DNI_PATTERNS.search(val)) else (comment or "")
    full_desc = desc if desc and len(desc) < 60 else (display or comment)
    return {"comment": comment, "description": desc, "display": display,
            "full_desc": full_desc, "designators": desigs,
            "layer": layer, "dni": is_dni}


def parse_bom_txt(filepath):
    """BOM from .txt/.csv: tab / ; / , delimiter is auto-detected,
    encoding utf-8/cp1251. The Layer column is optional (the layer is taken
    from Pick & Place anyway)."""
    content = _read_text(filepath)
    for delim in ("\t", ";", ","):
        all_rows = list(csv.reader(io.StringIO(content), delimiter=delim,
                                   quotechar='"'))
        hidx, header = _find_bom_header(all_rows)
        if hidx is not None and len(header) >= 2:
            break
    if hidx is None:
        raise ValueError("BOM header row not found "
                         "(need Designator + Comment/Description columns)")
    c = _bom_cols(header)
    results = []
    for row in all_rows[hidx + 1:]:
        def cell(idx):
            return row[idx].strip() if idx is not None and idx < len(row) else ""
        desig_str = cell(c["desig"])
        if not desig_str:
            continue
        r = _bom_row_to_result(cell(c["comment"]), cell(c["desc"]),
                               cell(c["val"]), desig_str, cell(c["layer"]))
        if r:
            results.append(r)
    return results


def parse_bom_excel(filepath):
    import openpyxl
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    ws = wb.active
    rows_data = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows_data:
        return []
    hidx, header = _find_bom_header(rows_data)
    if hidx is None:
        raise ValueError("BOM header row not found in the Excel file "
                         "(need Designator + Comment/Description columns)")
    c = _bom_cols(header)
    results = []
    for row in rows_data[hidx + 1:]:
        def cell(idx):
            return str(row[idx] or "").strip() if idx is not None and idx < len(row) else ""
        desig_str = cell(c["desig"])
        if not desig_str:
            continue
        r = _bom_row_to_result(cell(c["comment"]), cell(c["desc"]),
                               cell(c["val"]), desig_str, cell(c["layer"]))
        if r:
            results.append(r)
    return results


def parse_bom(filepath):
    ext = os.path.splitext(filepath)[1].lower()
    if ext in (".xls", ".xlsx"): return parse_bom_excel(filepath)
    return parse_bom_txt(filepath)


# ═══════════════════════════════════════════════════════════════════════
#  GROUPING
# ═══════════════════════════════════════════════════════════════════════

KEYED_PREFIXES = ("U", "D", "VD", "VT", "Q", "X", "J", "P", "XO", "T")


def group_from_bom(bom_rows, pnp):
    """Build groups from the BOM. Each component's layer comes from Pick & Place
    (the authoritative source): a BOM row combining a part from both sides
    is correctly split into Top and Bottom groups."""
    by_layer = defaultdict(list)
    for row in bom_rows:
        desigs = sorted(row["designators"], key=natural_sort_key)
        text = f"{row['comment']} {row['description']}"
        prefix = extract_prefix(desigs[0]) if desigs else ""
        if is_skippable(prefix, text):
            continue
        per_layer = defaultdict(list)
        for d in desigs:
            if d in pnp:
                comp = pnp[d]
                comp.comment = row["comment"]
                comp.description = row["description"]
                comp.dni = row["dni"]
                comp._estimate_size()
                per_layer[comp.layer].append(d)
            else:
                # Not in P&P (e.g. a DNI variant) - keep it in the table
                per_layer[row["layer"]].append(d)
        is_keyed = prefix in KEYED_PREFIXES
        for layer, ds in per_layer.items():
            by_layer[layer].append(ComponentGroup(
                comment=row["comment"], description=row["full_desc"],
                designators=ds, quantity=len(ds),
                note=STR["key"] if is_keyed else "", dni=row["dni"]))
    # Don't sort here - let the UI sort mode decide the order
    return dict(by_layer)


def group_auto(pnp):
    by_lp = defaultdict(lambda: defaultdict(list))
    for desig, c in pnp.items():
        if is_skippable(c.prefix, f"{c.description} {c.comment}"):
            continue
        by_lp[c.layer][c.prefix].append(desig)
    result = {}
    for layer in sorted(by_lp):
        groups = []
        for prefix in sorted(by_lp[layer], key=lambda p: PREFIX_ORDER.get(p, 50)):
            desigs = sorted(by_lp[layer][prefix], key=natural_sort_key)
            is_keyed = prefix in KEYED_PREFIXES
            groups.append(ComponentGroup(
                comment=f"{prefix}", description=f"All {prefix} components",
                designators=desigs, quantity=len(desigs),
                note=STR["key"] if is_keyed else ""))
        result[layer] = groups
    return result


# ═══════════════════════════════════════════════════════════════════════
#  RENDERING
# ═══════════════════════════════════════════════════════════════════════

def _half_extents(c):
    """Half-extents of a component along each axis, accounting for rotation."""
    r = c.rotation % 180
    if abs(r - 90) < 1:
        return c.h / 2, c.w / 2
    if r > 1:                       # arbitrary angle - use the diagonal
        d = math.hypot(c.w, c.h) / 2
        return d, d
    return c.w / 2, c.h / 2


def get_board_bounds(pnp, margin=3.0):
    if not pnp:
        return (0, 100, 0, 100)
    xs1, xs2, ys1, ys2 = [], [], [], []
    for c in pnp.values():
        hw, hh = _half_extents(c)
        xs1.append(c.x - hw); xs2.append(c.x + hw)
        ys1.append(c.y - hh); ys2.append(c.y + hh)
    return min(xs1)-margin, max(xs2)+margin, min(ys1)-margin, max(ys2)+margin


def entity_bounds(board_layers):
    """(x_min, x_max, y_min, y_max) of the board outline from Gerber/DXF, or None."""
    if not board_layers:
        return None
    xs, ys = [], []
    for etype, data in board_layers.get("outline") or []:
        try:
            if etype == "line":
                xs += [data[0], data[2]]; ys += [data[1], data[3]]
            elif etype == "circle":
                cx, cy, r = data
                xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
            elif etype == "rect":
                xs += [data[0], data[0] + data[2]]
                ys += [data[1], data[1] + data[3]]
            elif etype == "polyline":
                pts = data[0]
                xs += [p[0] for p in pts]; ys += [p[1] for p in pts]
            elif etype == "polygon":
                xs += [p[0] for p in data]; ys += [p[1] for p in data]
            elif etype == "garc":
                x1, y1, x2, y2, cx, cy, cw, w = data
                r = math.hypot(x1 - cx, y1 - cy)
                xs += [x1, x2, cx - r, cx + r]; ys += [y1, y2, cy - r, cy + r]
            elif etype == "arc":
                cx, cy, r, sa, ea = data
                xs += [cx - r, cx + r]; ys += [cy - r, cy + r]
        except Exception:
            continue
    if not xs:
        return None
    return min(xs), max(xs), min(ys), max(ys)


def compute_bounds(pnp, board_layers=None, margin=3.0):
    """View bounds: board outline (if present) union component extents -
    nothing gets clipped."""
    cx1, cx2, cy1, cy2 = get_board_bounds(pnp, margin)
    ob = entity_bounds(board_layers)
    if ob:
        x1, x2, y1, y2 = ob
        return (min(cx1, x1 - 1), max(cx2, x2 + 1),
                min(cy1, y1 - 1), max(cy2, y2 + 1))
    return (cx1, cx2, cy1, cy2)


def should_label(comp):
    if comp.prefix in LABEL_PREFIXES: return True
    return max(comp.w, comp.h) >= LABEL_MIN_SIZE


def draw_component(ax, comp, color, alpha=1.0, label=False,
                   mirror_x=0, linewidth=0.3, zorder=2, edge_color="#333",
                   hatch=None):
    """A component on the PDF view. When mirrored (Bottom view) the ENTIRE
    geometry is flipped: rotation angle (theta -> -theta) and the pin-1 marker
    offset (dx -> -dx), not just the centre - otherwise the key points to the
    wrong corner."""
    x, y = comp.x, comp.y
    mirrored = mirror_x > 0
    if mirrored:
        x = mirror_x - x
    w, h = comp.w, comp.h
    if comp.shape == "circle":
        ax.add_patch(Circle((x, y), w/2, facecolor=color, edgecolor=edge_color,
                           linewidth=linewidth, alpha=alpha, zorder=zorder,
                           hatch=hatch))
    else:
        angle = -comp.rotation if mirrored else comp.rotation
        ax.add_patch(Rectangle((x-w/2, y-h/2), w, h, angle=angle,
                               rotation_point="center", facecolor=color,
                               edgecolor=edge_color, linewidth=linewidth,
                               alpha=alpha, zorder=zorder, hatch=hatch))
        if comp.shape == "ic" and alpha >= 0.6:
            rad = math.radians(comp.rotation)
            dx = (-w/2+0.6)*math.cos(rad) - (-h/2+0.6)*math.sin(rad)
            dy = (-w/2+0.6)*math.sin(rad) + (-h/2+0.6)*math.cos(rad)
            if mirrored:
                dx = -dx
            ax.plot(x+dx, y+dy, "o", color="white",
                    markersize=max(2.2, min(w, h)*0.4),
                    zorder=zorder+1, markeredgecolor="#333", markeredgewidth=0.3)
    if label and should_label(comp):
        fs = max(2, min(5, min(w,h)*0.55))
        ax.text(x, y, comp.designator, ha="center", va="center",
                fontsize=fs, fontweight="bold", color="white", zorder=zorder+2,
                path_effects=[pe.Stroke(linewidth=1.0, foreground="#333"), pe.Normal()])


def draw_board(fig, pnp, gwc, layer, bounds, board_layers=None):
    x_min, x_max, y_min, y_max = bounds
    mirror_x = (x_min + x_max) if layer == "Bottom" else 0
    ax = fig.add_axes([0.04, 0.33, 0.92, 0.62])
    ax.set_xlim(x_min-2, x_max+2); ax.set_ylim(y_min-2, y_max+2)
    ax.set_aspect("equal"); ax.set_facecolor("white")

    has_dxf = board_layers is not None
    has_outline = bool(has_dxf and board_layers.get("outline"))

    # Draw the placeholder rectangle only when there is no real outline
    if not has_outline:
        ax.add_patch(Rectangle((x_min,y_min), x_max-x_min, y_max-y_min,
                               facecolor="#f8f8f8", edgecolor="#333", lw=1.2, zorder=0))

    # Render board outline from DXF/Gerber
    if has_outline:
        render_board_entities(ax, board_layers["outline"], color="#444444",
                           linewidth=0.6, alpha=0.9, mirror_x=mirror_x)

    # Render solder mask + overlay as background
    if has_dxf:
        overlay_key = "bottom" if layer == "Bottom" else "top"
        if board_layers.get(overlay_key):
            render_board_entities(ax, board_layers[overlay_key], color="#888888",
                               linewidth=0.3, alpha=0.5, mirror_x=mirror_x)

    # Background components (gray) — skip if DXF provides the visuals.
    # Always draw context on DNI pages, otherwise the page looks empty
    is_dni_page = bool(gwc) and all(g.dni for g, _, _ in gwc)
    highlighted = set()
    for g, _, _ in gwc: highlighted.update(g.designators)
    if not has_dxf or is_dni_page:
        for desig, comp in pnp.items():
            if comp.layer != layer or desig in highlighted: continue
            gray = "#e0e0e0" if comp.prefix in ("TP","S") else "#cccccc"
            a = 0.4 if comp.prefix in ("TP","S") else 0.6
            lw = 0.1 if comp.prefix in ("TP","S") else 0.15
            draw_component(ax, comp, gray, alpha=a, mirror_x=mirror_x,
                          linewidth=lw, zorder=1, edge_color="#999")

    # Highlighted components - colour fills (DNI: grey with hatching)
    for group, color_hex, _num in gwc:
        for desig in group.designators:
            if desig not in pnp: continue
            comp = pnp[desig]
            if comp.layer != layer:
                continue    # a component from the other side is not drawn on this page
            alpha_val = 0.65 if has_dxf else 0.92
            draw_component(ax, comp, color_hex, alpha=alpha_val, label=True,
                          mirror_x=mirror_x, linewidth=0.5, zorder=4,
                          edge_color="#333",
                          hatch="///" if group.dni else None)

    ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)


def draw_table(fig, gwc):
    ax = fig.add_axes([0.04, 0.065, 0.92, 0.25])
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
    cx = [0.01, 0.06, 0.58, 0.78, 0.88]
    headers = [STR["cell"], STR["refdes"], STR["description"],
               STR["qty"], STR["note"]]
    rh = 1.0 / (ROWS_PER_PAGE + 1.2); hy = 1.0 - rh * 0.2
    for j, h in enumerate(headers):
        ax.text(cx[j]+0.01, hy, h, ha="left", va="top",
               fontsize=5, fontweight="bold", color="#444")
    ax.axhline(y=hy-rh*0.55, xmin=0.01, xmax=0.99, color="#999", lw=0.5)
    for i, (group, color, num) in enumerate(gwc):
        ry = hy - rh * (i + 1.1)
        ax.add_patch(Rectangle((cx[0], ry-rh*0.3), 0.04, rh*0.6,
                               facecolor=color, edgecolor="#333", lw=0.5,
                               hatch="///" if group.dni else None))
        # Cell number inside the swatch - readable with colour blindness and in B/W
        if num is not None:
            ax.text(cx[0]+0.02, ry, str(num), ha="center", va="center",
                    fontsize=4.5, fontweight="bold", color="white",
                    path_effects=[pe.Stroke(linewidth=0.9, foreground="#333"),
                                  pe.Normal()])
        dt = ", ".join(group.designators)
        if len(dt) > 60: dt = dt[:57] + "..."
        sfx = " [DNI]" if group.dni else ""
        ax.text(cx[1]+0.01, ry, dt+sfx, ha="left", va="center", fontsize=4, color="#222")
        desc = group.description
        if len(desc) > 38: desc = desc[:35] + "..."
        ax.text(cx[2]+0.01, ry, desc, ha="left", va="center", fontsize=4.5, color="#333")
        ax.text(cx[3]+0.04, ry, str(group.quantity), ha="center", va="center",
               fontsize=5, color="#222")
        note = STR["dni_short"] if group.dni else group.note
        ax.text(cx[4]+0.01, ry, note, ha="left", va="center",
               fontsize=3.5, color="#b00020" if group.dni else "#666")
        ax.axhline(y=ry-rh*0.42, xmin=0.01, xmax=0.99, color="#ddd", lw=0.3)


def draw_title_block(fig, project_name, layer, mt, page, total, edition, designer):
    ax = fig.add_axes([0.04, 0.005, 0.92, 0.055])
    ax.set_xlim(0,1); ax.set_ylim(0,1); ax.axis("off")
    ax.add_patch(Rectangle((0,0), 1, 1, facecolor="#f5f5f5", edgecolor="#333", lw=0.8))
    for dx in [0.28, 0.56, 0.76, 0.88]:
        ax.plot([dx,dx], [0,1], color="#aaa", lw=0.4)
    ax.text(0.14, 0.6, project_name, ha="center", va="center",
           fontsize=7, fontweight="bold", color="#222")
    ax.text(0.14, 0.2, designer, ha="center", va="center", fontsize=5, color="#777")
    ax.text(0.42, 0.6, f"{layer} {mt}", ha="center", va="center",
           fontsize=7, fontweight="bold", color="#222")
    ax.text(0.42, 0.2, "Assembly Drawing", ha="center", va="center",
           fontsize=5, color="#777")
    ax.text(0.66, 0.5, f"Edition: {edition}", ha="center", va="center",
           fontsize=6, color="#333")
    ax.text(0.82, 0.5, f"Sheet {page}/{total}", ha="center", va="center",
           fontsize=6, fontweight="bold", color="#333")
    ax.text(0.94, 0.5, "A4", ha="center", va="center", fontsize=5, color="#999")


# ═══════════════════════════════════════════════════════════════════════
#  PDF GENERATION
# ═══════════════════════════════════════════════════════════════════════

def detect_mount_type(groups):
    tht_kw = ["THT","DIP","PIN HEADER","CONNECTOR","SIP","TO-220","VERTICAL","RIGHT ANGLE","R/A"]
    has_smt = has_tht = False
    for g in groups:
        if any(k in (g.description+" "+g.comment).upper() for k in tht_kw):
            has_tht = True
        else: has_smt = True
    if has_smt and has_tht: return "SMT/THT"
    return "THT" if has_tht else "SMT"


def generate_pdf(pnp, groups_by_layer, output_path,
                 project_name="Project", designed_by="", edition="v1.0",
                 progress_callback=None, board_layers=None):
    bounds = compute_bounds(pnp, board_layers)
    plan = []
    for layer in ["Bottom", "Top"]:
        groups = groups_by_layer.get(layer)
        if not groups: continue
        install = [g for g in groups if not g.dni]
        dni_groups = [g for g in groups if g.dni]
        mt = detect_mount_type(install or groups)
        # Assembly pages: colour by RUNNING group index on the side -
        # exactly how the GUI numbers them, so colours always match.
        for i in range(0, len(install), ROWS_PER_PAGE):
            chunk = install[i:i+ROWS_PER_PAGE]
            gwc = [(g, PALETTE[(i + j) % len(PALETTE)], i + j + 1)
                   for j, g in enumerate(chunk)]
            plan.append((layer, mt, gwc))
        # DNI parts get their own pages at the end of each side, grey and hatched
        for i in range(0, len(dni_groups), ROWS_PER_PAGE):
            chunk = dni_groups[i:i+ROWS_PER_PAGE]
            gwc = [(g, DNI_COLOR, None) for g in chunk]
            plan.append((layer, STR["dni_page"], gwc))
    total = len(plan)
    if total == 0: return 0

    with PdfPages(output_path) as pdf:
        for pn, (layer, mt, gwc) in enumerate(plan, 1):
            fig = plt.figure(figsize=(11.69, 8.27), facecolor="white")
            fig.patches.append(Rectangle((0.02,0.002), 0.96, 0.996,
                facecolor="none", edgecolor="#555", lw=0.8, transform=fig.transFigure))
            draw_board(fig, pnp, gwc, layer, bounds, board_layers=board_layers)
            draw_table(fig, gwc)
            draw_title_block(fig, project_name, layer, mt, pn, total, edition, designed_by)
            pdf.savefig(fig, dpi=150)
            plt.close(fig)
            if progress_callback:
                progress_callback(pn, total, layer, mt,
                                  sum(g.quantity for g, _, _ in gwc))
    return total


# ═══════════════════════════════════════════════════════════════════════
#  DESKTOP GUI — Tkinter + Canvas board viewer
# ═══════════════════════════════════════════════════════════════════════

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


class BoardCanvas:
    """Interactive PCB board viewer with zoom/pan on light board."""
    BOARD_BG = "#ffffff"
    COMP_IDLE = "#d0d0d0"

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, bg="#0a0e1a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.zoom = 1.0
        self.off_x = self.off_y = 0
        self.components = []
        self.colors = {}
        self.active = set()
        self.hl = set()
        self.bounds = (0, 100, 0, 80)
        self.side = "Bottom"
        self.board_layers = None
        self._px = self._py = 0
        self._last_size = (0, 0)
        self.canvas.bind("<MouseWheel>", lambda e: self._do_zoom(e, 1.15 if e.delta < 0 else 0.87))
        self.canvas.bind("<Button-4>", lambda e: self._do_zoom(e, 0.87))
        self.canvas.bind("<Button-5>", lambda e: self._do_zoom(e, 1.15))
        self.canvas.bind("<ButtonPress-1>", lambda e: setattr(self, '_px', e.x) or setattr(self, '_py', e.y))
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<Configure>", self._on_resize)

    def load(self, comps, side, colors, active, bounds, board_layers=None):
        # Rotation and shape are needed for faithful rendering (previously every
        # component was drawn as a horizontal rectangle).
        self.components = [(c.designator, c.x, c.y, c.w, c.h, c.prefix,
                            c.rotation, c.shape)
                           for c in comps if c.layer == side]
        self.side = side; self.colors = colors; self.active = active
        self.bounds = bounds; self.hl = set()
        self.board_layers = board_layers
        self.canvas.after(100, self.fit)

    def highlight(self, desigs):
        new = set(desigs) if desigs else set()
        if new != self.hl:          # avoid redraw when moving between child
            self.hl = new           # widgets of the same legend row
            self.draw()

    def fit(self):
        x1, x2, y1, y2 = self.bounds
        cw = max(self.canvas.winfo_width(), 100)
        ch = max(self.canvas.winfo_height(), 100)
        bw, bh = max(x2-x1, 1), max(y2-y1, 1)
        self.zoom = min(cw/bw, ch/bh) * 0.88
        self.off_x = (cw - bw*self.zoom)/2 - x1*self.zoom
        self.off_y = (ch - bh*self.zoom)/2 - y1*self.zoom
        self.draw()

    def _on_resize(self, e):
        # Re-centre when the window is resized
        if (e.width, e.height) != self._last_size and self.components:
            self._last_size = (e.width, e.height)
            self.fit()

    def _tx(self, x):
        if self.side == "Bottom": x = self.bounds[0] + self.bounds[1] - x
        return x * self.zoom + self.off_x

    def _ty(self, y):
        return (self.bounds[3] - y + self.bounds[2]) * self.zoom + self.off_y

    def _corners(self, cx, cy, w, h, rot):
        """Corners of a rotated rectangle in canvas coordinates.
        Offsets are computed in board coordinates and mirrored along X for the
        Bottom view - same as in the PDF."""
        X, Y = self._tx(cx), self._ty(cy)
        rad = math.radians(rot)
        cs, sn = math.cos(rad), math.sin(rad)
        pts = []
        for px, py in ((-w/2, -h/2), (w/2, -h/2), (w/2, h/2), (-w/2, h/2)):
            dx = px*cs - py*sn
            dy = px*sn + py*cs
            if self.side == "Bottom":
                dx = -dx
            pts += [X + dx*self.zoom, Y - dy*self.zoom]
        return X, Y, pts

    def draw(self):
        c = self.canvas; c.delete("all")
        b = self.bounds
        has_gerber = bool(self.board_layers)

        # Always draw white board background
        bx1, by1 = self._tx(b[0]+1), self._ty(b[3]-1)
        bx2, by2 = self._tx(b[1]-1), self._ty(b[2]+1)
        c.create_rectangle(min(bx1,bx2), min(by1,by2), max(bx1,bx2), max(by1,by2),
                          fill=self.BOARD_BG, outline="#a8a090", width=2)

        # 1) Board outline from Gerber
        if has_gerber and self.board_layers.get("outline"):
            self._draw_entities(self.board_layers["outline"], "#888877", 1.0)

        # 2) Gerber overlay (dark on white board) — UNDER colored blocks
        if has_gerber:
            key = "bottom" if self.side == "Bottom" else "top"
            if self.board_layers.get(key):
                self._draw_entities(self.board_layers[key], "#444444", 0.7)

        # 3) Colored blocks WITH stipple on top (semi-transparent over gerber)
        has_hl = len(self.hl) > 0
        for d, cx, cy, w, h, pfx, rot, shape in self.components:
            X, Y, pts = self._corners(cx, cy, w, h, rot)
            sw, sh = w*self.zoom, h*self.zoom
            is_act = d in self.active
            is_hl = d in self.hl
            col = self.colors.get(d, self.COMP_IDLE)
            if has_hl:
                fill = col if is_hl else "#e8e8e8"
                ol = "#555" if is_hl else "#ccc"
                lw = 1.0 if is_hl else 0.2
            else:
                fill = col if is_act else self.COMP_IDLE
                ol = "#555" if is_act else "#ccc"
                lw = 0.8 if is_act else 0.3
            stip = "gray50" if (is_act or is_hl) and has_gerber else ""
            if shape == "circle":
                r = w/2*self.zoom
                c.create_oval(X-r, Y-r, X+r, Y+r, fill=fill, outline=ol,
                             width=lw, stipple=stip)
            elif rot % 360 == 0:
                c.create_rectangle(X-sw/2, Y-sh/2, X+sw/2, Y+sh/2,
                                   fill=fill, outline=ol, width=lw, stipple=stip)
            else:
                c.create_polygon(pts, fill=fill, outline=ol,
                                width=max(lw, 0.5), stipple=stip)

        # 4) Labels (topmost)
        for d, cx, cy, w, h, pfx, rot, shape in self.components:
            X, Y, pts = self._corners(cx, cy, w, h, rot)
            sw, sh = w*self.zoom, h*self.zoom
            is_act = d in self.active
            is_hl = d in self.hl
            if not (is_act or is_hl): continue
            if (pfx in ("U","J","X","XO","T","VT","F","S","SW") or max(w,h)>=4) and sw > 14:
                fs = max(7, min(11, int(min(sw,sh)*0.32)))
                c.create_text(X, Y, text=d, fill="white",
                    font=("Segoe UI", fs, "bold"))

    def _draw_entities(self, entities, color, width_scale):
        """Render Gerber/DXF entities on Canvas."""
        c = self.canvas
        for item in entities:
            etype = item[0]; data = item[1]
            try:
                if etype == "line":
                    x1, y1, x2, y2 = data[0], data[1], data[2], data[3]
                    w = data[4] if len(data) > 4 else 0.1
                    lw = max(0.5, w * self.zoom * width_scale)
                    c.create_line(self._tx(x1), self._ty(y1),
                                 self._tx(x2), self._ty(y2),
                                 fill=color, width=lw, capstyle="round")
                elif etype == "circle":
                    cx, cy, r = data
                    sx = self._tx(cx) - r * self.zoom
                    sy = self._ty(cy) - r * self.zoom
                    ex = self._tx(cx) + r * self.zoom
                    ey = self._ty(cy) + r * self.zoom
                    c.create_oval(sx, sy, ex, ey, fill=color, outline=color)
                elif etype == "rect":
                    rx, ry, rw, rh = data
                    sx = self._tx(rx)
                    sy = self._ty(ry + rh)
                    ex = self._tx(rx + rw)
                    ey = self._ty(ry)
                    c.create_rectangle(min(sx,ex), min(sy,ey),
                                      max(sx,ex), max(sy,ey),
                                      fill=color, outline=color)
                elif etype == "polyline":
                    pts, closed = data
                    coords = []
                    for px, py in pts:
                        coords.extend([self._tx(px), self._ty(py)])
                    if len(coords) >= 4:
                        if closed:
                            c.create_polygon(coords, fill="", outline=color,
                                           width=max(0.5, width_scale))
                        else:
                            c.create_line(coords, fill=color,
                                        width=max(0.5, width_scale))
                elif etype == "polygon":
                    coords = []
                    for px, py in data:
                        coords.extend([self._tx(px), self._ty(py)])
                    if len(coords) >= 6:
                        c.create_polygon(coords, fill=color, outline=color)
                elif etype == "garc":
                    x1, y1, x2, y2, acx, acy, cw_flag, w = data
                    r = math.sqrt((x1-acx)**2 + (y1-acy)**2)
                    if r < 0.001: continue
                    is_full = abs(x1-x2) < 0.001 and abs(y1-y2) < 0.001
                    lw = max(0.5, w*self.zoom*width_scale)
                    if is_full:
                        # Full circle — draw as oval
                        tcx, tcy = self._tx(acx), self._ty(acy)
                        tr = r * self.zoom
                        c.create_oval(tcx-tr, tcy-tr, tcx+tr, tcy+tr,
                                    outline=color, width=lw, fill=color)
                    else:
                        sa = math.atan2(y1-acy, x1-acx)
                        ea = math.atan2(y2-acy, x2-acx)
                        if cw_flag and ea > sa: ea -= 2*math.pi
                        if not cw_flag and ea < sa: ea += 2*math.pi
                        n = max(8, int(abs(ea-sa)*r*2))
                        coords = []
                        for i in range(n+1):
                            a = sa + (ea-sa)*i/n
                            coords.extend([self._tx(acx+r*math.cos(a)),
                                          self._ty(acy+r*math.sin(a))])
                        if len(coords) >= 4:
                            c.create_line(coords, fill=color, width=lw)
            except Exception:
                continue

    def _do_zoom(self, e, f):
        self.off_x = e.x - (e.x - self.off_x)*f
        self.off_y = e.y - (e.y - self.off_y)*f
        self.zoom *= f; self.draw()

    def _pan(self, e):
        self.off_x += e.x - self._px; self.off_y += e.y - self._py
        self._px, self._py = e.x, e.y; self.draw()


class App:
    ROWS = 7
    # 2026 design tokens
    C_BG    = "#0a0e1a"   # app background
    C_PANEL = "#111827"   # side panels
    C_CARD  = "#1a2236"   # cards
    C_HOVER = "#232d45"   # hover state
    C_TEXT  = "#f8fafc"   # primary text
    C_MUTED = "#8b96ab"   # secondary text
    C_FAINT = "#4b5568"   # faint labels
    C_ACCENT = "#4f7cff"  # accent blue
    C_GREEN = "#10b981"   # success
    # Type scale — one ladder instead of ad-hoc sizes
    F_BRAND = ("Segoe UI", 13, "bold")
    F_TITLE = ("Segoe UI", 12, "bold")
    F_BTN   = ("Segoe UI", 10, "bold")
    F_BODY  = ("Segoe UI", 10)
    F_SMALL = ("Segoe UI", 9)
    F_MICRO = ("Segoe UI", 7, "bold")
    # Metrics
    BTN_H = 32            # every toolbar button is this tall
    SIDE_BTN_W = 92       # BOTTOM and TOP share a width
    CARD_W = 188

    def __init__(self, root):
        self.root = root
        self.root.title(f"PCB Assembly Studio v{APP_VERSION}")
        self.root.geometry("1200x750")
        self.root.configure(bg="#0a0e1a")
        self.pnp = {}; self.bom_data = []; self.groups = {"Top":[],"Bottom":[]}
        self.side = "Bottom"; self.page = 0; self.board_layers = None
        self.sort_mode = "standard"  # standard | bom | custom
        self.custom_order = []  # user-defined prefix order
        self.main_built = False
        self.loaded = {"pnp": False, "bom": False, "gerber": False}
        self.meta = {"project": "Assembly", "designer": "", "edition": "v1.0"}
        self._show_welcome()

    # ══════════════════════════════════════════════════════════════════
    #  WELCOME SCREEN
    # ══════════════════════════════════════════════════════════════════

    def _show_welcome(self):
        self.welcome = tk.Frame(self.root, bg="#0a0e1a")
        self.welcome.pack(fill="both", expand=True)

        left = tk.Frame(self.welcome, bg="#111827", width=260)
        left.pack(side="left", fill="y"); left.pack_propagate(False)

        tk.Label(left, text="REQUIRED FILES", font=("Segoe UI", 9, "bold"),
                fg="#8b96ab", bg="#111827").pack(fill="x", padx=16, pady=(20, 12))

        self.file_dots = {}
        for key, name, ext in [("pnp","Pick & Place  (required)",".txt / .csv / .pos"),
                                ("bom","BOM",".txt / .csv / .xlsx"),
                                ("gerber","Gerber ZIP / DXF",".zip / .dxf")]:
            row = tk.Frame(left, bg="#111827"); row.pack(fill="x", padx=16, pady=3)
            dot = tk.Label(row, text="●", font=("Segoe UI", 12), fg="#f59e0b", bg="#111827")
            dot.pack(side="left", padx=(0, 8))
            self.file_dots[key] = dot
            tk.Label(row, text=name, font=("Segoe UI", 10), fg="#f8fafc",
                    bg="#111827").pack(side="left")
            tk.Label(row, text=ext, font=("Segoe UI", 8), fg="#8b96ab",
                    bg="#111827").pack(side="right")

        tk.Frame(left, bg="#1a2236", height=1).pack(fill="x", padx=16, pady=16)
        tk.Label(left, text="Export from Altium:\n\n"
            "• File → Assembly Outputs\n  → Pick and Place\n\n"
            "• Reports → BOM → Export\n\n"
            "• File → Fabrication Outputs\n  → Gerber Files\n\n"
            "KiCad: .pos file +\n  Gerber with layer names",
            font=("Segoe UI", 9), fg="#8b96ab", bg="#111827",
            justify="left", anchor="nw", wraplength=220).pack(fill="x", padx=16)

        # Drop zones (one per file type)
        right = tk.Frame(self.welcome, bg="#0a0e1a")
        right.pack(side="left", fill="both", expand=True, padx=20, pady=20)

        self.drop_zones = {}
        for key, title, ftypes, exts in [
            ("pnp", "Pick & Place", [("Text/CSV/POS","*.txt *.csv *.pos")], ".txt .csv .pos"),
            ("bom", "BOM", [("Text/CSV/Excel","*.txt *.csv *.xlsx *.xls")], ".txt .csv .xlsx"),
            ("gerber", "Gerber ZIP / DXF", [("ZIP/DXF","*.zip *.dxf *.dwg")], ".zip .dxf"),
        ]:
            zone = tk.Frame(right, bg="#111827", highlightthickness=2,
                           highlightbackground="#334155", cursor="hand2")
            zone.pack(fill="x", pady=4, ipady=12)

            inner = tk.Frame(zone, bg="#111827")
            inner.pack(fill="x", padx=16)

            lbl = tk.Label(inner, text=f"📁  Drop {title} here or click to browse",
                font=("Segoe UI", 10), fg="#8b96ab", bg="#111827", anchor="w")
            lbl.pack(side="left")
            tk.Label(inner, text=exts, font=("Segoe UI", 8), fg="#4b5568",
                    bg="#111827").pack(side="right")

            self.drop_zones[key] = (zone, lbl)

            # Click to browse
            def make_browse(k, ft):
                def browse(e=None):
                    p = filedialog.askopenfilename(filetypes=ft + [("All","*.*")])
                    if p: self._load_file_typed(k, p)
                return browse
            bc = make_browse(key, ftypes)
            for w in [zone, inner, lbl]:
                w.bind("<Button-1>", bc)

            # DnD per zone
            if HAS_DND:
                try:
                    zone.drop_target_register(DND_FILES)
                    def make_drop(k):
                        def on_drop(e):
                            p = e.data.strip().strip("{}")
                            if os.path.isfile(p): self._load_file_typed(k, p)
                        return on_drop
                    zone.dnd_bind("<<Drop>>", make_drop(key))
                except Exception:
                    pass

            # Hover effect
            zone.bind("<Enter>", lambda e, z=zone: z.configure(highlightbackground="#3b82f6"))
            zone.bind("<Leave>", lambda e, z=zone: z.configure(highlightbackground="#334155"))

        # Start button (appears after PnP loaded)
        self.start_btn_enabled = False
        self._start_btn_parent = right
        self.start_btn = self._rounded_btn(right, "▶  Open project",
            self._try_open_project,
            bg="#2a3350", fg="#5b6478", hover="#2a3350",
            font=("Segoe UI", 12, "bold"), padx=24, pady=10, radius=10)
        self.start_btn.pack(pady=(16, 0))

        tk.Label(right, text="100% offline · all files stay on your computer",
                font=("Segoe UI", 8), fg="#334155", bg="#0a0e1a").pack(
                    side="bottom", pady=4)

    def _load_file_typed(self, ftype, path):
        try:
            if ftype == "pnp":
                self.pnp = parse_pick_place(path)
                self.loaded["pnp"] = True
                if self.meta.get("project") in ("", "Assembly"):
                    self.meta["project"] = os.path.splitext(os.path.basename(path))[0]
                self._regroup()
            elif ftype == "bom":
                self.bom_data = parse_bom(path)
                if not self.bom_data:
                    raise ValueError(
                        "The BOM was read but contains no component rows.\n"
                        "Check the Designator / Comment columns.")
                self.loaded["bom"] = True
                if self.pnp:
                    self._regroup()
            elif ftype == "gerber":
                self.board_layers = load_board_drawing(path)
                self.loaded["gerber"] = True
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        # Update dots
        for k, dot in self.file_dots.items():
            dot.configure(fg="#22c55e" if self.loaded[k] else "#f59e0b")

        # Update zone label
        if ftype in self.drop_zones:
            zone, lbl = self.drop_zones[ftype]
            fname = os.path.basename(path)
            lbl.configure(text=f"✅  {fname}", fg="#22c55e")

        # Enable start button
        if self.loaded["pnp"]:
            if not self.start_btn_enabled:
                self.start_btn_enabled = True
                # Rebuild button in enabled style
                self.start_btn.destroy()
                self.start_btn = self._rounded_btn(
                    self._start_btn_parent,
                    "▶  Open project", self._try_open_project,
                    bg="#4f7cff", fg="white", hover="#3b63d9",
                    font=("Segoe UI", 12, "bold"), padx=24, pady=10, radius=10)
                self.start_btn.pack(pady=(16, 0))

    # ══════════════════════════════════════════════════════════════════
    #  MAIN WORKSPACE
    # ══════════════════════════════════════════════════════════════════

    def _try_open_project(self):
        if self.start_btn_enabled:
            self._switch_to_main()

    def _switch_to_main(self):
        if self.main_built:
            return
        self.welcome.destroy()
        self.main_built = True
        self._build_main()
        self.root.after(200, self._refresh)

    def _build_main(self):
        # Toolbar
        tb = tk.Frame(self.root, bg="#111827", height=52)
        tb.pack(fill="x"); tb.pack_propagate(False)
        tk.Label(tb, text="▦  PCB Assembly Studio", font=self.F_BRAND,
                fg="white", bg="#111827").pack(side="left", padx=14)

        self._side_btn_frame = tk.Frame(tb, bg="#111827")
        self._side_btn_frame.pack(side="left", padx=6)
        self._rebuild_side_btns()

        self._rounded_btn(tb, "📂  Import", self._import_more,
            bg="#1a2236", fg="#8b96ab", hover="#232d45",
            font=self.F_BTN, padx=14, min_h=self.BTN_H).pack(
                side="left", padx=8)
        self.export_btn = self._rounded_btn(tb, "📄  Export PDF",
            self._export_pdf, bg="#10b981", hover="#0d9668",
            font=self.F_BTN, padx=16, min_h=self.BTN_H)
        self.export_btn.pack(side="right", padx=14)

        body = tk.Frame(self.root, bg="#0a0e1a")
        body.pack(fill="both", expand=True)

        # Left panel: sort mode
        self.left_panel = tk.Frame(body, bg="#111827", width=216)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)

        # Board
        bf = tk.Frame(body, bg="#0a0e1a")
        bf.pack(side="left", fill="both", expand=True)
        self.board = BoardCanvas(bf)

        # Right panel: legend
        self.right_panel = tk.Frame(body, bg="#111827", width=320)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)

        # Status
        self.status = tk.Label(self.root, text="", bg="#111827", fg="#8b96ab",
            font=("Segoe UI", 9), anchor="w", padx=14)
        self.status.pack(fill="x")

    # ── Left panel: sort modes ────────────────────────────────────────

    def _build_left(self):
        for w in self.left_panel.winfo_children(): w.destroy()

        tk.Label(self.left_panel, text="SORT ORDER",
                font=("Segoe UI", 9, "bold"), fg="#8b96ab", bg="#111827",
                anchor="w").pack(fill="x", padx=14, pady=(14, 8))

        modes = [
            ("standard", "Standard", "SMD passive → ICs → THT", "#3b82f6"),
            ("bom", "As in BOM", "Original BOM order", "#a855f7"),
            ("custom", "Custom", "User-defined order", "#f59e0b"),
        ]
        sel_bg = {"standard": "#1e3a5f", "bom": "#2d1f4e", "custom": "#3d2e1a"}

        for mode_id, name, desc, color in modes:
            is_sel = self.sort_mode == mode_id
            base   = sel_bg.get(mode_id, "#1a2236") if is_sel else "#1a2236"
            border = color if is_sel else "#1a2236"

            W, H, R = self.CARD_W, 64, 12
            cv = tk.Canvas(self.left_panel, width=W, height=H,
                           bg="#111827", highlightthickness=0, cursor="hand2")
            cv.pack(padx=14, pady=4)

            def render(canvas=cv, fill=base, brd=border, nm=name, ds=desc,
                       col=color, sel=is_sel, mid=mode_id):
                canvas.delete("all")
                r = R
                x1, y1, x2, y2 = 2, 2, W - 2, H - 2
                pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
                       x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
                canvas.create_polygon(pts, smooth=True, fill=fill,
                                      outline=brd, width=2)
                canvas.create_text(16, 21, text=nm, anchor="w",
                                   font=self.F_BODY if not sel else self.F_BTN,
                                   fill=col if sel else "#94a3b8")
                canvas.create_text(16, 43, text=ds, anchor="w",
                                   font=("Segoe UI", 7), fill=self.C_MUTED)
                ind = col if sel else "#475569"
                rx = W - 26
                canvas.create_oval(rx, 13, rx+14, 27, outline=ind, width=2)
                if sel:
                    canvas.create_oval(rx+4, 17, rx+10, 23, fill=ind, outline="")
                if mid == "custom":
                    p = canvas.create_text(W-46, 20, text="✏",
                                           font=self.F_SMALL, fill=self.C_MUTED)
                    canvas.tag_bind(p, "<Button-1>",
                                    lambda e: self._open_custom_dialog())

            render()
            # hover feedback — every other interactive element has it
            cv.bind("<Enter>", lambda e, c=cv, b=base, r_=render:
                    r_(canvas=c, fill=self.C_HOVER))
            cv.bind("<Leave>", lambda e, c=cv, b=base, r_=render:
                    r_(canvas=c, fill=b))
            cv.bind("<Button-1>", lambda e, m=mode_id: self._set_sort(m))

    def _set_sort(self, mode):
        self.sort_mode = mode
        self.page = 0
        self._regroup()
        self._refresh()

    def _regroup(self):
        if not self.pnp: return

        # Build groups from BOM or auto
        if self.bom_data:
            self.groups = group_from_bom(self.bom_data, self.pnp)
        else:
            self.groups = group_auto(self.pnp)

        # Apply sort based on mode
        if self.sort_mode == "bom":
            # Keep original BOM order — don't sort
            pass
        elif self.sort_mode == "custom" and self.custom_order:
            order_map = {p: i for i, p in enumerate(self.custom_order)}
            for layer in self.groups:
                self.groups[layer].sort(
                    key=lambda g: order_map.get(
                        extract_prefix(g.designators[0]) if g.designators else "", 999))
        else:
            # Standard: assembly order (small passives → ICs → THT)
            for layer in self.groups:
                self.groups[layer].sort(
                    key=lambda g: PREFIX_ORDER.get(
                        extract_prefix(g.designators[0]) if g.designators else "", 50))

        # DNI groups always go last on a side - same order as in the PDF
        for layer in self.groups:
            self.groups[layer].sort(key=lambda g: g.dni)

    def _open_custom_dialog(self):
        """Dialog for custom prefix ordering."""
        side_groups = self.groups.get(self.side, [])
        prefixes = []
        seen = set()
        for g in side_groups:
            p = extract_prefix(g.designators[0]) if g.designators else ""
            if p and p not in seen:
                prefixes.append(p)
                seen.add(p)

        if self.custom_order:
            # Merge: keep custom order, add any new prefixes at end
            merged = [p for p in self.custom_order if p in seen]
            for p in prefixes:
                if p not in merged:
                    merged.append(p)
            prefixes = merged

        dlg = tk.Toplevel(self.root)
        dlg.title("Custom sort order")
        dlg.geometry("260x400")
        dlg.configure(bg="#111827")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Drag to reorder:", font=("Segoe UI", 10, "bold"),
                fg="#f8fafc", bg="#111827").pack(padx=12, pady=(12, 8))

        listbox = tk.Listbox(dlg, font=("Consolas", 12), bg="#1a2236",
            fg="#f8fafc", selectbackground="#2563eb", selectforeground="white",
            relief="flat", height=15, activestyle="none")
        listbox.pack(fill="both", expand=True, padx=12, pady=4)
        for p in prefixes:
            listbox.insert("end", f"  {p}")

        btn_frame = tk.Frame(dlg, bg="#111827")
        btn_frame.pack(fill="x", padx=12, pady=4)
        tk.Button(btn_frame, text="▲ Up", font=("Segoe UI", 9), relief="flat",
            bg="#1a2236", fg="#8b96ab",
            command=lambda: self._move_item(listbox, -1)).pack(side="left", padx=2)
        tk.Button(btn_frame, text="▼ Down", font=("Segoe UI", 9), relief="flat",
            bg="#1a2236", fg="#8b96ab",
            command=lambda: self._move_item(listbox, 1)).pack(side="left", padx=2)

        def apply():
            self.custom_order = [listbox.get(i).strip() for i in range(listbox.size())]
            self.sort_mode = "custom"
            self._regroup()
            self._refresh()
            dlg.destroy()

        tk.Button(btn_frame, text="Apply", font=("Segoe UI", 10, "bold"),
            relief="flat", bg="#4f7cff", fg="white",
            command=apply).pack(side="right", padx=2)

    def _move_item(self, lb, direction):
        sel = lb.curselection()
        if not sel: return
        idx = sel[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= lb.size(): return
        item = lb.get(idx)
        lb.delete(idx)
        lb.insert(new_idx, item)
        lb.selection_set(new_idx)

    # ── Right panel: legend ───────────────────────────────────────────

    def _rounded_swatch(self, parent, color, w=18, h=18, r=5):
        """Canvas-based rounded rectangle swatch."""
        cv = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"),
                      highlightthickness=0)
        x1, y1, x2, y2 = 1, 1, w-1, h-1
        pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
               x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
        cv.create_polygon(pts, smooth=True, fill=color, outline="")
        return cv

    @staticmethod
    def _dim(hex_color, factor=0.45):
        """Darken a #rrggbb colour — used for disabled button states."""
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
        return "#%02x%02x%02x" % (int(r*factor), int(g*factor), int(b*factor))

    def _rounded_btn(self, parent, text, command, bg, fg="white",
                     hover=None, font=("Segoe UI", 10, "bold"),
                     padx=16, pady=7, radius=9, min_w=0, min_h=0):
        """Canvas button with rounded corners + hover / press / disabled states.

        min_w / min_h let related buttons (e.g. a BOTTOM/TOP toggle pair)
        share identical dimensions regardless of label length.
        """
        import tkinter.font as tkfont
        hover = hover or bg
        f = tkfont.Font(font=font)
        W = max(f.measure(text) + padx * 2, min_w)
        H = max(f.metrics("linespace") + pady * 2, min_h)

        cv = tk.Canvas(parent, width=W, height=H, bg=parent.cget("bg"),
                       highlightthickness=0, cursor="hand2")
        cv._enabled = True

        def draw(fill=None):
            cv.delete("all")
            if not cv._enabled:
                fill, txt = self._dim(bg), self.C_FAINT
            else:
                fill, txt = (fill or bg), fg
            r = radius
            x1, y1, x2, y2 = 1, 1, W - 1, H - 1
            pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r, x2,y2,
                   x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1]
            cv.create_polygon(pts, smooth=True, fill=fill, outline="")
            cv.create_text(W/2, H/2, text=text, font=font, fill=txt)

        cv._draw = draw
        draw()
        cv.bind("<Enter>",           lambda e: draw(hover) if cv._enabled else None)
        cv.bind("<Leave>",           lambda e: draw())
        cv.bind("<ButtonPress-1>",   lambda e: draw(self._dim(hover, .8)) if cv._enabled else None)
        cv.bind("<ButtonRelease-1>", lambda e: (draw(hover), command()) if cv._enabled else None)
        return cv

    @staticmethod
    def _set_btn_enabled(btn, enabled):
        """Toggle a _rounded_btn between active and greyed-out."""
        if btn is None or not hasattr(btn, "_draw"):
            return
        btn._enabled = enabled
        btn.configure(cursor="hand2" if enabled else "arrow")
        btn._draw()

    def _build_legend(self):
        for w in self.right_panel.winfo_children(): w.destroy()

        sg = self.groups.get(self.side, [])
        tp = max(1, math.ceil(len(sg) / self.ROWS))

        hdr = tk.Frame(self.right_panel, bg=self.C_PANEL)
        hdr.pack(fill="x", padx=16, pady=(16, 4))
        tk.Label(hdr, text="Legend", font=("Segoe UI", 12, "bold"),
                fg=self.C_TEXT, bg=self.C_PANEL).pack(side="left")
        tk.Label(hdr, text=f"  {len(sg)}", font=("Segoe UI", 11),
                fg=self.C_MUTED, bg=self.C_PANEL).pack(side="left")

        nav = tk.Frame(hdr, bg=self.C_PANEL); nav.pack(side="right")
        self._rounded_btn(nav, "‹", self._prev,
            bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
            font=self.F_BODY, padx=4, pady=3, radius=7,
            min_w=28, min_h=26).pack(side="left")
        tk.Label(nav, text=f"{self.page+1} / {tp}", font=("Segoe UI", 9),
                fg=self.C_MUTED, bg=self.C_PANEL).pack(side="left", padx=6)
        self._rounded_btn(nav, "›", self._next,
            bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
            font=self.F_BODY, padx=4, pady=3, radius=7,
            min_w=28, min_h=26).pack(side="left")

        ch = tk.Frame(self.right_panel, bg=self.C_PANEL)
        ch.pack(fill="x", padx=16, pady=(10, 4))
        ch.columnconfigure(1, weight=1)
        tk.Label(ch, text="", width=2, bg=self.C_PANEL).grid(row=0, column=0)
        tk.Label(ch, text="PART", font=("Segoe UI", 7, "bold"),
                fg=self.C_FAINT, bg=self.C_PANEL, anchor="w").grid(
                    row=0, column=1, sticky="w")
        tk.Label(ch, text="QTY", font=("Segoe UI", 7, "bold"),
                fg=self.C_FAINT, bg=self.C_PANEL, width=4).grid(row=0, column=2)
        tk.Label(ch, text="REFS", font=("Segoe UI", 7, "bold"),
                fg=self.C_FAINT, bg=self.C_PANEL, width=13,
                anchor="e").grid(row=0, column=3)

        install_colors = self._install_colors()
        start = self.page * self.ROWS
        for i, g in enumerate(sg[start:start+self.ROWS]):
            ci = start + i
            color = DNI_COLOR if g.dni else install_colors.get(ci, PALETTE[0])

            row = tk.Frame(self.right_panel, bg=self.C_PANEL, cursor="hand2")
            row.pack(fill="x", padx=10, pady=2)
            row.columnconfigure(1, weight=1)

            sw = self._rounded_swatch(row, color)
            sw.grid(row=0, column=0, rowspan=2, padx=(6, 10), pady=3)

            title = (g.comment or "—")
            if g.dni:
                title += "  ⛔"
            tk.Label(row, text=title, font=("Segoe UI", 10, "bold"),
                    fg="#f87171" if g.dni else self.C_TEXT,
                    bg=self.C_PANEL, anchor="w").grid(
                        row=0, column=1, sticky="w", padx=(0, 4))

            desc = g.description if g.description != g.comment else ""
            if desc:
                tk.Label(row, text=desc[:45], font=("Segoe UI", 7),
                        fg=self.C_MUTED, bg=self.C_PANEL, anchor="w").grid(
                            row=1, column=1, sticky="w", padx=(0, 4))

            tk.Label(row, text=str(g.quantity), font=("Segoe UI", 12, "bold"),
                    fg=self.C_TEXT, bg=self.C_PANEL, width=4,
                    anchor="e").grid(row=0, column=2, rowspan=2, padx=2, sticky="e")

            refs_short = ", ".join(g.designators[:3])
            if len(g.designators) > 3:
                refs_short += "…"
            refs_label = tk.Label(row, text=refs_short, font=("Segoe UI", 8),
                    fg=self.C_MUTED, bg=self.C_PANEL, width=13, anchor="e")
            refs_label.grid(row=0, column=3, rowspan=2, padx=(2, 6), sticky="e")

            full_refs = ", ".join(g.designators)
            if len(g.designators) > 3:
                self._add_tooltip(refs_label, full_refs)
                refs_label._skip_hover = True

            self._bind_hover(row, g.designators)

    def _bind_hover(self, widget, desigs):
        widget.bind("<Enter>", lambda e, dd=desigs: self.board.highlight(dd))
        widget.bind("<Leave>", lambda e: self.board.highlight(None))
        for ch in widget.winfo_children():
            if getattr(ch, '_skip_hover', False):
                continue
            self._bind_hover(ch, desigs)

    def _add_tooltip(self, widget, text):
        """Show tooltip with full text on hover, clamped to screen bounds."""
        self._tip_window = None
        def show(e):
            try:
                if hasattr(self, '_tip_window') and self._tip_window:
                    self._tip_window.destroy()
            except: pass
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.configure(bg="#111827")
            lbl = tk.Label(tw, text=text, font=("Segoe UI", 9),
                          bg="#111827", fg="#f8fafc", padx=10, pady=6,
                          wraplength=350, justify="left",
                          highlightthickness=1, highlightbackground="#475569")
            lbl.pack()
            tw.update_idletasks()
            # Clamp position to screen
            tw_w = tw.winfo_reqwidth()
            tw_h = tw.winfo_reqheight()
            scr_w = tw.winfo_screenwidth()
            scr_h = tw.winfo_screenheight()
            x = e.x_root + 15
            y = e.y_root + 10
            if x + tw_w > scr_w - 10:
                x = e.x_root - tw_w - 10
            if y + tw_h > scr_h - 40:
                y = e.y_root - tw_h - 10
            tw.wm_geometry(f"+{x}+{y}")
            self._tip_window = tw
        def hide(e):
            try:
                if hasattr(self, '_tip_window') and self._tip_window:
                    self._tip_window.destroy()
                    self._tip_window = None
            except: pass
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _install_colors(self):
        """Colour for each non-DNI group on a side, by running index -
        the same formula as generate_pdf, so GUI and PDF colours match."""
        sg = self.groups.get(self.side, [])
        colors = {}
        k = 0
        for ci, g in enumerate(sg):
            if g.dni:
                continue
            colors[ci] = PALETTE[k % len(PALETTE)]
            k += 1
        return colors

    # ── Navigation & refresh ──────────────────────────────────────────

    def _rebuild_side_btns(self):
        for w in self._side_btn_frame.winfo_children():
            w.destroy()
        for s in ["Bottom", "Top"]:
            is_active = (s == self.side)
            b = self._rounded_btn(self._side_btn_frame, s.upper(),
                lambda s=s: self._set_side(s),
                bg="#4f7cff" if is_active else "#1a2236",
                fg="white" if is_active else "#8b96ab",
                hover="#3b63d9" if is_active else "#232d45",
                font=self.F_BTN, padx=14,
                min_w=self.SIDE_BTN_W, min_h=self.BTN_H)
            b.pack(side="left", padx=3)

    def _set_side(self, s):
        self.side = s; self.page = 0
        self._rebuild_side_btns()
        self._refresh()

    def _prev(self):
        if self.page > 0: self.page -= 1; self._refresh()

    def _next(self):
        sg = self.groups.get(self.side, [])
        if self.page < math.ceil(len(sg) / self.ROWS) - 1:
            self.page += 1; self._refresh()

    def _refresh(self):
        sg = self.groups.get(self.side, [])
        start = self.page * self.ROWS
        pg = sg[start:start+self.ROWS]
        install_colors = self._install_colors()
        colors = {}
        for ci, g in enumerate(sg):
            c = DNI_COLOR if g.dni else install_colors.get(ci, PALETTE[0])
            for d in g.designators: colors[d] = c
        active = set()
        for g in pg: active.update(g.designators)
        bounds = compute_bounds(self.pnp, self.board_layers)
        self.board.load(list(self.pnp.values()), self.side, colors, active, bounds,
                       board_layers=self.board_layers)
        self._build_left()
        self._build_legend()
        top = sum(1 for c in self.pnp.values() if c.layer == "Top")
        bot = sum(1 for c in self.pnp.values() if c.layer == "Bottom")
        self.status.configure(text=f"{len(self.pnp)} comp (Top:{top} Bot:{bot}) · "
            f"{len(sg)} groups · {self.side} · scroll=zoom drag=pan")

    def _sniff_type(self, path):
        """Detect the type of an imported file from its extension and contents."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".zip": return "gerber"
        if ext in (".dxf", ".dwg"): return "gerber"
        if ext in (".xlsx", ".xls"): return "bom"
        if ext == ".pos": return "pnp"
        try:
            head = _read_text(path)[:4000].lower()
        except Exception:
            head = ""
        if (re.search(r'designator|(^|\W)ref(\W|$)', head)
                and re.search(r'layer|side', head)
                and re.search(r'center-x|mid x|pos ?x', head)):
            return "pnp"
        return "bom" if self.pnp else "pnp"

    def _import_more(self):
        paths = filedialog.askopenfilenames(title="Import",
            filetypes=[("All","*.txt *.csv *.pos *.xlsx *.xls *.zip *.dxf *.dwg"),
                       ("All","*.*")])
        for p in paths:
            self._load_file_typed(self._sniff_type(p), p)
        if self.main_built: self._refresh()

    # ── Export ────────────────────────────────────────────────────────

    def _ask_metadata(self):
        """Title block dialog: project / designer / edition. None = cancelled."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Title block")
        dlg.configure(bg="#111827")
        dlg.transient(self.root); dlg.grab_set()
        fields = [("Project", self.meta.get("project", "Assembly")),
                  ("Designer", self.meta.get("designer", "")),
                  ("Edition", self.meta.get("edition", "v1.0"))]
        entries = []
        for i, (lbl, default) in enumerate(fields):
            tk.Label(dlg, text=lbl, bg="#111827", fg="#8b96ab",
                     font=("Segoe UI", 9)).grid(row=i, column=0, sticky="w",
                                               padx=12, pady=6)
            e = tk.Entry(dlg, width=30, bg="#1a2236", fg="#f8fafc",
                         insertbackground="white", relief="flat")
            e.insert(0, default)
            e.grid(row=i, column=1, padx=12, pady=6)
            entries.append(e)
        out = {}
        def ok():
            out["project"], out["designer"], out["edition"] = \
                [e.get().strip() for e in entries]
            dlg.destroy()
        tk.Button(dlg, text="Export", command=ok, bg="#10b981", fg="white",
                  relief="flat", padx=18, font=("Segoe UI", 10, "bold")
                  ).grid(row=3, column=1, sticky="e", padx=12, pady=10)
        entries[0].focus_set()
        dlg.bind("<Return>", lambda e: ok())
        dlg.wait_window()
        if not out:
            return None
        self.meta = out
        return out

    def _export_pdf(self):
        if not self.pnp:
            messagebox.showinfo("Info", "Load Pick & Place first"); return
        path = filedialog.asksaveasfilename(title="Export PDF",
            defaultextension=".pdf", filetypes=[("PDF","*.pdf")])
        if not path: return
        meta = self._ask_metadata()
        if meta is None: return
        # Generate in a background thread so the GUI stays responsive
        self._set_btn_enabled(self.export_btn, False)
        self.status.configure(text="Generating PDF...")
        threading.Thread(target=self._export_worker, args=(path, meta),
                         daemon=True).start()

    def _export_worker(self, path, meta):
        def progress(pn, total, *args):
            self.root.after(0, lambda: self.status.configure(
                text=f"PDF: page {pn}/{total}..."))
        try:
            pages = generate_pdf(self.pnp, self.groups, path,
                project_name=meta["project"] or "Assembly",
                designed_by=meta["designer"],
                edition=meta["edition"] or "v1.0",
                board_layers=self.board_layers,
                progress_callback=progress)
            self.root.after(0, self._export_done, path, pages, None)
        except Exception as e:
            self.root.after(0, self._export_done, path, 0, str(e))

    def _export_done(self, path, pages, err):
        self._set_btn_enabled(self.export_btn, True)
        if err:
            self.status.configure(text="Export failed")
            messagebox.showerror("Error", err)
        elif pages == 0:
            self.status.configure(text="No groups to export")
            messagebox.showinfo("Info",
                "No components to draw - check the BOM and Pick & Place files.")
        else:
            self.status.configure(
                text=f"Exported {pages} pages → {os.path.basename(path)}")
            messagebox.showinfo("Done", f"PDF: {pages} pages\n{path}")


if __name__ == "__main__":
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    app = App(root)
    root.mainloop()
