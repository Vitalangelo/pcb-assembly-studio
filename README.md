# PCB Assembly Studio

Turn Altium or KiCad outputs — Gerber, BOM, Pick & Place — into multi-page **PCB
assembly drawings**. Interactive board viewer, print-ready PDF export.

**100% offline.** Everything is parsed on your machine. No uploads, no network calls.

---

## 📥 Download

**[PCBAssemblyStudio.exe](https://github.com/VitaliyaF/pcb-assembly-studio/releases/latest)** — one file, ~44 MB, double-click. No Python needed.

> Not code-signed, so Windows shows a warning on first run: **More info** → **Run anyway**.

## 🚀 Run from source

1. Install [Python 3.10+](https://www.python.org/downloads/) — tick **"Add Python to PATH"**
2. Green **Code** button above → **Download ZIP** → right-click the ZIP → **Properties** → tick **Unblock** → unzip
3. Double-click **`run.bat`**

> Don't skip the Unblock. Windows silently refuses to run `.bat` files unpacked
> from a downloaded ZIP — `run.bat` would just do nothing when you click it.

`run.bat` installs `matplotlib` and `gerbonara` the first time, then starts the app.
By hand:

```
python -m pip install matplotlib gerbonara
python pcb_assembly_studio.py
```

Optional extras: `openpyxl` for `.xlsx` BOMs, `ezdxf` for DXF, `tkinterdnd2` for
drag-and-drop.

Want your own `.exe`? Double-click `build_exe.bat` → `dist\PCBAssemblyStudio.exe`.

---

## 📋 Input Files

| File | Format | Export from Altium | Required |
|------|--------|--------------------|----------|
| **Pick & Place** | `.txt` `.csv` `.pos` | File → Assembly Outputs → Generates pick and place files | ✅ |
| **BOM** | `.txt` `.csv` `.xlsx` | Reports → Bill of Materials → Export | ✅ |
| **Gerber ZIP** | `.zip` | File → Fabrication Outputs → Gerber Files | Optional |

**Pick & Place** — Altium fixed-width or CSV, KiCad `.pos`. Simple
(`Designator Layer X Y Rotation`) or extended with Comment/Footprint/Description.
Millimeters or mils, auto-detected.

**BOM** — needs **Comment, Description, Designator, Layer**. Tab, `;` or `,`
delimiters auto-detected. DNI / DNP / "Do not place" parts are listed separately
with a ⛔ marker and get their own PDF pages; Russian markers
("НЕ УСТАНАВЛИВАТЬ", "НЕ СТАВИТЬ") count too, and cp1251 files decode correctly.

**Gerber ZIP** — layers by extension: `.GTO`/`.GBO` silkscreen, `.GTS`/`.GBS`
solder mask, `.GM1`–`.GM4`/`.GKO` outline. Cyrillic filenames inside the ZIP are fine.

---

## 🖥️ What You Get

**Viewer** — scroll to zoom, drag to pan. Gerber artwork as the background, colored
component highlights, pin-1 markers on ICs, bottom layer auto-mirrored.

**Legend** — 7 groups per page. Hover a row to light those parts up on the board,
hover REFS for the full designator list.

**Sort order** — Standard (SMD passives → semiconductors → ICs → THT), as in BOM,
or your own prefix order.

**PDF** — multi-page A4 landscape, 7 colors per page (red, yellow, green, cyan,
blue, magenta, dark red), board artwork from Gerber, title block with page numbers.

---

## ⚠️ Limitations

- Component sizes are estimated from description/package text. Load Gerbers for real pad geometry.
- Pin-1 markers come from Pick & Place rotation. Unusual library footprint orientation can put the marker in the wrong corner — check against your design.
- DWG needs ODA File Converter. Export DXF from Altium instead, or use a Gerber ZIP.

---

## 🌍 Drawing Language

The GUI is English-only, but text **inside the generated PDF** is localisable.
Set `LANG = "ru"` in `pcb_assembly_studio.py` for bilingual Russian/English table
headers. Only the drawing changes.

---

## 📝 Changelog

**v1.0** — Initial public release
