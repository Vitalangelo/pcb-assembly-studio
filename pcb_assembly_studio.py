#!/usr/bin/env python3
"""
PCB Assembly Studio
───────────────────────────────────────────────
Assembly drawing generator for Altium Designer / KiCad projects.
Standalone GUI - packaged to .exe via PyInstaller.
"""

import csv
import datetime
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

APP_VERSION = "1.1"

# ---------------------------------------------------------------------------
#  ISO drawing sheet - geometry, line widths, lettering
#  ISO 5457:1999  sheet sizes and layout      ISO 7200:2004  title block fields
#  ISO 7573:2008  parts lists                 ISO 6433:2012  part references
#  ISO 128-20     lines                       ISO 3098-1     lettering
#  ISO 5455       scales
# ---------------------------------------------------------------------------

# ISO 5457:1999 table 1 - trimmed sheet, (short side, long side) in mm
SHEET_SIZES = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0)}

# ISO 5457:1999 table 2 gives the field counts (A4 6/4, A3 8/6, A2 12/8).
# grid_edges() reproduces them from the 50 mm rule, so they are not tabulated.

# ISO 5457:1999 4.2 - the 20 mm left border doubles as the filing margin
BORDER_LEFT = 20.0
BORDER_EDGE = 10.0
ZONE_STRIP = 5.0            # width of the grid reference border
GRID_FIELD_LEN = 50.0       # ISO 5457:1999 4.4 - corner fields take the remainder
# I and O are skipped - they read as 1 and 0 (ISO 5457:1999 4.4)
ZONE_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"

TITLE_BLOCK_W = 180.0       # ISO 7200:2004 clause 6 - fills the A4 drawing space
TITLE_BLOCK_H = 36.0
CAPTION_H = 7.0             # strip above the view for its caption and scale

# ISO 128-20 line widths in mm; wide:narrow is 2:1
LW_FRAME, LW_WIDE, LW_NARROW, LW_FINE = 0.7, 0.5, 0.35, 0.25

# ISO 3098-1 5.3 lettering heights in mm - the sqrt(2) ladder
H_MICRO, H_SMALL, H_MID, H_BIG = 1.8, 2.5, 3.5, 5.0

# ISO 5455 recommended scales, largest first
ISO_SCALES = [(50, 1), (20, 1), (10, 1), (5, 1), (2, 1), (1, 1),
              (1, 2), (1, 5), (1, 10), (1, 20), (1, 50), (1, 100)]

PT_PER_MM = 72.0 / 25.4     # matplotlib measures in points, the sheet in millimetres
CAP_RATIO = 0.70            # cap height / em size of the default sans font


def mm_font(h_mm):
    """ISO 3098 lettering height (cap height, mm) -> matplotlib font size in pt."""
    return h_mm * PT_PER_MM / CAP_RATIO


def mm_lw(w_mm):
    """ISO 128-20 line width (mm) -> matplotlib linewidth in pt."""
    return w_mm * PT_PER_MM


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
        "refdes": "Ref. designation",
        "description": "Description",
        "qty": "Qty.",
        "note": "Note",
        "key": "Key",
        "dni_short": "DNI",
        "dni_page": "DO NOT INSTALL / DNI",
        "item": "Item",
        "part_no": "Part number",
        "tech_data": "Technical data",
        "package": "Package",
        "remarks": "Remarks",
        "tb_owner": "Legal owner",
        "tb_doctype": "Document type",
        "tb_creator": "Created by",
        "tb_approver": "Approved by",
        "tb_date": "Date of issue",
        "tb_docno": "Identification no.",
        "tb_rev": "Rev.",
        "tb_scale": "Scale",
        "tb_lang": "Lang.",
        "tb_sheet": "Sheet",
        "tb_title": "Title",
        "doctype_value": "Assembly drawing",
        "view_top": "TOP SIDE",
        "view_bottom": "BOTTOM SIDE (MIRRORED)",
    },
    "ru": {
        "cell": "Ячейка / Cell",
        "refdes": "Поз. обозначение / Ref. designation",
        "description": "Описание / Description",
        "qty": "Кол. / Qty.",
        "note": "Прим. / Note",
        "key": "Ключ / Key",
        "dni_short": "НЕ УСТАН.",
        "dni_page": "НЕ УСТАНАВЛИВАТЬ / DNI",
        "item": "Поз. / Item",
        "part_no": "Тип / Part number",
        "tech_data": "Техн. данные / Technical data",
        "package": "Корпус / Package",
        "remarks": "Примечание / Remarks",
        "tb_owner": "Владелец / Legal owner",
        "tb_doctype": "Тип документа / Document type",
        "tb_creator": "Разработал / Created by",
        "tb_approver": "Утвердил / Approved by",
        "tb_date": "Дата выпуска / Date of issue",
        "tb_docno": "Обозначение / Identification no.",
        "tb_rev": "Изм. / Rev.",
        "tb_scale": "Масштаб / Scale",
        "tb_lang": "Язык / Lang.",
        "tb_sheet": "Лист / Sheet",
        "tb_title": "Наименование / Title",
        "doctype_value": "Сборочный чертёж / Assembly drawing",
        "view_top": "ВИД СВЕРХУ / TOP SIDE",
        "view_bottom": "ВИД СНИЗУ, ЗЕРКАЛЬНО / BOTTOM SIDE (MIRRORED)",
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

# A Comment or Value consisting of nothing but one of these means the part is
# not fitted - a KiCad habit. The match has to be against the WHOLE field: a
# word-boundary search would also fire on "100 nF" and "4.7 nF", quietly
# marking every spaced nanofarad capacitor as do-not-install.
NOT_FITTED_EXACT = {"nf", "dnf"}


def is_not_fitted(*fields):
    return any(str(f or "").strip().lower() in NOT_FITTED_EXACT for f in fields)


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
    footprint: str = ""

    def __post_init__(self):
        self.prefix = extract_prefix(self.designator)
        self._estimate_size()

    def _estimate_size(self):
        # The footprint name is the most reliable size hint a BOM offers:
        # "USON-10_2.5x1.0mm_P0.5mm" beats guessing from a marketing blurb.
        desc = (self.description + " " + self.comment + " "
                + self.footprint).upper()
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
    footprint: str = ""
    part_no: str = ""
    tech: str = ""


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
                          alpha=0.7, mirror_x=0, scale=1.0):
    """Render board entities (from Gerber or DXF) on matplotlib axes.

    `scale` is paper millimetres per board millimetre: Gerber aperture widths
    are real board dimensions, so they have to be converted to points through
    the drawing scale rather than used as if they were already points."""
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
                lw = max(0.08, min(w * scale * PT_PER_MM, 1.2))
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
                lw = max(0.08, min(w * scale * PT_PER_MM, 1.2))
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
        # Altium calls it Footprint or Pattern, KiCad Footprint, some
        # in-house exports Package - all mean the same column.
        "footprint": col("footprint", "package", "pattern", contains="footprint"),
        "part_no": col("part number", "partnumber", "part no", "mpn",
                       "manufacturer part number"),
    }


