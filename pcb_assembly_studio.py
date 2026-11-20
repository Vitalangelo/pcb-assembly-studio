#!/usr/bin/env python3
"""
PCB Assembly Studio
───────────────────────────────────────────────
Assembly drawing generator for Altium Designer / KiCad projects.
Standalone GUI - packaged to .exe via PyInstaller.
"""

import copy
import csv
import datetime
import io
import json
import math
import os
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Dict, List
from assembly_geometry import load_gerber_evidence, load_drill_evidence, infer_geometry

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

APP_VERSION = "1.4.1"

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


def shade(hex_color, factor=0.45):
    """Darken a #rrggbb colour, keeping its hue."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r*factor), int(g*factor), int(b*factor))


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
    part_no: str = ""
    size_source: str = "unknown"
    sort_size: tuple = None
    sort_source: str = ""
    geometry_stage: str = ""
    mount_source: str = ""
    geometry_review: str = ""

    def __post_init__(self):
        self.prefix = extract_prefix(self.designator)
        self._estimate_size()

    def _estimate_size(self):
        self.size_source = "unknown"
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
            "01005": (0.4, 0.2), "0201": (0.6, 0.3), "0402": (1.0, 0.5), "0603": (1.6, 0.8),
            "0805": (2.0, 1.25), "1206": (3.2, 1.6), "1210": (3.2, 2.5),
            "2512": (6.3, 3.2), "1812": (4.5, 3.2), "2010": (5.0, 2.5),
        }
        for source in (self.footprint.upper(), self.description.upper(), self.comment.upper()):
            dimensions = re.search(r'(\d+(?:\.\d+)?)\s*[xX×]\s*(\d+(?:\.\d+)?)\s*mm', source, re.I)
            if dimensions:
                self.w, self.h = map(float, dimensions.groups())
                self.size_source = "dimensions"
                if prefix == "U":
                    self.shape = "ic"
                return
            # Manufacturer inductor series codes (e.g. WE-PD 1210) are not
            # the imperial chip size 1210.
            chip = prefix in ("R", "RT", "C", "FB", "D", "VD", "LED") or bool(
                re.match(r'^(?:L|IND)[_ -]?\d{4}(?:\D|$)', self.footprint.upper()))
            for code, (w, h) in pkg.items() if chip else ():
                if re.search(r'(?<!\d)' + code + r'(?!\d)', source):
                    self.w, self.h = w, h
                    self.size_source = "package"
                    return
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
                self.w, self.h, self.shape = w, h, "ic"
                self.size_source = "package estimate"
                return
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
    part_no: str = ""
    tech: str = ""
    stage: str = "SMT"


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


def project_name(filepath):
    """Board name from a Pick & Place file name, for the drawing title.

    Altium names the file "Pick Place for <board>", KiCad "<board>-top.pos" or
    "<board>-all-pos.csv"; only <board> belongs in a title block.
    """
    stem = os.path.splitext(os.path.basename(filepath))[0]
    name = re.sub(r"^pick[\s_-]*(?:(?:and|&)[\s_-]*)?place(?:[\s_-]+for)?[\s_-]+",
                  "", stem, flags=re.I)
    name = re.sub(r"-(?:top|bottom|both|all)(?:-pos)?$", "", name, flags=re.I)
    return name.strip() or stem


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
                comment=cell("comment"), description=cell("description"),
                footprint=cell("footprint"))
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
                description=get_field(line, "description"),
                footprint=get_field(line, "footprint"))
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
                comp.footprint = row["footprint"] or comp.footprint
                comp.part_no = row["part_no"]
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
            groups.append(ComponentGroup(
                comment=f"{prefix}", description=f"All {prefix} components",
                designators=desigs, quantity=len(desigs),
                note=STR["key"] if is_keyed else "",
                part_no=prefix))
        result[layer] = groups
    return result


# ═══════════════════════════════════════════════════════════════════════
#  CUSTOM PAGES — a drawing of only the designators typed in
# ═══════════════════════════════════════════════════════════════════════

_REF_NUMBERED = re.compile(r"^([A-Z]+)(\d+)$")
_REF_RANGE = re.compile(r"^([A-Za-z]+)(\d+)-([A-Za-z]*)(\d+)$")


def parse_refs(text, known):
    """Designators typed for a custom page -> (found, not_found).

    Commas, semicolons, spaces and new lines all separate. "R7-R12" and
    "R7-12" are ranges, and pick whichever members exist - gaps in numbering
    are normal. A single designator that does not exist is reported back, so
    is a range with no members at all. Case is ignored.
    """
    by_upper = {d.upper(): d for d in known}
    numbered = defaultdict(list)            # "R" -> [(7, "R7"), ...]
    for up, d in by_upper.items():
        m = _REF_NUMBERED.match(up)
        if m:
            numbered[m.group(1)].append((int(m.group(2)), d))
    text = re.sub(r"\s*[-–]\s*", "-", str(text or ""))   # "R7 – R12"
    found, missing, seen = [], [], set()
    for tok in re.split(r"[,;\s]+", text):
        if not tok:
            continue
        hits = []
        if tok.upper() in by_upper:         # before ranges: "U1-1" may be a name
            hits = [by_upper[tok.upper()]]
        else:
            m = _REF_RANGE.match(tok)
            if m and m.group(3).upper() in ("", m.group(1).upper()):
                lo, hi = sorted((int(m.group(2)), int(m.group(4))))
                hits = [d for n, d in sorted(numbered[m.group(1).upper()])
                        if lo <= n <= hi]
        if not hits and tok not in missing:
            missing.append(tok)
        for d in hits:
            if d not in seen:
                seen.add(d)
                found.append(d)
    return found, missing


def page_groups(groups_by_layer, designators):
    """The drawing's groups cut down to `designators`.

    Grouping, order and colours follow the full drawing's rules, so a part
    looks the same on a custom page as anywhere else - only fewer of them.
    """
    wanted = set(designators)
    result = {}
    for layer, groups in groups_by_layer.items():
        kept = []
        for g in groups:
            ds = [d for d in g.designators if d in wanted]
            if ds:
                kept.append(replace(g, designators=ds, quantity=len(ds)))
        if kept:
            result[layer] = kept
    return result


# ═══════════════════════════════════════════════════════════════════════
#  PROJECT FILES — loaded files, settings and custom pages as JSON
# ═══════════════════════════════════════════════════════════════════════

PROJECT_EXT = ".pcbasm"
PROJECT_FORMAT = 4
# Title block and sheet fields a project remembers. The date of issue is left
# out on purpose: it belongs to an issue of the drawing, not to the project.
PROJECT_META_KEYS = ("project", "owner", "doc_no", "edition", "designer",
                     "approver", "sheet_format", "orientation", "scale_mode")


def file_ref(path, project_dir):
    """How a project stores a loaded file: a path relative to the project
    file, so the folder can be moved or copied as a whole, and the absolute
    path as a fallback for when only the project file was moved."""
    ref = {"abs": os.path.abspath(path)}
    try:
        ref["rel"] = os.path.relpath(path, project_dir).replace(os.sep, "/")
    except ValueError:      # another drive on Windows - there is no relative path
        pass
    return ref


def resolve_file_ref(ref, project_dir):
    """Path of a stored file as it exists now, or None when it is gone."""
    candidates = []
    if ref.get("rel"):
        candidates.append(os.path.join(project_dir, ref["rel"]))
    if ref.get("abs"):
        candidates.append(ref["abs"])
    for c in candidates:
        if os.path.isfile(c):
            return os.path.normpath(c)
    return None


def write_project(path, data):
    """Write through a temporary file, so a failed save cannot leave a
    half-written project in place of the good one."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def read_project(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "files" not in data:
        raise ValueError("Not a PCB Assembly Studio project:\n" + path)
    if data.get("format", 1) > PROJECT_FORMAT:
        raise ValueError("This project was saved by a newer version of "
                         "PCB Assembly Studio. Update the app to open it.")
    return data


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
    # A fixed ISO line width swamps a small part: a 0.25 mm edge around a 0402
    # drawn 0.4 mm tall leaves hardly any colour showing, and every group
    # prints as the same dark grey. The edge stays a fraction of the short side.
    lw = mm_lw(min(linewidth, max(0.05, min(w, h) * scale * 0.15)))
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

    Columns: part reference, reference designation, part number, technical
    data, quantity and remarks - the 5.2.1 set without a package column, and
    with the quantity moved from after the part reference to before remarks.

    Deviation from 5.1: the header sits at the top and the items read downward.
    The standard puts the header against the title block - which would mean
    reading upward - but every reader of this drawing expects a table to start
    at its heading, and 5.1 allows a top header wherever the list is not in
    conjunction with the title block.
    """
    s = sheet
    x, y = s.lay["parts_list"]
    W = TITLE_BLOCK_W
    cols = [0.0, 12.0, 70.0, 108.0, 154.0, 166.0, W]   # column boundaries
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

    heads = [STR["item"], STR["refdes"], STR["part_no"], STR["tech_data"],
             STR["qty"], STR["remarks"]]
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
        w_ref = cols[2] - cols[1] - 2.8
        refs = _wrap_mm(", ".join(group.designators), w_ref, H_MICRO,
                        max_lines=2 if rowh >= 6.0 else 1, sep=", ")
        lead = (len(refs) - 1) / 2.0 * H_MICRO * 1.5
        for k, ln in enumerate(refs):
            s.ftext(x + cols[1] + 1.4, mid + lead - k * H_MICRO * 1.5, ln,
                    w_ref, h=H_MICRO)
        s.ftext(x + cols[2] + 1.4, mid, group.part_no or group.comment,
                cols[3] - cols[2] - 2.8, h=H_SMALL)
        s.ftext(x + cols[3] + 1.4, mid, group.tech,
                cols[4] - cols[3] - 2.8, h=H_SMALL)
        s.text(x + (cols[4] + cols[5]) / 2.0, mid, str(group.quantity),
               h=H_SMALL, ha="center")
        note = STR["dni_short"] if group.dni else group.note
        s.ftext(x + cols[5] + 1.4, mid, note, W - cols[5] - 2.8, h=H_MICRO,
                color="#b00020" if group.dni else "#333")


def draw_board_view(sheet, pnp, gwc, layer, bounds, scale, scale_txt,
                    board_layers=None, page_name=""):
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
        # Solid fill, edged in a darker shade of the same colour: artwork
        # showing through a translucent part muddies neighbouring colours
        # into each other, and a grey edge on a tiny part reads as grey.
        edge = "#333" if group.dni else shade(color_hex, 0.55)
        for desig in group.designators:
            comp = pnp.get(desig)
            if comp is None or comp.layer != layer:
                continue    # a part from the other side is not drawn on this page
            draw_component(ax, comp, color_hex, label=True,
                           mirror_x=mirror_x, zorder=4, edge_color=edge,
                           linewidth=LW_NARROW if group.dni else LW_FINE,
                           scale=scale, hatch="///" if group.dni else None)

    # View caption and scale designation (ISO 5455: the word SCALE + the ratio)
    cx_, cy_ = sheet.lay["caption"]
    view = STR["view_bottom"] if layer == "Bottom" else STR["view_top"]
    if page_name:
        view = "%s · %s" % (page_name, view)
    scale_note = "%s %s" % (STR["tb_scale"].split(" / ")[0].upper(), scale_txt)
    sheet.text(sheet.x1 - 1.0, cy_, scale_note, h=H_MID, ha="right",
               fontweight="bold")
    room = (sheet.x1 - 1.0) - sheet.measure(scale_note, H_MID, "bold") - cx_ - 4.0
    sheet.ftext(cx_ + 1.0, cy_, view, room, h=H_MID, fontweight="bold")


# ═══════════════════════════════════════════════════════════════════════
#  PDF GENERATION
# ═══════════════════════════════════════════════════════════════════════

def valid_size_overrides(data):
    result = {}
    if not isinstance(data, dict):
        return result
    for d, size in data.items():
        try:
            w, h = size
            w, h = float(w), float(h)
            if math.isfinite(w) and math.isfinite(h) and w > 0 and h > 0:
                result[str(d)] = [w, h]
        except (TypeError, ValueError):
            continue
    return result


def apply_size_overrides(pnp, sizes):
    for d, (w, h) in valid_size_overrides(sizes).items():
        if d in pnp:
            pnp[d].w, pnp[d].h, pnp[d].size_source = w, h, "manual"
            pnp[d].sort_size, pnp[d].sort_source = (w, h), "manual"


def component_stage(comp, group):
    """Explicit technology wins; connector names alone do not imply THT."""
    def result(stage, source):
        if comp:
            comp.mount_source = source
            if comp.geometry_stage and comp.geometry_stage != stage:
                comp.mount_source += " / geometry conflict — review"
        return stage
    for text in (group.tech, comp.footprint if comp else "",
                 group.description + " " + group.comment):
        text = text.upper().replace("_", " ")
        if re.search(r"\b(SMD|SMT|SURFACE[ -]MOUNT)\b", text):
            return result("SMT", "file text")
        if re.search(r"\b(THT|TH|PTH|THROUGH[ -]HOLE|DIP\d*|PDIP\d*|SIP\d*)\b|TO[-_ ]?220", text):
            return result("THT", "file text")
        if re.search(r"\bDIP\d+(?:SW)?\b", text):
            return result("THT", "package hint — review")
    if comp and comp.geometry_stage:
        return result(comp.geometry_stage, "PTH + copper — inferred")
    text = " ".join((comp.footprint if comp else "", group.description, group.comment)).upper().replace("_", " ")
    # Solder bridges and zero-ohm jumpers are not pin headers.
    if re.search(r"SOLDER\s*(JUMPER|BRIDGE)|\b0\s*(OHM|Ω)\b", text):
        return result("SMT", "name hint — review")
    if re.search(r"PIN[ -]?HEADER|PIN[ -]?SOCKET|SOCKET[ -]?HEADER|\bJUMPERS?\b|ПЕРЕМЫЧК", text):
        return result("THT", "name hint — review")
    return result("SMT", "unconfirmed — review")


def assembly_groups(groups, pnp, overrides=None, order=None, sort_mode="standard", excluded=None):
    """Split BOM rows by size and installation stage, preserving board side."""
    overrides = overrides or {}
    excluded = set(excluded or [])
    ranks = {d: i for i, d in enumerate(order or [])}
    result = {}
    for side, gs in groups.items():
        split = []
        for g in gs:
            buckets = {}
            for d in g.designators:
                if d in excluded:
                    continue
                c = pnp.get(d)
                stage = overrides.get(d) or component_stage(c, g)
                if stage not in ("SMT", "THT", "Manual"):
                    stage = component_stage(c, g)
                size = tuple(round(v, 4) for v in (c.sort_size or (c.w,c.h))) if c else (float("inf"), float("inf"))
                # An explicitly ordered part can be moved independently of its BOM row.
                key = (stage, size)
                buckets.setdefault(key, []).append(d)
            for (stage, size), ds in buckets.items():
                if ranks:
                    ds.sort(key=lambda d: ranks.get(d, len(ranks)))
                runs = []
                for d in ds:
                    if (not runs or (d in ranks and runs[-1][-1] in ranks and
                                     ranks[d] != ranks[runs[-1][-1]] + 1)):
                        runs.append([])
                    runs[-1].append(d)
                for run in runs:
                    split.append(replace(g, designators=run, quantity=len(run), stage=stage))
        if sort_mode == "standard":
            def size_key(g):
                cs = [pnp[d] for d in g.designators if d in pnp]
                dims = [c.sort_size or (c.w,c.h) for c in cs]
                size = max(((w*h, max(w,h)) for w,h in dims), default=(float("inf"), float("inf")))
                unknown = not cs or any(not c.sort_size and c.size_source == "unknown" for c in cs)
                return (unknown,) + size + (natural_sort_key(g.designators[0]),)
            split.sort(key=size_key)
        if ranks:
            split.sort(key=lambda g: min((ranks.get(d, len(ranks)) for d in g.designators), default=len(ranks)))
        split.sort(key=lambda g: g.dni)
        result[side] = split
    return result


def assembly_sections(groups):
    """The same sections drive the preview and PDF, with local sheet numbers."""
    sections = []
    for stage, sides in (("SMT", ("Bottom",)), ("SMT", ("Top",)),
                         ("THT / Manual", ("Bottom", "Top"))):
        sheets = []
        for side in sides:
            gs = [g for g in groups.get(side, [])
                  if (g.stage == "SMT") == (stage == "SMT")]
            for dni in (False, True):
                indexed = [(i, g) for i, g in enumerate(gs) if g.dni == dni]
                for start in range(0, len(indexed), ROWS_PER_PAGE):
                    chunk = indexed[start:start + ROWS_PER_PAGE]
                    gwc = [(g, DNI_COLOR if dni else PALETTE[i % len(PALETTE)], i + 1)
                           for i, g in chunk]
                    sheets.append((side, STR["dni_page"] if dni else stage, gwc))
        if sheets:
            sections.append((stage, sheets))
    return sections


def generate_pdf(pnp, groups_by_layer, output_path,
                 project_name="Project", designed_by="", edition="A",
                 progress_callback=None, board_layers=None,
                 sheet_format="A4", landscape=True, scale_mode="auto",
                 meta=None, page_name=""):
    """`page_name` marks a custom page: it heads the view caption and the
    title block's supplementary title on every sheet."""
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
    for _, sheets in assembly_sections(groups_by_layer):
        for local_page, (layer, mt, gwc) in enumerate(sheets, 1):
            plan.append((layer, mt, gwc, local_page, len(sheets)))
    total = len(plan)
    if total == 0: return 0

    with PdfPages(output_path) as pdf:
        for pn, (layer, mt, gwc, local_page, section_total) in enumerate(plan, 1):
            sheet = Sheet(sheet_format, landscape)
            draw_frame(sheet)
            draw_board_view(sheet, pnp, gwc, layer, bounds, scale, scale_txt,
                            board_layers=board_layers, page_name=page_name)
            draw_parts_list(sheet, gwc, lay["rowh"])
            view = STR["view_bottom"] if layer == "Bottom" else STR["view_top"]
            sub = [page_name, view, mt] if page_name else [view, mt]
            draw_title_block(sheet, info, local_page, section_total, " · ".join(sub), scale_txt)
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
    ZOOM_STEP = 1.15
    # Focus mode: everything but the chosen group fades almost to the board
    # colour, so the group stands out without hiding where on the board it is.
    FADE_OUTLINE = "#dddddd"
    FADE_ART = "#efefef"
    FADE_COMP = "#f3f3f3"
    RING_BELOW = 16     # px - parts smaller than this on screen get a locator ring
    CLICK_SLOP = 4      # px - a press that moves further than this is a pan

    def __init__(self, parent):
        self.canvas = tk.Canvas(parent, bg="#0a0e1a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.zoom = 1.0
        self.off_x = self.off_y = 0
        self.components = []
        self.colors = {}
        self.active = set()
        self.hl = set()
        self.focus = set()
        self.on_click = None        # called for a click on the board, not a pan
        self.bounds = (0, 100, 0, 80)
        self.side = "Bottom"
        self.board_layers = None
        self._px = self._py = 0
        self._press_at = None
        self._last_size = (0, 0)
        # Wheel away from you zooms in
        self.canvas.bind("<MouseWheel>", lambda e: self._do_zoom(
            e, self.ZOOM_STEP if e.delta > 0 else 1 / self.ZOOM_STEP))
        self.canvas.bind("<Button-4>", lambda e: self._do_zoom(e, self.ZOOM_STEP))
        self.canvas.bind("<Button-5>", lambda e: self._do_zoom(e, 1 / self.ZOOM_STEP))
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._pan)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Configure>", self._on_resize)

    def load(self, comps, side, colors, active, bounds, board_layers=None):
        # Rotation and shape are needed for faithful rendering (previously every
        # component was drawn as a horizontal rectangle).
        self.components = [(c.designator, c.x, c.y, c.w, c.h, c.prefix,
                            c.rotation, c.shape)
                           for c in comps if c.layer == side]
        self.side = side; self.colors = colors; self.active = active
        self.bounds = bounds; self.hl = set(); self.focus = set()
        self.board_layers = board_layers
        self.canvas.after(100, self.fit)

    def highlight(self, desigs):
        new = set(desigs) if desigs else set()
        if new != self.hl:          # avoid redraw when moving between child
            self.hl = new           # widgets of the same legend row
            self.draw()

    def set_focus(self, desigs):
        """Fade the board around these parts until cleared with None.
        Unlike load() this keeps the zoom, so a zoomed-in area stays put."""
        self.focus = set(desigs) if desigs else set()
        self.draw()

    def fit(self):
        if not self.canvas.winfo_exists():
            return      # the workspace was closed before the timer fired
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
        # Hovering a legend row previews that group; otherwise the clicked one
        shown = self.hl or self.focus
        faded = bool(self.focus)

        # Always draw white board background
        bx1, by1 = self._tx(b[0]+1), self._ty(b[3]-1)
        bx2, by2 = self._tx(b[1]-1), self._ty(b[2]+1)
        c.create_rectangle(min(bx1,bx2), min(by1,by2), max(bx1,bx2), max(by1,by2),
                          fill=self.BOARD_BG, outline="#a8a090", width=2)

        # 1) Board outline from Gerber
        if has_gerber and self.board_layers.get("outline"):
            self._draw_entities(self.board_layers["outline"],
                                self.FADE_OUTLINE if faded else "#888877", 1.0)

        # 2) Gerber overlay (dark on white board) — UNDER colored blocks
        if has_gerber:
            key = "bottom" if self.side == "Bottom" else "top"
            if self.board_layers.get(key):
                self._draw_entities(self.board_layers[key],
                                    self.FADE_ART if faded else "#444444", 0.7)

        # 3) Colored blocks, solid: a stippled block over the artwork turned
        #    neighbouring colours into the same speckled grey
        rings = []
        for d, cx, cy, w, h, pfx, rot, shape in self.components:
            X, Y, pts = self._corners(cx, cy, w, h, rot)
            sw, sh = w*self.zoom, h*self.zoom
            is_act = d in self.active
            is_hl = d in shown
            col = self.colors.get(d, self.COMP_IDLE)
            if shown:
                fill = col if is_hl else (self.FADE_COMP if faded else "#e8e8e8")
                ol = shade(col, 0.6) if is_hl else ("" if faded else "#ccc")
                lw = 1.0 if is_hl else 0.2
                if is_hl and faded and max(sw, sh) < self.RING_BELOW:
                    rings.append((X, Y, max(sw, sh) / 2 + 7, col))
            else:
                fill = col if is_act else self.COMP_IDLE
                ol = shade(col, 0.6) if is_act else "#ccc"
                lw = 0.8 if is_act else 0.3
            if shape == "circle":
                r = w/2*self.zoom
                c.create_oval(X-r, Y-r, X+r, Y+r, fill=fill, outline=ol,
                             width=lw)
            elif rot % 360 == 0:
                c.create_rectangle(X-sw/2, Y-sh/2, X+sw/2, Y+sh/2,
                                   fill=fill, outline=ol, width=lw)
            else:
                c.create_polygon(pts, fill=fill, outline=ol,
                                width=max(lw, 0.5))

        # 4) Locator rings, so a focused 0402 can be found without zooming.
        #    The dark rim keeps a yellow ring visible on the white board.
        for X, Y, r, col in rings:
            c.create_oval(X-r, Y-r, X+r, Y+r, outline="#1f2937", width=4)
            c.create_oval(X-r, Y-r, X+r, Y+r, outline=col, width=2)

        # 5) Labels (topmost)
        for d, cx, cy, w, h, pfx, rot, shape in self.components:
            X, Y, pts = self._corners(cx, cy, w, h, rot)
            sw, sh = w*self.zoom, h*self.zoom
            if d not in shown and (faded or d not in self.active): continue
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

    def _press(self, e):
        self._px, self._py = e.x, e.y
        self._press_at = (e.x, e.y)

    def _pan(self, e):
        self.off_x += e.x - self._px; self.off_y += e.y - self._py
        self._px, self._py = e.x, e.y; self.draw()

    def _release(self, e):
        if self._press_at and self.on_click:
            x0, y0 = self._press_at
            if abs(e.x - x0) <= self.CLICK_SLOP and abs(e.y - y0) <= self.CLICK_SLOP:
                self.on_click()
        self._press_at = None


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
        self.root.geometry("1200x750")
        self.root.configure(bg="#0a0e1a")
        self.main_built = False
        self.exporting = False
        self.focus_group = None  # legend index of the clicked group on this side
        self.legend_rows = {}    # legend index -> row frame on the current page
        self._reset_project()
        # Ctrl+S matched by key code (83 = VK_S on Windows), not by <Control-s>:
        # with a Cyrillic layout the keysym is "ы" and <Control-s> never fires.
        # One binding only, so the save dialog cannot open twice.
        self.root.bind("<Control-KeyPress>", lambda e: self._save_project()
                       if e.keycode == 83 or e.keysym.lower() == "s" else None)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show_welcome()

    def _reset_project(self):
        """Everything a project holds, back to an empty, unsaved state."""
        self.pnp = {}; self.bom_data = []; self.groups = {"Top":[],"Bottom":[]}
        # Pick & Place as parsed. BOM grouping writes into the components, so
        # every regroup starts from a copy of this - a replaced BOM leaves no trace.
        self.pnp_parsed = {}
        self.side = "Bottom"; self.page = 0; self.board_layers = None
        self.sort_mode = "standard"  # standard | bom | custom
        self.custom_order = []  # user-defined prefix order
        self.stage = "SMT"
        self.mount_overrides = {}
        self.assembly_order = []
        self.excluded_refs = set()
        self.size_overrides = {}
        self.installation_groups = {}
        self.geometry_evidence = {}
        self.archive_drill_evidence = {}
        self.drill_evidence = {}
        self.npth_evidence = {}
        self.geometry_warnings = []
        self.files = {"pnp": "", "bom": "", "gerber": "", "drill": "", "npth": ""}
        self.auto_project = ""  # title taken from the P&P name, not typed in
        self.pages = []         # custom pages: {"name": ..., "refs": typed text}
        self.view = None        # index into self.pages, None = full drawing
        self.shown = {}         # groups by layer for the current view
        self.project_path = ""
        self.dirty = False
        self.meta = {"project": "Assembly", "designer": "", "edition": "A",
                     "owner": "", "doc_no": "", "approver": "",
                     "sheet_format": "A4", "orientation": "Landscape",
                     "scale_mode": "auto",
                     "date": datetime.date.today().isoformat()}
        self._update_title()

    def _update_title(self):
        name = ""
        if self.project_path:
            name = " — " + os.path.basename(self.project_path)
            if self.dirty:
                name += " *"
        self.root.title(f"PCB Assembly Studio v{APP_VERSION}{name}")

    def _mark_dirty(self):
        if not self.dirty:
            self.dirty = True
            self._update_title()

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
                                ("gerber","Gerber + NC drill ZIP / DXF",".zip / .dxf")]:
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

        if self.project_path:
            proj = tk.Frame(right, bg="#0a0e1a")
            proj.pack(fill="x", pady=(0, 8))
            tk.Label(proj, text="Project  " + os.path.basename(self.project_path),
                     font=self.F_BTN, fg=self.C_TEXT, bg="#0a0e1a").pack(side="left")
            # Without closing, Save would write different files into this project
            self._rounded_btn(proj, "Close project", self._close_project,
                bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
                font=self.F_SMALL, padx=12, pady=4, radius=7).pack(side="right")

        self.drop_zones = {}
        for key, title, ftypes, exts in [
            ("pnp", "Pick & Place", [("Text/CSV/POS","*.txt *.csv *.pos")], ".txt .csv .pos"),
            ("bom", "BOM", [("Text/CSV/Excel","*.txt *.csv *.xlsx *.xls")], ".txt .csv .xlsx"),
            ("gerber", "Gerber + NC drill ZIP / DXF", [("ZIP/DXF","*.zip *.dxf *.dwg")], ".zip .dxf"),
        ]:
            zone = tk.Frame(right, bg="#111827", highlightthickness=2,
                           highlightbackground="#334155", cursor="hand2")
            zone.pack(fill="x", pady=4, ipady=12)

            inner = tk.Frame(zone, bg="#111827")
            inner.pack(fill="x", padx=16)

            lbl = tk.Label(inner, font=("Segoe UI", 10), bg="#111827", anchor="w")
            lbl.pack(side="left")
            # Unload button. Its own click binding does not reach the zone
            # behind it, so removing a file never opens the browse dialog.
            clear = tk.Label(inner, text="", width=2, font=("Segoe UI", 11),
                             fg="#8b96ab", bg="#111827", cursor="hand2")
            clear.pack(side="right", padx=(10, 0))
            clear.bind("<Button-1>", lambda e, k=key: self._unload_file(k))
            clear.bind("<Enter>", lambda e, c=clear: c.configure(fg="#f87171"))
            clear.bind("<Leave>", lambda e, c=clear: c.configure(fg="#8b96ab"))
            tk.Label(inner, text=exts, font=("Segoe UI", 8), fg="#4b5568",
                    bg="#111827").pack(side="right")

            self.drop_zones[key] = (title, lbl, clear)

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

        bar = tk.Frame(right, bg="#0a0e1a")
        bar.pack(pady=(16, 0))
        # Continue - greyed out until a Pick & Place is loaded
        self.start_btn = self._rounded_btn(bar, "▶  Continue",
            self._try_continue,
            bg="#4f7cff", fg="white", hover="#3b63d9",
            font=("Segoe UI", 12, "bold"), padx=24, pady=10, radius=10)
        self.start_btn.pack(side="left")
        self._rounded_btn(bar, "📂  Open project…", self._open_project,
            bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
            font=("Segoe UI", 12, "bold"), padx=20, pady=10, radius=10).pack(
                side="left", padx=(12, 0))

        tk.Label(right, text="100% offline · all files stay on your computer",
                font=("Segoe UI", 8), fg="#334155", bg="#0a0e1a").pack(
                    side="bottom", pady=4)
        # Coming back from the workspace, the files loaded so far are shown
        self._update_welcome()

    def _update_welcome(self):
        """Dots, zone labels, unload buttons and the Open button, from self.files."""
        if self.main_built:
            return      # files imported from the workspace - no welcome screen
        for k, path in self.files.items():
            if k not in self.file_dots:
                continue  # legacy projects may link separate drill files
            self.file_dots[k].configure(fg="#22c55e" if path else "#f59e0b")
            title, lbl, clear = self.drop_zones[k]
            if path:
                lbl.configure(text=f"✅  {os.path.basename(path)}", fg="#22c55e")
            else:
                lbl.configure(text=f"📁  Drop {title} here or click to browse",
                              fg="#8b96ab")
            clear.configure(text="✕" if path else "")
        self._set_btn_enabled(self.start_btn, bool(self.files["pnp"]))

    def _load_file_typed(self, ftype, path):
        try:
            if ftype == "pnp":
                self.pnp_parsed = parse_pick_place(path)
                # Replace a title that came from the previous P&P file name,
                # but never one the user typed in
                name = project_name(path)
                if self.meta.get("project") in ("", "Assembly", self.auto_project):
                    self.meta["project"] = name
                self.auto_project = name
                self._regroup()
            elif ftype == "bom":
                bom = parse_bom(path)
                if not bom:
                    raise ValueError(
                        "The BOM was read but contains no component rows.\n"
                        "Check the Designator / Comment columns.")
                self.bom_data = bom
                self._regroup()
            elif ftype == "gerber":
                drawing = load_board_drawing(path)
                evidence = load_gerber_evidence(path) if path.lower().endswith('.zip') else {}
                drills = load_drill_evidence(path, required=False) if path.lower().endswith('.zip') else {}
                self.board_layers, self.geometry_evidence = drawing, evidence
                self.archive_drill_evidence = drills
                # Replacing the bundle must not retain drilling from another board.
                self.drill_evidence, self.npth_evidence = {}, {}
                self.files['drill'], self.files['npth'] = '', ''
                self._regroup()
            elif ftype in ("drill", "npth"):
                evidence = load_drill_evidence(path)
                if ftype == "drill":
                    self.drill_evidence = evidence
                else:
                    self.npth_evidence = evidence
                self._regroup()
                if evidence.get('warnings'):
                    messagebox.showwarning("Drill data needs review", "\n".join(evidence['warnings'][:5]))
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return
        self.files[ftype] = path
        self._mark_dirty()
        self._update_welcome()

    def _unload_file(self, ftype):
        if not self.files[ftype]:
            return
        if ftype == "pnp":
            self.pnp_parsed = {}
            self._regroup()
        elif ftype == "bom":
            self.bom_data = []
            self._regroup()
        elif ftype == "gerber":
            self.board_layers = None
            self.geometry_evidence = {}
            self.archive_drill_evidence = {}
            self._regroup()
        elif ftype in ("drill", "npth"):
            if ftype == "drill": self.drill_evidence = {}
            else: self.npth_evidence = {}
            self._regroup()
        self.files[ftype] = ""
        self._mark_dirty()
        self._update_welcome()

    # ══════════════════════════════════════════════════════════════════
    #  PROJECT FILE
    # ══════════════════════════════════════════════════════════════════

    def _save_project(self):
        """Save to the current project file, asking for one the first time.
        Returns True when the project was written."""
        path = self.project_path
        if not path:
            name = re.sub(r'[\\/:*?"<>|]+', "_", self.meta.get("project", "")).strip(" .")
            path = filedialog.asksaveasfilename(title="Save project",
                initialfile=(name or "Assembly") + PROJECT_EXT,
                # next to the P&P, so the stored relative paths stay short
                initialdir=os.path.dirname(self.files["pnp"]) or None,
                defaultextension=PROJECT_EXT,
                filetypes=[("PCB Assembly Studio project", "*" + PROJECT_EXT)])
            if not path:
                return False
        folder = os.path.dirname(os.path.abspath(path))
        data = {
            "app": "PCB Assembly Studio", "version": APP_VERSION,
            "format": PROJECT_FORMAT,
            "files": {k: file_ref(p, folder) for k, p in self.files.items() if p},
            "sort_mode": self.sort_mode,
            "custom_order": self.custom_order,
            "meta": {k: self.meta.get(k, "") for k in PROJECT_META_KEYS},
            "pages": self.pages,
            "mount_overrides": self.mount_overrides,
            "assembly_order": self.assembly_order,
            "excluded_refs": sorted(self.excluded_refs),
            "size_overrides": self.size_overrides,
        }
        try:
            write_project(path, data)
        except OSError as e:
            messagebox.showerror("Error", "Could not save the project:\n%s" % e)
            return False
        self.project_path = path
        self.dirty = False
        self._update_title()
        if self.main_built:
            self.status.configure(text="Saved → " + os.path.basename(path))
        return True

    def _confirm_discard(self):
        """True when the current project may be dropped: nothing worth keeping
        is unsaved, or the user saved it or chose not to."""
        if not self.dirty or not (self.project_path or self.pages):
            return True
        ans = messagebox.askyesnocancel("Unsaved changes",
            "Save changes to the project before closing it?")
        if ans is None:
            return False
        return self._save_project() if ans else True

    def _on_close(self):
        if self.exporting:
            # a half-written PDF is worse than a second click
            self.status.configure(text="Wait for the PDF export to finish")
            return
        if self._confirm_discard():
            self.root.destroy()

    def _close_project(self):
        if not self._confirm_discard():
            return
        self._reset_project()
        self.welcome.destroy()
        self._show_welcome()

    def _open_project(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(title="Open project",
            filetypes=[("PCB Assembly Studio project", "*" + PROJECT_EXT),
                       ("All", "*.*")])
        if not path:
            return
        try:
            data = read_project(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Error", str(e))
            return
        self._reset_project()
        self.project_path = path
        self.welcome.destroy()
        self._show_welcome()
        folder = os.path.dirname(os.path.abspath(path))
        self.meta.update({k: v for k, v in (data.get("meta") or {}).items()
                          if k in PROJECT_META_KEYS})
        self.sort_mode = data.get("sort_mode", "standard")
        self.custom_order = list(data.get("custom_order") or [])
        self.mount_overrides = {str(d): s for d, s in (data.get("mount_overrides") or {}).items()
                                if s in ("SMT", "THT", "Manual")}
        self.assembly_order = list(dict.fromkeys(str(d) for d in data.get("assembly_order") or []))
        self.excluded_refs = set(str(d) for d in data.get("excluded_refs") or [])
        self.size_overrides = valid_size_overrides(data.get("size_overrides"))
        self.pages = [{"name": str(p.get("name", "")), "refs": str(p.get("refs", ""))}
                      for p in data.get("pages") or []]
        relocated = False
        names = {"pnp": "Pick & Place", "bom": "BOM", "gerber": "Gerber / DXF",
                 "drill": "Plated drills", "npth": "Non-plated drills"}
        for ftype in ("pnp", "bom", "gerber", "drill", "npth"):
            ref = (data.get("files") or {}).get(ftype)
            if not ref:
                continue
            found = resolve_file_ref(ref, folder)
            if not found:
                if not messagebox.askyesno("File not found",
                        "The %s file of this project was not found:\n%s\n\n"
                        "Locate it?" % (names[ftype], ref.get("abs", ref.get("rel")))):
                    continue
                found = filedialog.askopenfilename(title="Locate " + names[ftype],
                                                   initialdir=folder)
                if not found:
                    continue
                relocated = True
            self._load_file_typed(ftype, found)
        # A file found in a new place is a change the project should remember
        self.dirty = relocated
        self._update_title()
        self._update_welcome()
        if self.files["pnp"]:
            self._switch_to_main()

    # ══════════════════════════════════════════════════════════════════
    #  MAIN WORKSPACE
    # ══════════════════════════════════════════════════════════════════

    def _try_continue(self):
        if self.files["pnp"]:
            self._switch_to_main()

    def _switch_to_main(self):
        if self.main_built:
            return
        self.welcome.destroy()
        self.main_built = True
        self.page = 0       # the file set may have changed since the last visit
        self._build_main()
        self.root.after(200, self._refresh)

    def _back_to_files(self):
        """Return to the file screen to replace or remove a loaded file."""
        if self.exporting:
            # the export callback still reports into this window
            self.status.configure(text="Wait for the PDF export to finish")
            return
        self.root.unbind("<Escape>")
        self.main.destroy()
        self.main_built = False
        self._show_welcome()

    def _build_main(self):
        # One frame holds the whole workspace, so going back to the file
        # screen removes it in a single destroy()
        self.main = tk.Frame(self.root, bg="#0a0e1a")
        self.main.pack(fill="both", expand=True)

        # Toolbar
        tb = tk.Frame(self.main, bg="#111827", height=52)
        tb.pack(fill="x"); tb.pack_propagate(False)
        self._rounded_btn(tb, "←  Files", self._back_to_files,
            bg="#1a2236", fg="#8b96ab", hover="#232d45",
            font=self.F_BTN, padx=14, min_h=self.BTN_H).pack(
                side="left", padx=(14, 0))
        tk.Label(tb, text="▦  PCB Assembly Studio", font=self.F_BRAND,
                fg="white", bg="#111827").pack(side="left", padx=14)

        self._side_btn_frame = tk.Frame(tb, bg="#111827")
        self._side_btn_frame.pack(side="left", padx=6)
        self._rebuild_side_btns()

        self._rounded_btn(tb, "📂  Import", self._import_more,
            bg="#1a2236", fg="#8b96ab", hover="#232d45",
            font=self.F_BTN, padx=14, min_h=self.BTN_H).pack(
                side="left", padx=8)
        self._rounded_btn(tb, "💾  Save", self._save_project,
            bg="#1a2236", fg="#8b96ab", hover="#232d45",
            font=self.F_BTN, padx=14, min_h=self.BTN_H).pack(side="left")
        self.export_btn = self._rounded_btn(tb, "📄  Export PDF",
            self._export_pdf, bg="#10b981", hover="#0d9668",
            font=self.F_BTN, padx=16, min_h=self.BTN_H)
        self.export_btn.pack(side="right", padx=14)

        body = tk.Frame(self.main, bg="#0a0e1a")
        body.pack(fill="both", expand=True)

        # Left panel: sort mode
        self.left_panel = tk.Frame(body, bg="#111827", width=216)
        self.left_panel.pack(side="left", fill="y")
        self.left_panel.pack_propagate(False)

        # Board
        bf = tk.Frame(body, bg="#0a0e1a")
        bf.pack(side="left", fill="both", expand=True)
        self.board = BoardCanvas(bf)
        self.board.on_click = lambda: self._set_focus(None)
        self.root.bind("<Escape>", lambda e: self._set_focus(None))

        # Right panel: legend
        self.right_panel = tk.Frame(body, bg="#111827", width=320)
        self.right_panel.pack(side="right", fill="y")
        self.right_panel.pack_propagate(False)

        # Status
        self.status = tk.Label(self.main, text="", bg="#111827", fg="#8b96ab",
            font=("Segoe UI", 9), anchor="w", padx=14)
        self.status.pack(fill="x")

    # ── Left panel: sort modes ────────────────────────────────────────

    def _build_left(self):
        for w in self.left_panel.winfo_children(): w.destroy()

        tk.Label(self.left_panel, text="SORT ORDER",
                font=("Segoe UI", 9, "bold"), fg="#8b96ab", bg="#111827",
                anchor="w").pack(fill="x", padx=14, pady=(14, 8))

        modes = [
            ("standard", "Smallest first", "All types, increasing body size", "#3b82f6"),
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

        tk.Label(self.left_panel, text="ASSEMBLY PREVIEW", bg=self.C_PANEL,
                 fg=self.C_MUTED, font=self.F_SMALL).pack(pady=(12, 4))
        stage = ttk.Combobox(self.left_panel, state="readonly", width=22,
                            values=("SMT", "THT / Manual"))
        stage.set(self.stage)
        stage.pack(padx=14, fill="x")
        stage.bind("<<ComboboxSelected>>", lambda e: self._set_stage(stage.get()))
        self._rounded_btn(self.left_panel, "Edit installation…", self._assembly_dialog,
            bg=self.C_CARD, fg=self.C_TEXT, hover=self.C_HOVER,
            font=self.F_SMALL).pack(padx=14, pady=8, fill="x")
        self._build_pages()

    # ── Left panel: custom pages ──────────────────────────────────────

    def _build_pages(self):
        head = tk.Frame(self.left_panel, bg=self.C_PANEL)
        head.pack(fill="x", padx=14, pady=(18, 6))
        tk.Label(head, text="PAGES", font=("Segoe UI", 9, "bold"),
                 fg=self.C_MUTED, bg=self.C_PANEL).pack(side="left")
        add = tk.Label(head, text="+ New", font=("Segoe UI", 9, "bold"),
                       fg=self.C_ACCENT, bg=self.C_PANEL, cursor="hand2")
        add.pack(side="right")
        add.bind("<Button-1>", lambda e: self._page_dialog())

        # The list scrolls once there are more pages than fit under the cards
        cv = tk.Canvas(self.left_panel, bg=self.C_PANEL, highlightthickness=0,
                       yscrollincrement=34)
        cv.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        inner = tk.Frame(cv, bg=self.C_PANEL)
        win = cv.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))

        full = sum(g.quantity for gs in self.groups.values() for g in gs)
        rows = [(None, "Full drawing", full)]
        for i, p in enumerate(self.pages):
            groups, _missing = self._page_view(i)
            rows.append((i, p["name"], sum(g.quantity for gs in groups.values()
                                           for g in gs)))
        for idx, name, count in rows:
            sel = idx == self.view
            bg = self.C_HOVER if sel else self.C_CARD
            row = tk.Frame(inner, bg=bg, height=30, cursor="hand2")
            row.pack(fill="x", pady=2)
            row.pack_propagate(False)
            tk.Frame(row, bg=self.C_ACCENT if sel else bg, width=3).pack(
                side="left", fill="y")
            if idx is not None:
                for sym, cmd, hot in (("✕", self._delete_page, "#f87171"),
                                      ("✏", self._page_dialog, self.C_TEXT)):
                    b = tk.Label(row, text=sym, font=self.F_SMALL,
                                 fg=self.C_MUTED, bg=bg, cursor="hand2")
                    b.pack(side="right", padx=(0, 6))
                    b.bind("<Button-1>", lambda e, c=cmd, i=idx: c(i))
                    b.bind("<Enter>", lambda e, w=b, h=hot: w.configure(fg=h))
                    b.bind("<Leave>", lambda e, w=b: w.configure(fg=self.C_MUTED))
            cnt = tk.Label(row, text=str(count), font=self.F_SMALL,
                           fg=self.C_MUTED, bg=bg)
            cnt.pack(side="right", padx=6)
            # Packed last, so a long name is what gets cut when space runs out
            nm = tk.Label(row, text=name, anchor="w", bg=bg,
                          font=self.F_BTN if sel else self.F_SMALL,
                          fg=self.C_TEXT if sel else "#94a3b8")
            nm.pack(side="left", fill="x", expand=True, padx=(8, 0))
            for w in (row, cnt, nm):
                w.bind("<Button-1>", lambda e, i=idx: self._set_view(i))

        def wheel(e):
            if inner.winfo_reqheight() > cv.winfo_height():
                cv.yview_scroll(-1 if e.delta > 0 else 1, "units")

        def bind_wheel(w):
            w.bind("<MouseWheel>", wheel)
            for ch in w.winfo_children():
                bind_wheel(ch)
        bind_wheel(cv)

    def _page_view(self, idx):
        """Custom page `idx` -> (groups by layer, typed designators that are
        not in the drawing)."""
        known = [d for gs in self.groups.values() for g in gs for d in g.designators]
        found, missing = parse_refs(self.pages[idx]["refs"], known)
        return page_groups(self.groups, found), missing

    def _set_view(self, idx, force=False):
        """Show the full drawing (None) or custom page `idx`."""
        if idx == self.view and not force:
            return
        self.view = idx
        self.page = 0
        shown = self.groups if idx is None else self._page_view(idx)[0]
        # A page with parts on one side only opens on that side
        if shown and not shown.get(self.side):
            self.side = "Top" if self.side == "Bottom" else "Bottom"
            self._rebuild_side_btns()
        self._refresh()

    def _page_dialog(self, idx=None):
        """Create a custom page, or edit page `idx`."""
        page = (self.pages[idx] if idx is not None else
                {"name": "Page %d" % (len(self.pages) + 1), "refs": ""})
        known = [d for gs in self.groups.values() for g in gs for d in g.designators]

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit page" if idx is not None else "New page")
        dlg.configure(bg=self.C_PANEL)
        dlg.transient(self.root); dlg.grab_set()
        dlg.resizable(False, False)

        def label(text, row, pady):
            tk.Label(dlg, text=text, bg=self.C_PANEL, fg=self.C_MUTED,
                     font=self.F_SMALL).grid(row=row, column=0, sticky="w",
                                             padx=14, pady=pady)

        label("Name", 0, (14, 4))
        e_name = tk.Entry(dlg, width=46, bg=self.C_CARD, fg=self.C_TEXT,
                          insertbackground="white", relief="flat",
                          font=self.F_SMALL)
        e_name.insert(0, page["name"])
        e_name.grid(row=1, column=0, sticky="we", padx=14, ipady=3)
        label("Designators", 2, (12, 4))
        txt = tk.Text(dlg, width=46, height=8, wrap="word", bg=self.C_CARD,
                      fg=self.C_TEXT, insertbackground="white", relief="flat",
                      font=self.F_SMALL, padx=6, pady=4)
        txt.insert("1.0", page["refs"])
        txt.grid(row=3, column=0, sticky="we", padx=14)
        tk.Label(dlg, text="Commas, spaces or new lines between them. "
                           "R7-R12 is a range.",
                 bg=self.C_PANEL, fg=self.C_FAINT, font=("Segoe UI", 8)).grid(
            row=4, column=0, sticky="w", padx=14, pady=(4, 0))
        # Lines reserved up front, so the window does not resize while typing
        status = tk.Label(dlg, bg=self.C_PANEL, fg=self.C_MUTED, font=self.F_SMALL,
                          anchor="w")
        status.grid(row=5, column=0, sticky="we", padx=14, pady=(10, 0))
        warn = tk.Label(dlg, bg=self.C_PANEL, fg="#f87171", font=self.F_SMALL,
                        anchor="nw", justify="left", wraplength=330, height=2)
        warn.grid(row=6, column=0, sticky="we", padx=14)

        def check(*_):
            found, missing = parse_refs(txt.get("1.0", "end"), known)
            groups = page_groups(self.groups, found)
            per_side = {s: sum(g.quantity for g in groups.get(s, []))
                        for s in ("Top", "Bottom")}
            n = per_side["Top"] + per_side["Bottom"]
            msg = "%d part%s" % (n, "" if n == 1 else "s")
            if n:
                msg += ": " + ", ".join("%d on %s" % (c, s.lower())
                                        for s, c in per_side.items() if c)
            status.configure(text=msg)
            warn.configure(text="Not in the drawing: " + ", ".join(missing[:10]) +
                                ("…" if len(missing) > 10 else "") if missing else "")
            return n

        result = {}

        def save():
            if not check():
                warn.configure(text="None of these designators are in the drawing.")
                return
            entry = {"name": e_name.get().strip() or page["name"],
                     "refs": txt.get("1.0", "end").strip()}
            if idx is None:
                self.pages.append(entry)
                result["view"] = len(self.pages) - 1
            else:
                self.pages[idx] = entry
                result["view"] = idx
            dlg.destroy()

        bar = tk.Frame(dlg, bg=self.C_PANEL)
        bar.grid(row=7, column=0, sticky="e", padx=14, pady=(8, 14))
        tk.Button(bar, text="Cancel", command=dlg.destroy, bg=self.C_CARD,
                  fg=self.C_MUTED, relief="flat", padx=14,
                  font=self.F_SMALL).pack(side="left", padx=(0, 8))
        tk.Button(bar, text="Save", command=save, bg=self.C_GREEN, fg="white",
                  relief="flat", padx=18, font=self.F_BTN).pack(side="left")

        txt.bind("<KeyRelease>", check)
        e_name.bind("<Return>", lambda e: txt.focus_set())
        dlg.bind("<Control-Return>", lambda e: save())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        check()
        (txt if idx is None else e_name).focus_set()
        dlg.wait_window()
        if "view" in result:
            self._mark_dirty()
            self._set_view(result["view"], force=True)

    def _delete_page(self, idx):
        if not messagebox.askyesno("Delete page",
                'Delete page "%s"?' % self.pages[idx]["name"]):
            return
        del self.pages[idx]
        if self.view == idx:
            self.view = None
            self.page = 0
        elif self.view is not None and self.view > idx:
            self.view -= 1
        self._mark_dirty()
        self._refresh()

    def _set_sort(self, mode):
        if mode != self.sort_mode:
            self._mark_dirty()
        self.sort_mode = mode
        self.page = 0
        self._regroup()
        self._refresh()

    def _regroup(self):
        self.pnp = {d: copy.copy(c) for d, c in self.pnp_parsed.items()}
        if not self.pnp:
            self.groups = {"Top": [], "Bottom": []}
            self.installation_groups = {}
            return

        # Build groups from BOM or auto
        if self.bom_data:
            self.groups = group_from_bom(self.bom_data, self.pnp)
        else:
            self.groups = group_auto(self.pnp)

        drills = {'holes': self.archive_drill_evidence.get('holes', []) +
                  self.drill_evidence.get('holes', []) + self.npth_evidence.get('holes', [])}
        decisions = infer_geometry(self.pnp, self.geometry_evidence, drills)
        self.geometry_warnings = list(dict.fromkeys(
            self.geometry_evidence.get('warnings', []) + self.drill_evidence.get('warnings', []) +
            self.npth_evidence.get('warnings', []) + self.archive_drill_evidence.get('warnings', [])))
        for d, info in decisions.items():
            comp = self.pnp[d]
            comp.sort_size = info.get('sort_size')
            comp.sort_source = info.get('size_source', '')
            comp.geometry_stage = info.get('stage', '')
            comp.geometry_review = '; '.join(info.get('review', []))

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

        apply_size_overrides(self.pnp, self.size_overrides)
        self.installation_groups = assembly_groups(self.groups, self.pnp, self.mount_overrides,
                                                  self.assembly_order, self.sort_mode)
        self.groups = page_groups(self.installation_groups,
                                 [d for gs in self.installation_groups.values()
                                  for g in gs for d in g.designators if d not in self.excluded_refs])
        # DNI groups always go last on a side - same order as in the PDF
        for layer in self.groups:
            self.groups[layer].sort(key=lambda g: g.dni)

    def _set_stage(self, stage):
        self.stage = stage
        self.page = 0
        self._refresh()

    def _assembly_dialog(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("Installation order — select parts to move or defer")
        dlg.geometry("1200x700")
        dlg.transient(self.root)
        dlg.grab_set()
        tk.Label(dlg, text="THT = through-hole. Manual = late hand installation (including SMD). Both go after SMT.\n"
                 "Exclude removes a part from the assembly list and PDF; Include restores it. Unknown sizes sort last.\n"
                 "Paste area is an estimated placement area, not body size. Inferred / review results need checking.\n"
                 "THT geometry detection needs Gerber outer copper + plated drill coordinates; it is not guaranteed.",
                 justify="left").pack(anchor="w", padx=12, pady=10)
        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=12)
        tree = ttk.Treeview(frame, columns=("side", "stage", "basis", "footprint", "size", "part"),
                            show="tree headings", selectmode="extended")
        tree.heading("#0", text="Ref")
        tree.column("#0", width=80)
        for key, title, width in (("side", "Side", 65), ("stage", "Stage", 85),
                                  ("basis", "Mount source / review", 210),
                                  ("footprint", "Footprint", 160), ("size", "Sort size mm / source", 215),
                                  ("part", "Part", 220)):
            tree.heading(key, text=title)
            tree.column(key, width=width)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        overrides = dict(self.mount_overrides)
        excluded = set(self.excluded_refs)
        sizes = copy.deepcopy(self.size_overrides)
        ordered = []
        records = {}
        for _, sheets in assembly_sections(self.installation_groups):
            for side, _, gwc in sheets:
                for g, _, _ in gwc:
                    for d in g.designators:
                        if d in records:
                            continue
                        c = self.pnp.get(d)
                        records[d] = (side, g, c)
                        ordered.append(d)

        def refresh():
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for d in ordered:
                side, g, c = records[d]
                detected = component_stage(c, g)
                dimensions = c.sort_size if c and c.sort_size else (c.w,c.h) if c else None
                source = (c.sort_source or c.size_source) if c else 'unknown'
                tree.insert("", "end", iid=d, text=d, values=(side,
                    "Excluded" if d in excluded else overrides.get(d) or detected,
                    "user override" if d in overrides else (c.mount_source + (" / " + c.geometry_review if c.geometry_review else "")) if c else "unconfirmed — review",
                    c.footprint if c else "—",
                    (f"{sizes[d][0]:g} × {sizes[d][1]:g} / manual" if d in sizes else
                     f"{dimensions[0]:g} × {dimensions[1]:g} / {source}" if dimensions and source != "unknown" else
                     "Unknown — set size"), g.comment))
            tree.selection_set(selected)
            if selected:
                tree.see(selected[0])

        def assign(value):
            for d in tree.selection():
                if value == "Auto":
                    overrides.pop(d, None)
                else:
                    overrides[d] = value
            refresh()

        def include(value):
            for d in tree.selection():
                if value:
                    excluded.discard(d)
                else:
                    excluded.add(d)
            refresh()

        size_bar = ttk.Frame(dlg)
        size_bar.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Label(size_bar, text="Size for selected parts, mm: ").pack(side="left")
        width = ttk.Entry(size_bar, width=8)
        width.pack(side="left")
        ttk.Label(size_bar, text=" × ").pack(side="left")
        height = ttk.Entry(size_bar, width=8)
        height.pack(side="left")

        def set_size(auto=False):
            selected = tree.selection()
            if not selected:
                return
            if not auto:
                size = valid_size_overrides({"value": [width.get().replace(",", "."), height.get().replace(",", ".")]})
                if not size:
                    messagebox.showerror("Size", "Enter two positive, finite dimensions in millimetres.", parent=dlg)
                    return
            for d in selected:
                if auto:
                    sizes.pop(d, None)
                    c = records[d][2]
                    if c:
                        c = copy.copy(c)
                        c._estimate_size()
                        # Restore automatic geometry, not the applied manual size.
                        inferred = infer_geometry(self.pnp, self.geometry_evidence,
                            {'holes': self.archive_drill_evidence.get('holes', []) +
                             self.drill_evidence.get('holes', []) + self.npth_evidence.get('holes', [])}).get(d, {})
                        c.sort_size = inferred.get('sort_size')
                        c.sort_source = inferred.get('size_source', '')
                        side, g, _ = records[d]
                        records[d] = (side, g, c)
                else:
                    sizes[d] = size["value"][:]
            refresh()

        ttk.Button(size_bar, text="Set size", command=set_size).pack(side="left", padx=4)
        ttk.Button(size_bar, text="Auto size", command=lambda: set_size(True)).pack(side="left")
        ttk.Button(size_bar, text="Geometry warnings…", command=lambda: messagebox.showinfo(
            "Geometry evidence", "\n".join(self.geometry_warnings) if self.geometry_warnings else
            "No file parsing warnings. Inferred matches are still estimates; review mounting and sizes.", parent=dlg)).pack(side="left", padx=4)
        ttk.Button(size_bar, text="Exclude", command=lambda: include(False)).pack(side="right")
        ttk.Button(size_bar, text="Include", command=lambda: include(True)).pack(side="right", padx=4)

        moved = [False]
        reset = [False]

        def reset_order():
            reset[0] = True
            dlg.title("Installation order — automatic size order will be restored on Apply")

        def move(delta):
            selected = set(tree.selection())
            indices = range(len(ordered)) if delta < 0 else range(len(ordered) - 1, -1, -1)
            for i in indices:
                j = i + delta
                if ordered[i] in selected and 0 <= j < len(ordered) and ordered[j] not in selected:
                    ordered[i], ordered[j] = ordered[j], ordered[i]
                    moved[0] = True
            refresh()

        bar = ttk.Frame(dlg)
        bar.pack(fill="x", padx=12, pady=12)
        for label, value in (("Auto detect", "Auto"), ("SMT", "SMT"), ("THT", "THT"), ("Install manually", "Manual")):
            ttk.Button(bar, text=label, command=lambda v=value: assign(v)).pack(side="left", padx=2)
        ttk.Button(bar, text="↑ Earlier", command=lambda: move(-1)).pack(side="left", padx=2)
        ttk.Button(bar, text="↓ Later", command=lambda: move(1)).pack(side="left", padx=2)

        def save():
            if (overrides != self.mount_overrides or moved[0] or reset[0] or
                    excluded != self.excluded_refs or sizes != self.size_overrides):
                self.mount_overrides = overrides
                self.excluded_refs = excluded
                self.size_overrides = sizes
                if reset[0]:
                    self.assembly_order = []
                elif moved[0]:
                    self.assembly_order = ordered
                self._mark_dirty()
                self._regroup()
                self.page = 0
                self._refresh()
            dlg.destroy()

        ttk.Button(bar, text="Apply", command=save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(dlg, text="Reset manual order to selected sort mode", command=reset_order).pack(pady=(0, 8))
        refresh()

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
            self._mark_dirty()
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
                fill, txt = shade(bg), self.C_FAINT
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
        cv.bind("<ButtonPress-1>",   lambda e: draw(shade(hover, .8)) if cv._enabled else None)
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

        sg = self.shown.get(self.side, [])
        tp = max(1, len(self.preview_sheets))
        # On a custom page the legend is titled with the page, as a reminder
        # that the board shows only part of the drawing
        title = "Legend" if self.view is None else self.pages[self.view]["name"]
        if len(title) > 16:
            title = title[:15] + "…"

        hdr = tk.Frame(self.right_panel, bg=self.C_PANEL)
        hdr.pack(fill="x", padx=16, pady=(16, 4))
        nav = tk.Frame(hdr, bg=self.C_PANEL); nav.pack(side="right")
        self._rounded_btn(nav, "‹", self._prev,
            bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
            font=self.F_BODY, padx=4, pady=3, radius=7,
            min_w=28, min_h=26).pack(side="left")
        tk.Label(nav, text=self.preview_number, font=("Segoe UI", 9),
                fg=self.C_MUTED, bg=self.C_PANEL).pack(side="left", padx=6)
        self._rounded_btn(nav, "›", self._next,
            bg=self.C_CARD, fg=self.C_MUTED, hover=self.C_HOVER,
            font=self.F_BODY, padx=4, pady=3, radius=7,
            min_w=28, min_h=26).pack(side="left")
        # Packed after the page buttons, so a long title is what gets squeezed
        tk.Label(hdr, text=title, font=("Segoe UI", 12, "bold"),
                fg=self.C_TEXT, bg=self.C_PANEL).pack(side="left")
        tk.Label(hdr, text=f"  {len(sg)}", font=("Segoe UI", 11),
                fg=self.C_MUTED, bg=self.C_PANEL).pack(side="left")

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
        start = self.preview_start
        self.legend_rows = {}
        for i, g in enumerate(self.preview_groups):
            ci = start + i
            color = DNI_COLOR if g.dni else install_colors.get(ci, PALETTE[0])

            row = tk.Frame(self.right_panel, bg=self.C_PANEL, cursor="hand2")
            self.legend_rows[ci] = row
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
            self._bind_click(row, ci)

    def _bind_hover(self, widget, desigs):
        widget.bind("<Enter>", lambda e, dd=desigs: self.board.highlight(dd))
        widget.bind("<Leave>", lambda e: self.board.highlight(None))
        for ch in widget.winfo_children():
            if getattr(ch, '_skip_hover', False):
                continue
            self._bind_hover(ch, desigs)

    def _bind_click(self, widget, ci):
        widget.bind("<Button-1>", lambda e: self._set_focus(
            None if self.focus_group == ci else ci))
        for ch in widget.winfo_children():
            self._bind_click(ch, ci)

    def _paint(self, widget, bg):
        widget.configure(bg=bg)
        for ch in widget.winfo_children():
            self._paint(ch, bg)

    def _set_focus(self, ci):
        """Focus the board on legend group `ci` of this side, or clear with None."""
        sg = self.shown.get(self.side, [])
        if ci is not None and ci >= len(sg):
            ci = None
        if not self.main_built or ci == self.focus_group:
            return
        self.focus_group = ci
        for idx, row in self.legend_rows.items():
            self._paint(row, self.C_HOVER if idx == ci else self.C_PANEL)
        self.board.set_focus(sg[ci].designators if ci is not None else None)

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
        sg = self.shown.get(self.side, [])
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
        if self.page < len(self.preview_sheets) - 1:
            self.page += 1; self._refresh()

    def _refresh(self):
        if not self.main_built:
            return      # left for the file screen before the timer fired
        self.focus_group = None     # board.load() clears the board side of it
        if self.view is not None and self.view >= len(self.pages):
            self.view = None
        if self.view is None:
            self.shown, missing = self.groups, []
        else:
            self.shown, missing = self._page_view(self.view)
        all_groups = self.shown
        self.shown = {side: [g for g in gs if (g.stage == "SMT") == (self.stage == "SMT")]
                      for side, gs in all_groups.items()}
        section = next((sheets for stage, sheets in assembly_sections(all_groups)
                        if stage == self.stage and any(s == self.side for s, _, _ in sheets)), [])
        self.preview_sheets = [(i, sheet) for i, sheet in enumerate(section, 1) if sheet[0] == self.side]
        self.page = min(self.page, max(0, len(self.preview_sheets) - 1))
        self.preview_start = sum(len(sheet[2]) for _, sheet in self.preview_sheets[:self.page])
        self.preview_groups = ([g for g, _, _ in self.preview_sheets[self.page][1][2]]
                               if self.preview_sheets else [])
        self.preview_number = (f"{self.preview_sheets[self.page][0]} / {len(section)}"
                               if self.preview_sheets else "0 / 0")
        sg = self.shown.get(self.side, [])
        pg = self.preview_groups
        install_colors = self._install_colors()
        colors = {}
        for ci, g in enumerate(sg):
            c = DNI_COLOR if g.dni else install_colors.get(ci, PALETTE[0])
            for d in g.designators: colors[d] = c
        active = set()
        for g in pg: active.update(g.designators)
        bounds = compute_bounds(self.pnp, self.board_layers)
        self.board.load([c for d, c in self.pnp.items() if d not in self.excluded_refs],
                       self.side, colors, active, bounds,
                       board_layers=self.board_layers)
        self._build_left()
        self._build_legend()
        top = sum(1 for c in self.pnp.values() if c.layer == "Top")
        bot = sum(1 for c in self.pnp.values() if c.layer == "Bottom")
        text = (f"{len(self.pnp)} comp (Top:{top} Bot:{bot}) · "
                f"{len(sg)} groups · {self.side} · scroll=zoom drag=pan · "
                f"click a legend row to focus, Esc to clear")
        if self.geometry_warnings:
            text += f" · {len(self.geometry_warnings)} geometry warning(s) — review in Edit installation"
        if self.view is not None:
            text = f"Page {self.pages[self.view]['name']} · " + text
            if missing:
                text += " · not in the drawing: " + ", ".join(missing)
        self.status.configure(text=text)

    def _sniff_type(self, path):
        """Detect the type of an imported file from its extension and contents."""
        ext = os.path.splitext(path)[1].lower()
        if ext == ".zip": return "gerber"
        if ext in (".drl", ".xln", ".exc", ".tap"): return "drill"
        if ext in (".dxf", ".dwg"): return "gerber"
        if ext in (".xlsx", ".xls"): return "bom"
        if ext == ".pos": return "pnp"
        try:
            head = _read_text(path)[:4000].lower()
        except Exception:
            head = ""
        if re.search(r'(?m)^\s*m48\b', head):
            return "npth" if re.search(r'type\s*=\s*non[_ -]?plated', head) else "drill"
        if (re.search(r'designator|(^|\W)ref(\W|$)', head)
                and re.search(r'layer|side', head)
                and re.search(r'center-x|mid x|pos ?x', head)):
            return "pnp"
        return "bom" if self.pnp else "pnp"

    def _import_more(self):
        paths = filedialog.askopenfilenames(title="Import",
            filetypes=[("All","*.txt *.csv *.pos *.xlsx *.xls *.zip *.dxf *.dwg *.drl *.xln *.exc *.tap"),
                       ("All","*.*")])
        for p in paths:
            ftype = self._sniff_type(p)
            if ftype in ('drill', 'npth'):
                messagebox.showinfo('NC drill files',
                    'Add plated and non-plated drill coordinate files to the Gerber ZIP, then load that archive.')
                continue
            self._load_file_typed(ftype, p)
        if self.main_built: self._refresh()

    # ── Export ────────────────────────────────────────────────────────

    def _ask_metadata(self):
        """Drawing setup: content, sheet, scale and the ISO 7200 title block.

        Returns (metadata dict, custom page index or None for the full
        drawing), or None when the dialog was cancelled.
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

        # Each custom page goes to a file of its own, so one is chosen here
        head("CONTENT", 0)
        tk.Label(dlg, text="Export", bg=self.C_PANEL, fg=self.C_MUTED,
                 font=self.F_SMALL).grid(row=1, column=0, sticky="w",
                                         padx=(14, 8), pady=4)
        content = ttk.Combobox(dlg, state="readonly", width=31, font=self.F_SMALL,
                               values=["Full drawing"] +
                                      ["Page: " + p["name"] for p in self.pages])
        content.current(0 if self.view is None else self.view + 1)
        content.grid(row=1, column=1, sticky="w", padx=(0, 14), pady=4)

        head("SHEET — ISO 5457", 2)
        v_fmt = combo("Format", "sheet_format",
                      list(SHEET_SIZES.keys()), "A4", 3)
        v_orient = combo("Orientation", "orientation",
                         ["Landscape", "Portrait"], "Landscape", 4)
        v_scale = combo("Scale", "scale_mode",
                        ["auto", "fit"] + ["%d:%d" % s for s in ISO_SCALES],
                        "auto", 5)

        head("TITLE BLOCK — ISO 7200", 6)
        e_title = entry("Title", "project", "Assembly", 7)
        e_owner = entry("Legal owner", "owner", "", 8)
        e_docno = entry("Identification no.", "doc_no", "", 9)
        e_rev = entry("Revision index", "edition", "A", 10)
        e_date = entry("Date of issue", "date",
                       datetime.date.today().isoformat(), 11)
        e_creator = entry("Created by", "designer", "", 12)
        e_appr = entry("Approved by", "approver", "", 13)

        # Fixed width and two reserved lines: the hint text changes length with
        # every selection, and without this the window would resize under the
        # pointer as the user picks values.
        hint = tk.Label(dlg, bg=self.C_PANEL, fg=self.C_FAINT,
                        font=("Segoe UI", 8), justify="left", anchor="nw",
                        wraplength=self.HINT_W, width=1, height=2)
        hint.grid(row=14, column=0, columnspan=2, sticky="we", padx=14,
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
        chosen = {}     # read before the dialog is destroyed

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
            pick = content.current()
            chosen["view"] = None if pick <= 0 else pick - 1
            dlg.destroy()

        bar = tk.Frame(dlg, bg=self.C_PANEL)
        bar.grid(row=15, column=0, columnspan=2, sticky="e", padx=14,
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
        if any(out.get(k) != self.meta.get(k) for k in PROJECT_META_KEYS):
            self._mark_dirty()
        self.meta = out
        return out, chosen["view"]

    def _export_pdf(self):
        if not self.pnp:
            messagebox.showinfo("Info", "Load Pick & Place first"); return
        # Setup first: the file name is built from the title entered there
        setup = self._ask_metadata()
        if setup is None: return
        meta, view = setup
        name = "Assembly drawing for %s" % meta["project"]
        if view is None:
            groups, page_name = self.groups, ""
        else:
            groups, page_name = self._page_view(view)[0], self.pages[view]["name"]
            name += " - " + page_name
        name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
        # The .pdf is spelled out - a title like "Board_v4.1" would otherwise
        # have ".1" taken for its extension
        path = filedialog.asksaveasfilename(title="Export PDF",
            initialfile=name + ".pdf",
            defaultextension=".pdf", filetypes=[("PDF","*.pdf")])
        if not path: return
        # Generate in a background thread so the GUI stays responsive
        self.exporting = True
        self._set_btn_enabled(self.export_btn, False)
        self.status.configure(text="Generating PDF...")
        threading.Thread(target=self._export_worker,
                         args=(path, meta, groups, page_name),
                         daemon=True).start()

    def _export_worker(self, path, meta, groups, page_name):
        def progress(pn, total, *args):
            self.root.after(0, lambda: self.status.configure(
                text=f"PDF: page {pn}/{total}..."))
        try:
            pages = generate_pdf({d: c for d, c in self.pnp.items() if d not in self.excluded_refs}, groups, path,
                sheet_format=meta.get("sheet_format", "A4"),
                landscape=meta.get("orientation", "Landscape") == "Landscape",
                scale_mode=meta.get("scale_mode", "auto"),
                board_layers=self.board_layers,
                meta=meta, page_name=page_name,
                progress_callback=progress)
            self.root.after(0, self._export_done, path, pages, None)
        except Exception as e:
            self.root.after(0, self._export_done, path, 0, str(e))

    def _export_done(self, path, pages, err):
        self.exporting = False
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
