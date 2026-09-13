"""
Demo corpus generator - one realistic sample of every supported file type.

Why: real plant documents are confidential, and most public sample reports
sit behind paywalls (Scribd and similar). A demo needs a corpus that exercises
EVERY path in the pipeline - native text, tables, scans (OCR), forms,
spreadsheets, slides and drawings.

    python -m tools.make_corpus            # build everything into data/corpus
    python -m tools.make_corpus --clean    # remove the old files first

The numbers are deliberately consistent across files so that cross-document
questions work ("the report says 11.2 mm - what did the approval note say?").
That is the demo that lands hardest.
"""

from __future__ import annotations

import argparse
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
CORPUS = _ROOT / "data" / "corpus"

# ---- one source of truth, shared by every file -------------------------
TANK = "TK-4102"
PSV = "PSV-2041"
MEASURED, NOMINAL = 11.2, 12.0
PSV_SET, PSV_SPEC = 12.5, 14.0
REPORT_NO = "MRPL/INSP/2026/0412"
UNIT = "CDU-II"


# ---------------------------------------------------------------------------
def docx_inspection(p: Path) -> None:
    from docx import Document
    from docx.shared import Pt

    d = Document()
    d.add_heading("MRPL — EQUIPMENT INSPECTION REPORT", 0)
    d.add_paragraph(f"Report No: {REPORT_NO}    Unit: {UNIT}    Date: 12-Sep-2026")

    d.add_heading("1. Equipment Particulars", 1)
    t = d.add_table(rows=1, cols=4); t.style = "Table Grid"
    for i, h in enumerate(["Tag No", "Description", "Design Pressure", "Last Inspected"]):
        t.rows[0].cells[i].text = h
    for row in [
        (TANK, "Crude Storage Tank", "Atmospheric", "14-Mar-2024"),
        (PSV, "Safety Relief Valve", f"{PSV_SET} barg", "14-Mar-2024"),
        ("P-4110A", "Crude Transfer Pump", "18.0 barg", "02-Aug-2025"),
    ]:
        c = t.add_row().cells
        for i, v in enumerate(row):
            c[i].text = v

    d.add_heading("2. Observations", 1)
    for o in [
        f"a) Shell plate thickness at course-1 measured {MEASURED} mm against "
        f"nominal {NOMINAL} mm. Loss of 0.8 mm over 24 months.",
        f"b) {PSV} set pressure found at {PSV_SET} barg; SOP-114 specifies "
        f"{PSV_SPEC} barg. Deviation not previously documented.",
        "c) Minor external corrosion observed near nozzle N-3. No through-wall loss.",
        "d) Foundation grouting intact. No settlement recorded.",
    ]:
        d.add_paragraph(o, style="List Bullet")

    d.add_heading("3. Recommendation", 1)
    d.add_paragraph(
        f"Re-rate {TANK} per API 653 or restore thickness at next shutdown. "
        f"{PSV} to be bench-tested and reset to {PSV_SPEC} barg before restart."
    )
    d.add_paragraph("Inspected by: R. Nair (API 510 #48112)    Reviewed by: S. Kulkarni")
    d.save(p)


def docx_approval(p: Path) -> None:
    from docx import Document

    d = Document()
    d.add_heading(f"APPROVAL NOTE — {TANK}", 0)
    d.add_paragraph(f"Ref: {REPORT_NO}    MOC No: MOC-2026-0231")

    d.add_heading("Deviations", 1)
    d.add_paragraph(
        f"Shell plate thickness is {MEASURED} mm (below nominal {NOMINAL} mm). "
        f"Minimum required thickness per API 653 calculation is 10.4 mm, "
        f"therefore the vessel remains fit for service until next turnaround.",
        style="List Bullet")
    d.add_paragraph(
        f"{PSV} set at {PSV_SET} barg against SOP-114 requirement of {PSV_SPEC} barg. "
        f"This is a non-conformance and must be corrected before restart.",
        style="List Bullet")

    d.add_heading("Decision", 1)
    d.add_paragraph(
        "Conditional approval granted for continued operation until 31-Mar-2027, "
        "subject to: (1) quarterly UT monitoring at course-1, (2) PSV reset within "
        "14 days, (3) re-inspection before any change in service.")
    d.add_paragraph("Approved by: A. Deshpande, Head — Inspection    Date: 13-Sep-2026")
    d.save(p)