# Trailing footprint tokens that describe how the pads are drawn rather than
# what the part is: pitch, pad/mask/exposed-pad geometry, mounting variants,
# and the metric code that merely repeats the imperial one (0402 = 1005Metric).
_FP_NOISE = re.compile(
    r"^(?:\d{3,4}metric"
    r"|p[\d.]+mm"
    r"|(?:pad|mask|ep)[\d.x]*mm"
    r"|handsolder|thermalvias|horizontal|vertical|nominal|castellated"
    r"|pin1\w*)$", re.I)


def short_package(fp):
    """Trim a footprint name down to the part an assembler needs to read.

    Library names carry the whole pad geometry - "SOIC-8-1EP_3.9x4.9mm_P1.27mm_
    EP2.95x4.9mm_Mask2.71x3.4mm_ThermalVias" - which is unreadable in a table
    cell. Trailing tokens that only describe the pads are dropped, which leaves
    the package and its body size. Names whose meaning sits in a later token,
    such as "Crystal_SMD_HC49-SD", survive intact.
    """
    fp = str(fp or "").strip().strip('"')
    parts = fp.split("_")
    while len(parts) > 1 and _FP_NOISE.match(parts[-1]):
        parts.pop()
    return "_".join(parts)


# Values that occupy a Part Number cell without naming a part. Altium writes
# "[NoParam], OPA2180IDGK" when a variant leaves the parameter unset.
_PN_PLACEHOLDER = re.compile(r"^(?:generic|\[?noparam\]?|n/?a|none|-+|\*)$", re.I)


def _clean_pn(part_no, comment):
    """Manufacturer part number, or the Comment when the BOM has none."""
    for token in str(part_no or "").split(","):
        token = token.strip()
        if token and not _PN_PLACEHOLDER.match(token):
            return token
    return comment or ""


def _bom_row_to_result(comment, desc, val, desig_str, layer_raw, footprint="",
                       part_no=""):
    desigs = [d.strip() for d in re.split(r"[,;]", desig_str) if d.strip()]
    if not desigs:
        return None
    layer = normalize_layer(layer_raw) if layer_raw else "Top"
    blob = f"{comment} {val} {desc}"
    is_dni = bool(DNI_PATTERNS.search(blob)) or is_not_fitted(comment, val)
    display = val if (val and not DNI_PATTERNS.search(val)) else (comment or "")
    full_desc = desc if desc and len(desc) < 60 else (display or comment)
    return {"comment": comment, "description": desc, "display": display,
            "full_desc": full_desc, "designators": desigs, "layer": layer,
            "dni": is_dni, "footprint": footprint,
            "part_no": _clean_pn(part_no, display or comment),
            # A Description that runs long is a datasheet blurb, not table data
            "tech": desc if desc and len(desc) < 60 else ""}


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
                               cell(c["val"]), desig_str, cell(c["layer"]),
                               cell(c["footprint"]), cell(c["part_no"]))
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
                               cell(c["val"]), desig_str, cell(c["layer"]),
                               cell(c["footprint"]), cell(c["part_no"]))
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
                comp.footprint = row["footprint"]
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
                note=STR["key"] if is_keyed else "", dni=row["dni"],
                footprint=short_package(row["footprint"]),
                part_no=row["part_no"], tech=row["tech"]))
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
            fps = {short_package(pnp[d].footprint) for d in desigs}
            fps.discard("")
            groups.append(ComponentGroup(
                comment=f"{prefix}", description=f"All {prefix} components",
                designators=desigs, quantity=len(desigs),
                note=STR["key"] if is_keyed else "",
                footprint=fps.pop() if len(fps) == 1 else "",
                part_no=prefix))
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
                   mirror_x=0, linewidth=LW_FINE, zorder=2, edge_color="#333",
                   hatch=None, scale=1.0, key_marker=True):
    """A component on the PDF view. When mirrored (Bottom view) the ENTIRE
    geometry is flipped: rotation angle (theta -> -theta) and the pin-1 marker
    offset (dx -> -dx), not just the centre - otherwise the key points to the
    wrong corner.

    `scale` is paper millimetres per board millimetre, so text and markers can
    be sized in real ISO 3098 lettering heights instead of guessed points.
    """
    x, y = comp.x, comp.y
    mirrored = mirror_x > 0
    if mirrored:
        x = mirror_x - x
    w, h = comp.w, comp.h
    lw = mm_lw(linewidth)
    if comp.shape == "circle":
        ax.add_patch(Circle((x, y), w/2, facecolor=color, edgecolor=edge_color,
                           linewidth=lw, alpha=alpha, zorder=zorder,
                           hatch=hatch))
    else:
        angle = -comp.rotation if mirrored else comp.rotation
        ax.add_patch(Rectangle((x-w/2, y-h/2), w, h, angle=angle,
                               rotation_point="center", facecolor=color,
                               edgecolor=edge_color, linewidth=lw,
                               alpha=alpha, zorder=zorder, hatch=hatch))
        if comp.shape == "ic" and key_marker:
            rad = math.radians(comp.rotation)
            dx = (-w/2+0.6)*math.cos(rad) - (-h/2+0.6)*math.sin(rad)
            dy = (-w/2+0.6)*math.sin(rad) + (-h/2+0.6)*math.cos(rad)
            if mirrored:
                dx = -dx
            ms = max(0.8, min(w, h) * scale * 0.35) * PT_PER_MM
            ax.plot(x+dx, y+dy, "o", color="white", markersize=ms,
                    zorder=zorder+1, markeredgecolor="#333", markeredgewidth=0.3)
    if label and should_label(comp):
        # Cap height on paper. Below ~1.1 mm a designator is unreadable, so it
        # is dropped rather than turned into a smudge.
        cap = min(w, h) * scale * 0.45
        if cap >= 1.1:
            ax.text(x, y, comp.designator, ha="center", va="center",
                    fontsize=mm_font(min(cap, H_MID)), fontweight="bold",
                    color="white", zorder=zorder+2,
                    path_effects=[pe.Stroke(linewidth=1.0, foreground="#333"),
                                  pe.Normal()])


# ═══════════════════════════════════════════════════════════════════════
#  ISO DRAWING SHEET — ISO 5457 frame, ISO 7200 title block,
#                      ISO 7573 parts list, ISO 5455 scales
# ═══════════════════════════════════════════════════════════════════════

def _txt_w(s, h_mm):
    """Rough width in mm of `s` at ISO lettering height h_mm.

    Measured advance per character runs 0.75 h for lower case up to 0.89 h for
    Cyrillic capitals, so the estimate is deliberately pessimistic. Anything
    that must not overflow is measured exactly by Sheet.ftext() instead.
    """
    return len(s) * h_mm * 0.88


def _wrap_mm(text, max_mm, h_mm, max_lines=2, sep=" "):
    """Wrap `text` onto at most `max_lines` lines of `max_mm` width."""
    budget = max(4, int(max_mm / (h_mm * 0.80)))
    toks = str(text).split(sep)
    lines, cur, i = [], "", 0
    while i < len(toks) and len(lines) < max_lines:
        cand = toks[i] if not cur else cur + sep + toks[i]
        if len(cand) <= budget or not cur:
            cur, i = cand, i + 1
        else:
            lines.append(cur)
            cur = ""
    if cur and len(lines) < max_lines:
        lines.append(cur)
        cur = ""
    if not lines:
        return [""]
    if i < len(toks) or cur:        # something was left over - mark it
        lines[-1] = lines[-1][:budget - 1].rstrip(" ,;") + "…"
    return [ln if len(ln) <= budget else ln[:budget - 1] + "…" for ln in lines]


def grid_edges(centre, lo, hi):
    """Grid reference field boundaries between `lo` and `hi`.

    ISO 5457:1999 4.4 - fields are 50 mm long and start at the axis of symmetry
    of the trimmed sheet (the centring marks); the leftover at each end is
    absorbed by the corner fields. Reproduces the field counts of table 2.
    """
    edges = [lo]
    k = math.ceil((lo - centre) / GRID_FIELD_LEN)
    v = centre + k * GRID_FIELD_LEN
    while v < hi - 1e-6:
        if v > lo + 1e-6:
            edges.append(v)
        v += GRID_FIELD_LEN
    edges.append(hi)
    return edges


def sheet_layout(fmt, landscape):
    """Millimetre geometry of one sheet: drawing space and the blocks in it.

    Computed without building a figure so the drawing scale can be decided once
    for the whole document - every sheet then shows the board at the same size.
    """
    short, long_ = SHEET_SIZES.get(fmt, SHEET_SIZES["A4"])
    w, h = (long_, short) if landscape else (short, long_)
    x0, y0 = BORDER_LEFT, BORDER_EDGE
    x1, y1 = w - BORDER_EDGE, h - BORDER_EDGE
    # Row height shrinks on small sheets so the board view keeps most of the page
    rowh = 7.0
    while (TITLE_BLOCK_H + rowh * (ROWS_PER_PAGE + 1) > 0.45 * (y1 - y0)
           and rowh > 5.0):
        rowh -= 0.25
    stack = TITLE_BLOCK_H + rowh * (ROWS_PER_PAGE + 1)
    board_y = y0 + stack + 4.0
    return {
        "w": w, "h": h, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
        "rowh": rowh,
        "title_block": (x1 - TITLE_BLOCK_W, y0),
        "parts_list": (x1 - TITLE_BLOCK_W, y0 + TITLE_BLOCK_H),
        # The board axes stops short of the frame: the strip on top carries the
        # view caption and the ISO 5455 scale designation.
        "board": (x0, board_y, x1 - x0, y1 - CAPTION_H - board_y),
        "caption": (x0, y1 - CAPTION_H / 2.0),
    }