def docx_sop(p: Path) -> None:
    from docx import Document

    d = Document()
    d.add_heading("SOP-114 — Pressure Relief Device Setting & Testing", 0)
    d.add_paragraph("Revision 6    Effective: 01-Jan-2026    Owner: Inspection Dept")

    d.add_heading("4. Set Pressure Requirements", 1)
    d.add_paragraph(
        f"All safety relief valves protecting atmospheric storage tanks in {UNIT} "
        f"shall be set at {PSV_SPEC} barg unless a documented deviation exists, "
        f"approved by the Head of Inspection.")
    d.add_paragraph(
        "Set pressure shall be verified by bench test at intervals not exceeding "
        "24 months. Test records shall be retained for 10 years.")

    d.add_heading("5. Thickness Acceptance", 1)
    d.add_paragraph(
        "Minimum acceptable shell thickness shall be computed per API 653 "
        "Section 4.3. Measured thickness below nominal requires engineering "
        "evaluation but does not by itself constitute a rejection.")
    d.save(p)


# ---------------------------------------------------------------------------
def xlsx_thickness(p: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active; ws.title = "UT Readings"
    ws.append(["Tag", "Location", "Nominal (mm)", "Measured (mm)",
               "Loss (mm)", "Date", "Inspector"])
    for c in ws[1]:
        c.font = Font(bold=True)
    rows = [
        (TANK, "Course-1 N", NOMINAL, MEASURED, 0.8, "12-Sep-2026", "R. Nair"),
        (TANK, "Course-1 S", NOMINAL, 11.4, 0.6, "12-Sep-2026", "R. Nair"),
        (TANK, "Course-2 N", NOMINAL, 11.8, 0.2, "12-Sep-2026", "R. Nair"),
        (TANK, "Annular ring", 8.0, 7.6, 0.4, "12-Sep-2026", "R. Nair"),
        ("P-4110A", "Casing", 14.0, 13.7, 0.3, "02-Aug-2025", "M. Iyer"),
    ]
    for r in rows:
        ws.append(r)
    ws.column_dimensions["B"].width = 16

    ws2 = wb.create_sheet("Corrosion Rate")
    ws2.append(["Tag", "Interval (months)", "Loss (mm)", "Rate (mm/yr)", "Remaining life (yr)"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    ws2.append([TANK, 24, 0.8, 0.40, 2.0])
    ws2.append(["P-4110A", 13, 0.3, 0.28, 12.5])
    wb.save(p)


def pptx_review(p: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches, Pt

    pr = Presentation()
    s = pr.slides.add_slide(pr.slide_layouts[0])
    s.shapes.title.text = "Pre-Shutdown Review — CDU-II"
    s.placeholders[1].text = "Inspection Dept · 13-Sep-2026"

    s = pr.slides.add_slide(pr.slide_layouts[1])
    s.shapes.title.text = f"{TANK} — Open Items"
    tf = s.placeholders[1].text_frame
    tf.text = f"Shell thickness {MEASURED} mm vs nominal {NOMINAL} mm"
    for line in [
        f"{PSV} set at {PSV_SET} barg — SOP-114 requires {PSV_SPEC} barg",
        "Quarterly UT monitoring mandated until 31-Mar-2027",
        "MOC-2026-0231 conditional approval in force",
    ]:
        para = tf.add_paragraph(); para.text = line; para.level = 1

    s = pr.slides.add_slide(pr.slide_layouts[1])
    s.shapes.title.text = "Shutdown Scope"
    tf = s.placeholders[1].text_frame
    tf.text = "Bench test and reset all relief valves in CDU-II"
    for line in ["Weld overlay at course-1 if thickness < 10.8 mm",
                 "Replace nozzle N-3 reinforcement pad",
                 "Estimated duration: 9 days"]:
        para = tf.add_paragraph(); para.text = line; para.level = 1
    pr.save(p)


# ---------------------------------------------------------------------------
def _canvas(w: int, h: int, bg=(255, 253, 248)):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in ([
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ] if bold else [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _scanify(img, angle=0.4, noise=7):
    """Slight tilt plus grain, so OCR gets something like a real scan."""
    import random
    from PIL import Image
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(252, 250, 245),
                     expand=False)
    px = img.load()
    w, h = img.size
    random.seed(7)
    for _ in range((w * h) // 90):
        x, y = random.randrange(w), random.randrange(h)
        d = random.randint(-noise * 4, 0)
        r, g, b = px[x, y]
        px[x, y] = (max(0, r + d), max(0, g + d), max(0, b + d))
    return img


def png_scan_form(p: Path) -> None:
    """A scanned inspection form - exercises the OCR path."""
    img, dr = _canvas(1240, 1600)
    f_h, f_b, f_s = _font(34, True), _font(22), _font(18)

    dr.text((60, 50), "MRPL — EQUIPMENT INSPECTION REPORT", font=f_h, fill=(20, 20, 20))
    dr.line((60, 96, 1180, 96), fill=(60, 60, 60), width=2)
    dr.text((60, 118), f"Report No: {REPORT_NO}", font=f_b, fill=(30, 30, 30))
    dr.text((60, 150), f"Unit: {UNIT}", font=f_b, fill=(30, 30, 30))
    dr.text((700, 118), "Date: 12-Sep-2026", font=f_b, fill=(30, 30, 30))

    y = 210
    dr.text((60, y), "EQUIPMENT", font=f_b, fill=(0, 0, 0))
    y += 40
    cols = [60, 300, 700, 980]
    for i, hh in enumerate(["Tag No", "Description", "Design Pressure", "Status"]):
        dr.text((cols[i], y), hh, font=f_s, fill=(0, 0, 0))
    y += 10
    dr.line((60, y + 18, 1180, y + 18), fill=(120, 120, 120), width=1)
    y += 34
    for row in [(TANK, "Crude Storage Tank", "Atmospheric", "IN SERVICE"),
                (PSV, "Safety Relief Valve", f"{PSV_SET} barg", "DEVIATION"),
                ("P-4110A", "Crude Transfer Pump", "18.0 barg", "IN SERVICE")]:
        for i, v in enumerate(row):
            dr.text((cols[i], y), v, font=f_s, fill=(25, 25, 25))
        y += 36

    y += 40
    dr.text((60, y), "OBSERVATIONS", font=f_b, fill=(0, 0, 0)); y += 42
    for line in [
        f"1. Shell plate thickness course-1 = {MEASURED} mm (nominal {NOMINAL} mm).",
        f"2. {PSV} set pressure {PSV_SET} barg vs SOP-114 spec {PSV_SPEC} barg.",
        "3. Minor external corrosion near nozzle N-3, no through-wall loss.",
        "4. Foundation grouting intact, no settlement recorded.",
    ]:
        dr.text((70, y), line, font=f_s, fill=(25, 25, 25)); y += 34

    y += 40
    dr.text((60, y), "RECOMMENDATION", font=f_b, fill=(0, 0, 0)); y += 42
    for line in [f"Re-rate {TANK} per API 653 or restore thickness at shutdown.",
                 f"Bench test {PSV} and reset to {PSV_SPEC} barg before restart."]:
        dr.text((70, y), line, font=f_s, fill=(25, 25, 25)); y += 34

    y += 60
    dr.text((70, y), "Inspected by: R. Nair (API 510 #48112)", font=f_s, fill=(25, 25, 25))
    dr.line((70, y + 40, 420, y + 40), fill=(90, 90, 90), width=1)
    _scanify(img).save(p, quality=88)


def pdf_scanned(p: Path) -> None:
    """Image-only PDF - no embedded text, so OCR is mandatory."""
    from PIL import Image
    tmp = p.with_suffix(".tmp.png")
    png_scan_form(tmp)
    Image.open(tmp).convert("RGB").save(p, "PDF", resolution=150)
    tmp.unlink(missing_ok=True)


def png_nameplate(p: Path) -> None:
    """Photo of an equipment nameplate - small dense text, for the vision lane."""
    img, dr = _canvas(900, 620, bg=(196, 200, 198))
    dr.rectangle((40, 40, 860, 580), fill=(172, 177, 175), outline=(90, 95, 93), width=4)
    f_h, f_b = _font(40, True), _font(26)
    dr.text((80, 80), "MANUFACTURER PLATE", font=f_h, fill=(25, 28, 27))
    dr.line((80, 132, 820, 132), fill=(80, 85, 83), width=2)
    rows = [("TAG NO", TANK), ("SERVICE", "CRUDE STORAGE"),
            ("DESIGN PRESS", "ATMOSPHERIC"), ("DESIGN TEMP", "65 degC"),
            ("SHELL THK", f"{NOMINAL} mm NOM"), ("YEAR BUILT", "2011"),
            ("MFR SERIAL", "BHE-2011-5589")]
    y = 170
    for k, v in rows:
        dr.text((80, y), k, font=f_b, fill=(45, 50, 48))
        dr.text((420, y), v, font=f_b, fill=(20, 23, 22))
        y += 52
    _scanify(img, angle=-0.8, noise=10).save(p, quality=82)


def png_pid(p: Path) -> None:
    """
    A simple P&ID-style schematic. Not a real CAD drawing, but the pid lane,
    the vision lane and the future analyze_pid tool all need something.
    """
    img, dr = _canvas(1500, 1000, bg=(255, 255, 255))
    f_t, f_s = _font(26, True), _font(18)
    ink = (20, 20, 20)

    dr.rectangle((30, 30, 1470, 970), outline=ink, width=3)
    dr.text((50, 45), f"P&ID — {UNIT} CRUDE STORAGE & TRANSFER", font=f_t, fill=ink)
    dr.text((1050, 48), "DWG: PID-CDU2-004  REV: 3", font=f_s, fill=ink)
    dr.line((30, 90, 1470, 90), fill=ink, width=2)

    # tank
    dr.rectangle((120, 300, 420, 640), outline=ink, width=3)
    dr.arc((120, 260, 420, 340), 180, 360, fill=ink, width=3)
    dr.text((210, 450), TANK, font=f_t, fill=ink)
    dr.text((165, 490), "CRUDE STORAGE", font=f_s, fill=ink)

    # PSV on top
    dr.line((270, 300, 270, 210), fill=ink, width=3)
    dr.polygon([(270, 210), (240, 165), (300, 165)], outline=ink, width=3)
    dr.ellipse((235, 105, 305, 165), outline=ink, width=3)
    dr.text((248, 125), PSV, font=f_s, fill=ink)

    # line to pump
    dr.line((420, 560, 700, 560), fill=ink, width=3)
    # gate valve symbol
    dr.polygon([(540, 545), (540, 575), (570, 560)], outline=ink, width=3)
    dr.polygon([(600, 545), (600, 575), (570, 560)], outline=ink, width=3)
    dr.text((535, 590), "HV-4021", font=f_s, fill=ink)

    # pump
    dr.ellipse((700, 505, 810, 615), outline=ink, width=3)
    dr.polygon([(755, 505), (810, 560), (755, 615)], outline=ink, width=3)
    dr.text((715, 630), "P-4110A", font=f_s, fill=ink)

    # discharge + control valve
    dr.line((810, 560, 1100, 560), fill=ink, width=3)
    dr.polygon([(980, 545), (980, 575), (1010, 560)], outline=ink, width=3)
    dr.polygon([(1040, 545), (1040, 575), (1010, 560)], outline=ink, width=3)
    dr.line((1010, 545, 1010, 500), fill=ink, width=2)
    dr.ellipse((975, 440, 1045, 500), outline=ink, width=3)
    dr.text((990, 460), "FV", font=f_s, fill=ink)
    dr.text((985, 590), "FV-4033", font=f_s, fill=ink)

    dr.line((1100, 560, 1380, 560), fill=ink, width=3)
    dr.text((1180, 520), "TO CDU-II", font=f_s, fill=ink)
    dr.polygon([(1380, 550), (1380, 570), (1410, 560)], fill=ink)

    # level instrument
    dr.ellipse((430, 320, 500, 380), outline=ink, width=3)
    dr.text((447, 340), "LT", font=f_s, fill=ink)
    dr.line((420, 350, 430, 350), fill=ink, width=2)
    dr.text((432, 390), "LT-4102", font=f_s, fill=ink)

    img.save(p)


# ---------------------------------------------------------------------------
def md_bulletin(p: Path) -> None:
    p.write_text(f"""# Maintenance Bulletin MB-2026-17

**Subject:** Interim monitoring for {TANK}
**Issued:** 13-Sep-2026 · Inspection Department

## Background

Routine UT survey on 12-Sep-2026 recorded shell plate thickness of
**{MEASURED} mm** at course-1 against a nominal of {NOMINAL} mm. The computed
minimum required thickness per API 653 is 10.4 mm.

## Required Actions

| # | Action | Owner | Due |
|---|--------|-------|-----|
| 1 | Quarterly UT at course-1 (4 points) | Inspection | 31-Dec-2026 |
| 2 | Bench test and reset {PSV} to {PSV_SPEC} barg | Maintenance | 27-Sep-2026 |
| 3 | Visual check of nozzle N-3 each shift | Operations | Ongoing |

## Notes

- Operating temperature must not exceed 65 degC while MOC-2026-0231 is in force.
- Any reading below 10.8 mm shall be escalated to the Head of Inspection
  the same day.
""")


def html_circular(p: Path) -> None:
    p.write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Safety Circular SC-2026-09</title></head><body>
<h1>Safety Circular SC-2026-09</h1>
<p><strong>To:</strong> All {UNIT} shift engineers<br>
<strong>Date:</strong> 13-Sep-2026</p>

<h2>Relief valve settings</h2>
<p>A review of relief devices in {UNIT} found {PSV} set at {PSV_SET} barg,
whereas SOP-114 clause 4 requires {PSV_SPEC} barg for atmospheric storage
service. This is recorded as non-conformance NCR-2026-0088.</p>

<h2>Immediate instruction</h2>
<ol>
  <li>Do not raise {TANK} operating level above 82% until the valve is reset.</li>
  <li>Log tank level hourly in the shift register.</li>
  <li>Report any relief device lift to the Control Room immediately.</li>
</ol>
<p>Issued by: A. Deshpande, Head &mdash; Inspection</p>
</body></html>""")


def txt_log(p: Path) -> None:
    p.write_text(f"""OPERATOR SHIFT LOG — {UNIT}
Date: 12-Sep-2026    Shift: B (14:00-22:00)    Operator: V. Salunke

14:20  {TANK} level 71%, temp 58 degC. Normal.
15:05  Inspection team on site for UT survey at course-1.
16:40  UT survey complete. Team reported {MEASURED} mm at course-1,
       below nominal {NOMINAL} mm. Informed shift in-charge.
17:15  {PSV} inspected. Set pressure observed {PSV_SET} barg.
       SOP-114 requires {PSV_SPEC} barg. Raised NCR-2026-0088.
18:30  P-4110A vibration 3.1 mm/s, within limit.
20:00  {TANK} level 76%, temp 61 degC. Holding.
21:45  Handover to shift C. Open item: PSV reset pending.
""")


def csv_readings(p: Path) -> None:
    p.write_text("""tag,location,nominal_mm,measured_mm,loss_mm,date,inspector
TK-4102,Course-1 N,12.0,11.2,0.8,2026-09-12,R. Nair
TK-4102,Course-1 S,12.0,11.4,0.6,2026-09-12,R. Nair
TK-4102,Course-2 N,12.0,11.8,0.2,2026-09-12,R. Nair
TK-4102,Annular ring,8.0,7.6,0.4,2026-09-12,R. Nair
P-4110A,Casing,14.0,13.7,0.3,2025-08-02,M. Iyer
""")


# ---------------------------------------------------------------------------
BUILDERS = [
    ("inspection_report_TK4102.docx", docx_inspection, "native text + tables"),
    ("approval_note.docx",            docx_approval,   "native text"),
    ("SOP-114_relief_devices.docx",   docx_sop,        "the spec being checked against"),
    ("ut_thickness_log.xlsx",         xlsx_thickness,  "spreadsheet, 2 sheets"),
    ("preshutdown_review.pptx",       pptx_review,     "slides"),
    ("scanned_page.png",              png_scan_form,   "scan -> OCR"),
    ("scanned_report.pdf",            pdf_scanned,     "image-only PDF -> OCR"),
    ("nameplate_TK4102.jpg",          png_nameplate,   "photo -> OCR / vision"),
    ("PID-CDU2-004.png",              png_pid,         "P&ID schematic"),
    ("maintenance_bulletin.md",       md_bulletin,     "markdown + table"),
    ("safety_circular.html",          html_circular,   "html"),
    ("operator_shift_log.txt",        txt_log,         "plain text"),
    ("ut_readings.csv",               csv_readings,    "csv"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="remove existing files first")
    args = ap.parse_args()

    CORPUS.mkdir(parents=True, exist_ok=True)
    if args.clean:
        for f in CORPUS.iterdir():
            if f.is_file() and not f.name.startswith("."):
                f.unlink()
        print("corpus cleared")

    print(f"{'file':34} {'kb':>6}  covers")
    print("-" * 72)
    for name, fn, why in BUILDERS:
        path = CORPUS / name
        try:
            fn(path)
            print(f"{name:34} {path.stat().st_size/1024:6.1f}  {why}")
        except Exception as exc:
            print(f"{name:34} {'--':>6}  FAIL: {type(exc).__name__}: {exc}")
    print(f"\n{CORPUS}")
    print("now build the index:  python -m ingest.pipeline build --rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