def resolve_scale(board_w, board_h, area_w, area_h, mode="auto"):
    """-> (factor, label). `factor` is paper millimetres per board millimetre.

    "auto" picks the largest ISO 5455 scale that still fits the view area,
    "fit" fills the area exactly (an intermediate scale, which ISO 5455 allows
    when necessary), and "N:M" forces that scale.
    """
    if board_w > 0 and board_h > 0:
        fit = min(area_w / board_w, area_h / board_h)
    else:
        fit = 1.0
    if mode and ":" in str(mode):
        try:
            a, b = str(mode).split(":")
            if float(a) > 0 and float(b) > 0:
                return float(a) / float(b), "%s:%s" % (a.strip(), b.strip())
        except ValueError:
            pass
    if mode == "fit":
        def trim(v):
            return ("%.2f" % v).rstrip("0").rstrip(".")
        if fit >= 1.0:
            return fit, "%s:1" % trim(fit)
        return fit, "1:%s" % trim(1.0 / fit)
    for num, den in ISO_SCALES:
        if num / float(den) <= fit:
            return num / float(den), "%d:%d" % (num, den)
    num, den = ISO_SCALES[-1]
    return num / float(den), "%d:%d" % (num, den)


class Sheet:
    """One ISO 5457 drawing sheet.

    Everything is placed in millimetres on the trimmed sheet and the figure is
    exactly that size, so 1 mm here is 1 mm on paper when the PDF is printed at
    100 %. That is what makes the scale in the title block a real statement.
    """

    def __init__(self, fmt="A4", landscape=True):
        self.fmt = fmt if fmt in SHEET_SIZES else "A4"
        self.landscape = landscape
        self.lay = sheet_layout(self.fmt, landscape)
        self.w, self.h = self.lay["w"], self.lay["h"]
        self.x0, self.y0 = self.lay["x0"], self.lay["y0"]
        self.x1, self.y1 = self.lay["x1"], self.lay["y1"]
        self.fig = plt.figure(figsize=(self.w / 25.4, self.h / 25.4),
                              facecolor="white")
        # Overlay axes across the whole sheet, 1 data unit = 1 mm. The frame,
        # frame and tables are drawn here; the board view gets its own axes.
        self.ov = self.fig.add_axes([0, 0, 1, 1], zorder=10)
        self.ov.set_xlim(0, self.w)
        self.ov.set_ylim(0, self.h)
        self.ov.axis("off")
        self.ov.patch.set_visible(False)
        self._rend = None

    @property
    def designation(self):
        """Sheet size designation for the bottom border (ISO 5457:1999 3.1)."""
        return self.fmt

    # ── primitives, all in sheet millimetres ──────────────────────────
    def line(self, x1, y1, x2, y2, lw=LW_NARROW, color="#000"):
        self.ov.plot([x1, x2], [y1, y2], color=color, lw=mm_lw(lw),
                     solid_capstyle="butt")

    def rect(self, x, y, w, h, lw=LW_NARROW, edge="#000", fill="none", **kw):
        self.ov.add_patch(Rectangle((x, y), w, h, facecolor=fill,
                                    edgecolor=edge, lw=mm_lw(lw), **kw))

    def text(self, x, y, s, h=H_SMALL, ha="left", va="center", color="#000", **kw):
        return self.ov.text(x, y, s, fontsize=mm_font(h), ha=ha, va=va,
                            color=color, **kw)

    def measure(self, s, h_mm, weight="normal"):
        """Exact width in millimetres of `s` at lettering height h_mm."""
        t = self.ov.text(0, 0, s, fontsize=mm_font(h_mm), fontweight=weight)
        try:
            if self._rend is None:
                self._rend = self.fig.canvas.get_renderer()
            return t.get_window_extent(renderer=self._rend).width * 25.4 / self.fig.dpi
        finally:
            t.remove()

    def ftext(self, x, y, s, max_mm, h=H_SMALL, min_ratio=0.62, **kw):
        """Draw `s` inside a `max_mm` wide cell.

        Shrinks the lettering first - what a draughtsman does with a long name
        in a fixed title-block cell - and only truncates when even the smallest
        size will not fit. Widths are measured, not estimated, so bilingual
        headers cannot silently run over their cell borders.
        """
        s = str(s)
        if not s:
            return None
        weight = kw.get("fontweight", "normal")
        h_min = h * min_ratio
        w = self.measure(s, h, weight)
        # Shrink first. Hinting makes the rendered width only roughly linear in
        # the font size, so one scaling step can land just over the cell -
        # iterate instead of truncating text that would have fitted.
        for _ in range(6):
            if w <= max_mm or h <= h_min + 1e-9:
                break
            h = max(h_min, h * max_mm / w * 0.98)
            w = self.measure(s, h, weight)
        # Only now, at the smallest allowed size, cut the text.
        for _ in range(4):
            if w <= max_mm or len(s) <= 1:
                break
            keep = max(1, int(len(s) * max_mm / w) - 1)
            s = s[:keep].rstrip(" .,;/-") + "…"
            w = self.measure(s, h, weight)
        return self.text(x, y, s, h=h, **kw)

    def caption(self, x, y, s):
        """ISO 7200 style field caption: small, top-left inside its cell."""
        return self.text(x + 1.2, y - 1.4, s, h=H_MICRO, va="top", color="#555")

    def axes(self, x, y, w, h, **kw):
        """A matplotlib axes filling the given millimetre rectangle."""
        return self.fig.add_axes([x / self.w, y / self.h,
                                  w / self.w, h / self.h], **kw)


def draw_frame(sheet):
    """ISO 5457: frame, grid reference border, centring marks, trimming marks."""
    s = sheet
    z = ZONE_STRIP
    # 4.2 - frame limiting the drawing space
    s.rect(s.x0, s.y0, s.x1 - s.x0, s.y1 - s.y0, lw=LW_FRAME)
    # 4.4 - grid reference border
    s.rect(s.x0 - z, s.y0 - z, (s.x1 - s.x0) + 2 * z, (s.y1 - s.y0) + 2 * z,
           lw=LW_NARROW)
    ex = grid_edges(s.w / 2.0, s.x0, s.x1)
    ey = grid_edges(s.h / 2.0, s.y0, s.y1)
    both = s.fmt != "A4"        # A4 is referenced only at the top and the right
    for v in ex[1:-1]:
        s.line(v, s.y1, v, s.y1 + z)
        if both:
            s.line(v, s.y0, v, s.y0 - z)
    for v in ey[1:-1]:
        s.line(s.x1, v, s.x1 + z, v)
        if both:
            s.line(s.x0, v, s.x0 - z, v)
    for i in range(len(ex) - 1):                     # numerals, left to right
        cx = (ex[i] + ex[i + 1]) / 2.0
        s.text(cx, s.y1 + z / 2.0, str(i + 1), h=H_MID, ha="center")
        if both:
            s.text(cx, s.y0 - z / 2.0, str(i + 1), h=H_MID, ha="center")
    for i in range(len(ey) - 1):                     # capitals, top downwards
        ch = ZONE_LETTERS[i % len(ZONE_LETTERS)]
        cy = (ey[len(ey) - 2 - i] + ey[len(ey) - 1 - i]) / 2.0
        s.text(s.x1 + z / 2.0, cy, ch, h=H_MID, ha="center")
        if both:
            s.text(s.x0 - z / 2.0, cy, ch, h=H_MID, ha="center")
    # 4.3 - centring marks on the axes of symmetry of the trimmed sheet
    mx, my = s.w / 2.0, s.h / 2.0
    s.line(mx, s.y1 + z, mx, s.y1 - 10, lw=LW_FRAME)
    s.line(mx, s.y0 - z, mx, s.y0 + 10, lw=LW_FRAME)
    s.line(s.x0 - z, my, s.x0 + 10, my, lw=LW_FRAME)
    s.line(s.x1 + z, my, s.x1 - 10, my, lw=LW_FRAME)
    # 4.5 - trimming marks: two overlapping 10 x 5 rectangles at every corner
    for cx, cy, sx, sy in ((0, 0, 1, 1), (s.w, 0, -1, 1),
                           (0, s.h, 1, -1), (s.w, s.h, -1, -1)):
        s.rect(min(cx, cx + sx * 10), min(cy, cy + sy * 5),
               10, 5, lw=0.0, fill="#000", edge="none")
        s.rect(min(cx, cx + sx * 5), min(cy, cy + sy * 10),
               5, 10, lw=0.0, fill="#000", edge="none")
    # 3.1 - the size designation goes in the bottom border, at the right corner
    s.text(s.x1 - 1.0, BORDER_EDGE / 4.0, s.designation, h=H_SMALL, ha="right")


def draw_title_block(sheet, meta, page, total, sub_title, scale_txt):
    """ISO 7200:2004 title block, 180 mm wide, bottom right of the drawing space.

    Mandatory fields (tables 1-3): legal owner, identification number, date of
    issue, sheet number, title, creator, approval person, document type.
    Revision index, number of sheets, language code and scale are optional.
    """
    s = sheet
    x, y = s.lay["title_block"]
    W, H, L = TITLE_BLOCK_W, TITLE_BLOCK_H, 110.0
    s.rect(x, y, W, H, lw=LW_WIDE, fill="white", zorder=2)
    for yy in (y + 27, y + 18):
        s.line(x, yy, x + W, yy)
    s.line(x + L, y + 9, x + W, y + 9)
    s.line(x + L, y, x + L, y + H)
    s.line(x + 55, y + 18, x + 55, y + 27)
    s.line(x + 158, y + 9, x + 158, y + 18)
    s.line(x + 134, y, x + 134, y + 9)
    s.line(x + 152, y, x + 152, y + 9)

    def cell(cx, cy, cw, ch, cap, val, vh=H_MID, bold=False):
        s.ftext(cx + 1.2, cy + ch - 1.4, cap, cw - 2.4, h=H_MICRO, va="top",
                color="#555")
        s.ftext(cx + 1.4, cy + ch * 0.30, val, cw - 2.8, h=vh,
                fontweight="bold" if bold else "normal")

    cell(x, y + 27, L, 9, STR["tb_owner"], meta.get("owner", ""), bold=True)
    cell(x, y + 18, 55, 9, STR["tb_creator"], meta.get("designer", ""))
    cell(x + 55, y + 18, 55, 9, STR["tb_approver"], meta.get("approver", ""))
    cell(x + L, y + 27, W - L, 9, STR["tb_doctype"], STR["doctype_value"])
    cell(x + L, y + 18, W - L, 9, STR["tb_date"], meta.get("date", ""))
    cell(x + L, y + 9, 48, 9, STR["tb_docno"], meta.get("doc_no", ""))
    cell(x + 158, y + 9, W - 158, 9, STR["tb_rev"], meta.get("edition", ""))
    cell(x + L, y, 24, 9, STR["tb_scale"], scale_txt, bold=True)
    cell(x + 134, y, 18, 9, STR["tb_lang"], LANG.upper())
    cell(x + 152, y, W - 152, 9, STR["tb_sheet"], "%d / %d" % (page, total),
         bold=True)
    # Title and supplementary title share the tall left cell
    s.caption(x, y + 18, STR["tb_title"])
    s.ftext(x + 1.4, y + 9.6, meta.get("project", ""), L - 3.0, h=H_BIG,
            fontweight="bold")
    s.ftext(x + 1.4, y + 4.2, sub_title, L - 3.0, h=H_SMALL, color="#333")


def draw_parts_list(sheet, gwc, rowh):
    """ISO 7573:2008 parts list.

    Columns follow 5.2.1: part reference, quantity, reference designation,
    part number, technical data, package and remarks.

    Deviation from 5.1: the header sits at the top and the items read downward.
    The standard puts the header against the title block - which would mean
    reading upward - but every reader of this drawing expects a table to start
    at its heading, and 5.1 allows a top header wherever the list is not in
    conjunction with the title block.
    """
    s = sheet
    x, y = s.lay["parts_list"]
    W = TITLE_BLOCK_W
    cols = [0.0, 12.0, 21.0, 67.0, 99.0, 135.0, 168.0, W]   # column boundaries
    # The grid is always full height. Pages with fewer groups leave blank rows,
    # the way a preprinted list does, so every sheet has the same geometry.
    total_h = rowh * (ROWS_PER_PAGE + 1)
    head_y = y + rowh * ROWS_PER_PAGE            # bottom of the header row
    s.rect(x, y, W, total_h, lw=LW_WIDE, fill="white", zorder=2)
    s.line(x, head_y, x + W, head_y, lw=LW_WIDE)
    for c in cols[1:-1]:
        s.line(x + c, y, x + c, y + total_h)
    for i in range(1, ROWS_PER_PAGE):
        s.line(x, y + rowh * i, x + W, y + rowh * i)

    heads = [STR["item"], STR["qty"], STR["refdes"], STR["part_no"],
             STR["tech_data"], STR["package"], STR["remarks"]]
    for j, head in enumerate(heads):
        s.ftext(x + cols[j] + 1.4, head_y + rowh / 2.0, head,
                cols[j + 1] - cols[j] - 2.8, h=H_MICRO, fontweight="bold")

    for i, (group, color, num) in enumerate(gwc):
        ry = head_y - rowh * (i + 1)             # bottom of this item row
        mid = ry + rowh / 2.0
        # Part reference (ISO 6433): the colour key doubles as the item cell,
        # the number is encircled so it survives greyscale printing.
        s.rect(x + 1.0, ry + 0.8, cols[1] - 2.0, rowh - 1.6, lw=LW_FINE,
               fill=color, edge="#333", zorder=3,
               hatch="///" if group.dni else None)
        if num is not None:
            r = min(2.4, rowh / 2.0 - 1.2)
            s.ov.add_patch(Circle((x + cols[1] / 2.0, mid), r, facecolor="white",
                                  edgecolor="#000", lw=mm_lw(LW_FINE), zorder=4))
            s.text(x + cols[1] / 2.0, mid, str(num), h=H_MICRO, ha="center",
                   fontweight="bold", zorder=5)
        s.text(x + (cols[1] + cols[2]) / 2.0, mid, str(group.quantity),
               h=H_SMALL, ha="center")
        w_ref = cols[3] - cols[2] - 2.8
        refs = _wrap_mm(", ".join(group.designators), w_ref, H_MICRO,
                        max_lines=2 if rowh >= 6.0 else 1, sep=", ")
        lead = (len(refs) - 1) / 2.0 * H_MICRO * 1.5
        for k, ln in enumerate(refs):
            s.ftext(x + cols[2] + 1.4, mid + lead - k * H_MICRO * 1.5, ln,
                    w_ref, h=H_MICRO)
        s.ftext(x + cols[3] + 1.4, mid, group.part_no or group.comment,
                cols[4] - cols[3] - 2.8, h=H_SMALL)
        s.ftext(x + cols[4] + 1.4, mid, group.tech,
                cols[5] - cols[4] - 2.8, h=H_SMALL)
        s.ftext(x + cols[5] + 1.4, mid, group.footprint,
                cols[6] - cols[5] - 2.8, h=H_SMALL)
        note = STR["dni_short"] if group.dni else group.note
        s.ftext(x + cols[6] + 1.4, mid, note, W - cols[6] - 2.8, h=H_MICRO,
                color="#b00020" if group.dni else "#333")


def draw_board_view(sheet, pnp, gwc, layer, bounds, scale, scale_txt,
                    board_layers=None):
    """The board itself, drawn at exactly `scale` paper-mm per board-mm."""
    bx, by, bw, bh = sheet.lay["board"]
    x_min, x_max, y_min, y_max = bounds
    mirror_x = (x_min + x_max) if layer == "Bottom" else 0
    ax = sheet.axes(bx, by, bw, bh, zorder=1)
    # Mirroring reflects about the centre of the bounds, so the centre - and
    # therefore these limits - are the same on both sides of the board.
    cx, cy = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
    ax.set_xlim(cx - bw / scale / 2.0, cx + bw / scale / 2.0)
    ax.set_ylim(cy - bh / scale / 2.0, cy + bh / scale / 2.0)
    # No set_aspect: the axes box and the data range already have the same
    # ratio by construction, and letting matplotlib re-fit the box would
    # silently change the drawing scale.
    ax.set_facecolor("white")
    ax.set_xticks([]); ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_visible(False)

    has_art = board_layers is not None
    has_outline = bool(has_art and board_layers.get("outline"))

    if not has_outline:
        # No real outline in the Gerber/DXF - show the extents instead
        ax.add_patch(Rectangle((x_min, y_min), x_max-x_min, y_max-y_min,
                               facecolor="#f8f8f8", edgecolor="#333",
                               lw=mm_lw(LW_NARROW), zorder=0))
    else:
        render_board_entities(ax, board_layers["outline"], color="#333333",
                              linewidth=mm_lw(LW_NARROW), alpha=0.9,
                              mirror_x=mirror_x, scale=scale)
    if has_art:
        overlay_key = "bottom" if layer == "Bottom" else "top"
        if board_layers.get(overlay_key):
            render_board_entities(ax, board_layers[overlay_key], color="#888888",
                                  linewidth=mm_lw(LW_FINE), alpha=0.5,
                                  mirror_x=mirror_x, scale=scale)

    # Background components (grey). Skipped when the artwork already shows
    # them, except on DNI pages - those would otherwise look empty.
    is_dni_page = bool(gwc) and all(g.dni for g, _, _ in gwc)
    highlighted = set()
    for g, _, _ in gwc:
        highlighted.update(g.designators)
    if not has_art or is_dni_page:
        for desig, comp in pnp.items():
            if comp.layer != layer or desig in highlighted:
                continue
            gray = "#e0e0e0" if comp.prefix in ("TP", "S") else "#cccccc"
            a = 0.4 if comp.prefix in ("TP", "S") else 0.6
            draw_component(ax, comp, gray, alpha=a, mirror_x=mirror_x,
                           linewidth=LW_FINE * 0.6, zorder=1,
                           edge_color="#999", scale=scale, key_marker=False)

    for group, color_hex, _num in gwc:
        for desig in group.designators:
            comp = pnp.get(desig)
            if comp is None or comp.layer != layer:
                continue    # a part from the other side is not drawn on this page
            draw_component(ax, comp, color_hex,
                           alpha=0.65 if has_art else 0.92, label=True,
                           mirror_x=mirror_x, zorder=4, edge_color="#333",
                           linewidth=LW_NARROW if group.dni else LW_FINE,
                           scale=scale, hatch="///" if group.dni else None)

    # View caption and scale designation (ISO 5455: the word SCALE + the ratio)
    cx_, cy_ = sheet.lay["caption"]
    view = STR["view_bottom"] if layer == "Bottom" else STR["view_top"]
    scale_note = "%s %s" % (STR["tb_scale"].split(" / ")[0].upper(), scale_txt)
    sheet.text(sheet.x1 - 1.0, cy_, scale_note, h=H_MID, ha="right",
               fontweight="bold")
    room = (sheet.x1 - 1.0) - sheet.measure(scale_note, H_MID, "bold") - cx_ - 4.0
    sheet.ftext(cx_ + 1.0, cy_, view, room, h=H_MID, fontweight="bold")


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
                 project_name="Project", designed_by="", edition="A",
                 progress_callback=None, board_layers=None,
                 sheet_format="A4", landscape=True, scale_mode="auto",
                 meta=None):
    bounds = compute_bounds(pnp, board_layers, margin=2.0)
    lay = sheet_layout(sheet_format, landscape)
    _bx, _by, area_w, area_h = lay["board"]
    scale, scale_txt = resolve_scale(bounds[1] - bounds[0], bounds[3] - bounds[2],
                                     area_w, area_h, scale_mode)
    info = dict(meta or {})
    info.setdefault("project", project_name)
    info.setdefault("designer", designed_by)
    info.setdefault("edition", edition)
    info.setdefault("date", datetime.date.today().isoformat())

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
        base = len(install)
        for i in range(0, len(dni_groups), ROWS_PER_PAGE):
            chunk = dni_groups[i:i+ROWS_PER_PAGE]
            gwc = [(g, DNI_COLOR, base + i + j + 1) for j, g in enumerate(chunk)]
            plan.append((layer, STR["dni_page"], gwc))
    total = len(plan)
    if total == 0: return 0

    with PdfPages(output_path) as pdf:
        for pn, (layer, mt, gwc) in enumerate(plan, 1):
            sheet = Sheet(sheet_format, landscape)
            draw_frame(sheet)
            draw_board_view(sheet, pnp, gwc, layer, bounds, scale, scale_txt,
                            board_layers=board_layers)
            draw_parts_list(sheet, gwc, lay["rowh"])
            view = STR["view_bottom"] if layer == "Bottom" else STR["view_top"]
            draw_title_block(sheet, info, pn, total,
                             "%s · %s" % (view, mt), scale_txt)
            pdf.savefig(sheet.fig)
            plt.close(sheet.fig)
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
    FIELD_W = 264         # export dialog: value column, fixed so it cannot grow
    HINT_W = 400          # export dialog: hint wrap width, keeps it two lines
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
        self.meta = {"project": "Assembly", "designer": "", "edition": "A",
                     "owner": "", "doc_no": "", "approver": "",
                     "sheet_format": "A4", "orientation": "Landscape",
                     "scale_mode": "auto",
                     "date": datetime.date.today().isoformat()}
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
        """Drawing setup: sheet, scale and the ISO 7200 title block fields.

        Returns the metadata dict, or None when the dialog was cancelled.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Drawing setup — ISO 5457 / ISO 7200")
        dlg.configure(bg=self.C_PANEL)
        dlg.transient(self.root); dlg.grab_set()
        dlg.resizable(False, False)
        dlg.columnconfigure(1, minsize=self.FIELD_W)

        def head(text, row):
            tk.Label(dlg, text=text, bg=self.C_PANEL, fg=self.C_FAINT,
                     font=("Segoe UI", 8, "bold")).grid(
                row=row, column=0, columnspan=2, sticky="w",
                padx=14, pady=(14, 2))

        def entry(label, key, default, row, width=34):
            tk.Label(dlg, text=label, bg=self.C_PANEL, fg=self.C_MUTED,
                     font=self.F_SMALL).grid(row=row, column=0, sticky="w",
                                             padx=(14, 8), pady=4)
            e = tk.Entry(dlg, width=width, bg=self.C_CARD, fg=self.C_TEXT,
                         insertbackground="white", relief="flat",
                         font=self.F_SMALL)
            e.insert(0, self.meta.get(key, default))
            e.grid(row=row, column=1, sticky="w", padx=(0, 14), pady=4,
                   ipady=3)
            return e

        def combo(label, key, values, default, row):
            tk.Label(dlg, text=label, bg=self.C_PANEL, fg=self.C_MUTED,
                     font=self.F_SMALL).grid(row=row, column=0, sticky="w",
                                             padx=(14, 8), pady=4)
            var = tk.StringVar(value=self.meta.get(key, default))
            box = ttk.Combobox(dlg, textvariable=var, values=values,
                               state="readonly", width=31, font=self.F_SMALL)
            box.grid(row=row, column=1, sticky="w", padx=(0, 14), pady=4)
            return var

        head("SHEET — ISO 5457", 0)
        v_fmt = combo("Format", "sheet_format",
                      list(SHEET_SIZES.keys()), "A4", 1)
        v_orient = combo("Orientation", "orientation",
                         ["Landscape", "Portrait"], "Landscape", 2)
        v_scale = combo("Scale", "scale_mode",
                        ["auto", "fit"] + ["%d:%d" % s for s in ISO_SCALES],
                        "auto", 3)

        head("TITLE BLOCK — ISO 7200", 4)
        e_title = entry("Title", "project", "Assembly", 5)
        e_owner = entry("Legal owner", "owner", "", 6)
        e_docno = entry("Identification no.", "doc_no", "", 7)
        e_rev = entry("Revision index", "edition", "A", 8)
        e_date = entry("Date of issue", "date",
                       datetime.date.today().isoformat(), 9)
        e_creator = entry("Created by", "designer", "", 10)
        e_appr = entry("Approved by", "approver", "", 11)

        # Fixed width and two reserved lines: the hint text changes length with
        # every selection, and without this the window would resize under the
        # pointer as the user picks values.
        hint = tk.Label(dlg, bg=self.C_PANEL, fg=self.C_FAINT,
                        font=("Segoe UI", 8), justify="left", anchor="nw",
                        wraplength=self.HINT_W, width=1, height=2)
        hint.grid(row=12, column=0, columnspan=2, sticky="we", padx=14,
                  pady=(12, 0))

        def refresh_hint(*_):
            fmt = v_fmt.get()
            land = v_orient.get() == "Landscape"
            lay = sheet_layout(fmt, land)
            bounds = compute_bounds(self.pnp, self.board_layers, margin=2.0)
            _x, _y, aw, ah = lay["board"]
            _f, txt = resolve_scale(bounds[1] - bounds[0], bounds[3] - bounds[2],
                                    aw, ah, v_scale.get())
            note = ""
            if fmt == "A4" and land:
                note = "\nISO 5457 places A4 upright; landscape is a deviation."
            hint.configure(text="Sheet %.0f × %.0f mm · view %.0f × %.0f mm · "
                                "scale %s%s" % (lay["w"], lay["h"], aw, ah,
                                                txt, note))

        for var in (v_fmt, v_orient, v_scale):
            var.trace_add("write", refresh_hint)
        refresh_hint()

        out = {}

        def ok():
            out.update({
                "sheet_format": v_fmt.get(),
                "orientation": v_orient.get(),
                "scale_mode": v_scale.get(),
                "project": e_title.get().strip() or "Assembly",
                "owner": e_owner.get().strip(),
                "doc_no": e_docno.get().strip(),
                "edition": e_rev.get().strip() or "A",
                "date": e_date.get().strip(),
                "designer": e_creator.get().strip(),
                "approver": e_appr.get().strip(),
            })
            dlg.destroy()

        bar = tk.Frame(dlg, bg=self.C_PANEL)
        bar.grid(row=13, column=0, columnspan=2, sticky="e", padx=14,
                 pady=(8, 14))
        tk.Button(bar, text="Cancel", command=dlg.destroy, bg=self.C_CARD,
                  fg=self.C_MUTED, relief="flat", padx=14,
                  font=self.F_SMALL).pack(side="left", padx=(0, 8))
        tk.Button(bar, text="Export", command=ok, bg=self.C_GREEN, fg="white",
                  relief="flat", padx=18, font=self.F_BTN).pack(side="left")
        e_title.focus_set()
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        dlg.update_idletasks()
        dlg.geometry("%dx%d" % (dlg.winfo_reqwidth(), dlg.winfo_reqheight()))
        dlg.grid_propagate(False)
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
                sheet_format=meta.get("sheet_format", "A4"),
                landscape=meta.get("orientation", "Landscape") == "Landscape",
                scale_mode=meta.get("scale_mode", "auto"),
                board_layers=self.board_layers,
                meta=meta,
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
