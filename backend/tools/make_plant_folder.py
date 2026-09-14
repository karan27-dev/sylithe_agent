"""
Build one realistically messy folder to point the workbench at.

The generated test sets are each small and clean, which is the point when
scoring. A real manuals folder is not like that: one unit, many documents
written by different people over several years, one drawing, a scan nobody
retyped, a spreadsheet holding the history, a revision that was superseded and
never deleted, and a deviation that quietly makes a failing number acceptable.

Everything here is internally consistent, so the hard questions have real
answers:

  * V-201 fails its relief spec outright.
  * V-202 also looks like it fails - until DA-2026-207 is read.
  * SOP-220 exists twice, Rev 2 and Rev 3, and Rev 2 is superseded. Using the
    wrong one inverts the answer on PSV-203.
  * E-204 was never surveyed. Its thickness must be refused, not estimated.
  * The corrosion rates only come out right if the history sheet is used
    rather than the single latest interval.

    python -m tools.make_plant_folder [--dest PATH]
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_DEST = Path.home() / "Desktop" / "Plant Manuals"

# One source of truth. Every document below is generated from these numbers.
EQUIP = [
    # tag,      description,          nominal, measured, min_req, months
    ("V-201", "Crude Surge Drum",        18.0, 15.6, 14.2, 42),
    ("V-202", "Reflux Accumulator",      16.0, 14.9, 13.8, 36),
    ("E-203", "Feed/Effluent Exchanger", 14.0, 12.5, 11.6, 48),
    ("E-204", "Trim Cooler",             14.0, None, 11.6, None),
    ("P-205A", "Charge Pump A",          20.0, 18.8, 17.0, 30),
    ("P-205B", "Charge Pump B",          20.0, 19.6, 17.0, 30),
    ("T-206", "Product Storage Tank",    22.0, 19.4, 17.5, 54),
]

# tag, as-found barg, required barg, note
RELIEF = [
    ("PSV-201", 23.5, 25.0, "fails"),
    ("PSV-202", 18.2, 20.0, "covered by deviation DA-2026-207"),
    ("PSV-203", 31.0, 32.0, "Rev 3 requires 32.0; superseded Rev 2 said 30.0"),
    ("PSV-206", 12.0, 12.0, "compliant"),
]


def rate(nominal, measured, months):
    if measured is None or not months:
        return None
    return (nominal - measured) / (months / 12)


def life(measured, min_req, r):
    if measured is None or not r:
        return None
    return (measured - min_req) / r


# ---------------------------------------------------------------------------

def _doc(path: Path, title: str, meta: str, sections):
    from docx import Document
    d = Document()
    d.add_heading(title, 0)
    d.add_paragraph(meta)
    for head, body, bullets, table in sections:
        if head:
            d.add_heading(head, 1)
        if body:
            d.add_paragraph(body)
        for b in bullets or []:
            d.add_paragraph(b, style="List Bullet")
        if table:
            cols, rows = table
            t = d.add_table(rows=1, cols=len(cols))
            t.style = "Table Grid"
            for i, c in enumerate(cols):
                t.rows[0].cells[i].text = str(c)
            for r in rows:
                cells = t.add_row().cells
                for i, v in enumerate(r):
                    cells[i].text = "" if v is None else str(v)
    d.save(path)


def build(dest: Path) -> dict:
    import shutil
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    made = []

    # --- inspection report -------------------------------------------------
    rows = [(t, d, n, m if m is not None else "not surveyed",
             mo if mo else "-") for t, d, n, m, mr, mo in EQUIP]
    obs = []
    for t, d, n, m, mr, mo in EQUIP:
        if m is None:
            obs.append(f"{t} was not surveyed during this campaign. No "
                       f"thickness reading is available.")
        else:
            obs.append(f"{t} measured {m} mm against nominal {n} mm over "
                       f"{mo} months. Minimum required {mr} mm.")
    for tag, found, req, note in RELIEF:
        obs.append(f"{tag} as-found set pressure {found} barg.")
    _doc(dest / "INS-2026-0412 Inspection Report Unit 200.docx",
         "MRPL - EQUIPMENT INSPECTION REPORT",
         "Report No: MRPL/INSP/2026/0412    Unit: 200    Date: 08-Sep-2026    "
         "Inspector: R. Nair (API 510 #48112)",
         [("1. Equipment Particulars", None, None,
           (["Tag", "Description", "Nominal (mm)", "Measured (mm)", "Interval (months)"], rows)),
          ("2. Observations", None, obs, None),
          ("3. Recommendation",
           "Refer to SOP-220 Rev 3 for relief device settings and to the "
           "thickness history sheet for long-term corrosion rates.", None, None)])
    made.append("inspection report")

    # --- SOP, two revisions, one superseded --------------------------------
    _doc(dest / "SOP-220 Rev 2 Relief Devices (SUPERSEDED).docx",
         "SOP-220 Rev 2 - Relief Device Settings",
         "Status: SUPERSEDED on 01-Jul-2026 by Rev 3. Retained for record only.",
         [("4. Set pressure",
           "Relief valves on Unit 200 columns shall be set at 30.0 barg.",
           None, None)])
    _doc(dest / "SOP-220 Rev 3 Relief Devices (CURRENT).docx",
         "SOP-220 Rev 3 - Relief Device Settings",
         "Status: CURRENT. Effective 01-Jul-2026. Supersedes Rev 2.",
         [("4. Set pressure",
           "Relief valves shall be set as follows: surge drums 25.0 barg, "
           "accumulators 20.0 barg, columns 32.0 barg, storage tanks 12.0 barg.",
           None, None),
          ("7. Deviations",
           "A set pressure below the figure above is a non-conformance unless "
           "a deviation approval is in force and has not expired.", None, None)])
    made.append("SOP Rev 2 + Rev 3")

    # --- deviation approval ------------------------------------------------
    _doc(dest / "DA-2026-207 Deviation Approval PSV-202.docx",
         "DEVIATION APPROVAL DA-2026-207",
         "Ref: PSV-202    Raised: 14-Jul-2026    Approved: A. Deshpande, "
         "Head - Inspection",
         [("Deviation",
           "PSV-202 is approved to remain at 18.2 barg against the SOP-220 "
           "Rev 3 requirement of 20.0 barg, until the next turnaround on "
           "31-Mar-2027. This deviation is documented and approved, and the "
           "setting is therefore acceptable until that date.", None, None),
          ("Conditions", None,
           ["Accumulator level not to exceed 70 percent.",
            "Bench test at the 31-Mar-2027 turnaround without exception."], None)])
    made.append("deviation approval")

    # --- thickness history -------------------------------------------------
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active; ws.title = "History"
    ws.append(["Tag", "Survey year", "Measured mm"])
    for c in ws[1]:
        c.font = Font(bold=True)
    hist = {
        "V-201": [(2019, 18.0), (2022, 16.9), (2026, 15.6)],
        "V-202": [(2020, 16.0), (2023, 15.4), (2026, 14.9)],
        "E-203": [(2018, 14.0), (2022, 13.2), (2026, 12.5)],
        "P-205A": [(2021, 20.0), (2026, 18.8)],
        "P-205B": [(2021, 20.0), (2026, 19.6)],
        "T-206": [(2017, 22.0), (2022, 20.6), (2026, 19.4)],
    }
    for tag, pts in hist.items():
        for y, v in pts:
            ws.append([tag, y, v])
    ws2 = wb.create_sheet("Limits")
    ws2.append(["Tag", "Nominal mm", "Minimum required mm"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for t, d, n, m, mr, mo in EQUIP:
        ws2.append([t, n, mr])
    wb.save(dest / "UT Thickness History Unit 200.xlsx")
    made.append("thickness history (2 sheets)")

    # --- line list ---------------------------------------------------------
    (dest / "Line List Unit 200.csv").write_text(
        "line,from,to,size_in,service,isolation_valve\n"
        "200-P-001,V-201,P-205A,8,crude,HV-201\n"
        "200-P-002,V-201,P-205B,8,crude,HV-202\n"
        "200-P-003,P-205A,E-203,8,crude,HV-203\n"
        "200-P-004,P-205B,E-203,8,crude,HV-204\n"
        "200-P-005,E-203,T-206,10,product,HV-205\n"
        "200-P-006,V-202,E-204,6,reflux,HV-206\n")
    made.append("line list")

    # --- safety circular + shift log ---------------------------------------
    (dest / "Safety Circular SC-2026-11.html").write_text(
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Safety Circular SC-2026-11</title></head><body>"
        "<h1>Safety Circular SC-2026-11</h1>"
        "<p><strong>To:</strong> Unit 200 shift engineers<br>"
        "<strong>Date:</strong> 10-Sep-2026</p>"
        "<h2>Relief settings</h2>"
        "<p>PSV-201 was found at 23.5 barg against the SOP-220 Rev 3 "
        "requirement of 25.0 barg. This is recorded as non-conformance "
        "NCR-2026-0141 and must be corrected before the next start-up.</p>"
        "<h2>Instruction</h2><ol>"
        "<li>Do not raise V-201 level above 80 percent until PSV-201 is reset.</li>"
        "<li>Log drum level hourly.</li></ol></body></html>")
    (dest / "Operator Shift Log 08-Sep-2026.txt").write_text(
        "OPERATOR SHIFT LOG - UNIT 200\n"
        "Date: 08-Sep-2026    Shift: B    Operator: V. Salunke\n\n"
        "14:10  V-201 level 74%, temp 61 degC. Normal.\n"
        "15:40  Inspection team on site for UT survey.\n"
        "16:55  Survey complete. V-201 course-1 reads 15.6 mm.\n"
        "17:20  PSV-201 observed at 23.5 barg. SOP-220 Rev 3 requires 25.0.\n"
        "       Raised NCR-2026-0141.\n"
        "18:05  E-204 could not be surveyed - line still in service.\n"
        "19:30  P-205A vibration 3.4 mm/s, within limit.\n"
        "21:50  Handover to shift C. Open: PSV-201 reset, E-204 survey.\n")
    made.append("safety circular + shift log")

    # --- maintenance bulletin (markdown) -----------------------------------
    (dest / "Maintenance Bulletin MB-2026-22.md").write_text(
        "# Maintenance Bulletin MB-2026-22\n\n"
        "**Subject:** Unit 200 open items  \n**Issued:** 11-Sep-2026\n\n"
        "| # | Action | Owner | Due |\n|---|--------|-------|-----|\n"
        "| 1 | Reset PSV-201 to 25.0 barg | Maintenance | 30-Sep-2026 |\n"
        "| 2 | Survey E-204 - missed this campaign | Inspection | 08-Mar-2027 |\n"
        "| 3 | Quarterly UT on V-201 course-1 | Inspection | ongoing |\n\n"
        "PSV-202 remains at 18.2 barg under DA-2026-207 and requires no action "
        "before the 31-Mar-2027 turnaround.\n")
    made.append("maintenance bulletin")

    # --- P&ID + a scan -----------------------------------------------------
    _drawing(dest / "PID-U200-001 Unit 200 Overview.png")
    _scan(dest / "Scanned Inspection Sheet Unit 200.png")
    made.append("P&ID + scanned sheet")

    return {"dest": dest, "made": made,
            "files": sorted(f.name for f in dest.iterdir() if f.is_file())}


# ---------------------------------------------------------------------------

def _font(sz, bold=False):
    from PIL import ImageFont
    names = ["Arial Bold.ttf"] if bold else ["Arial.ttf"]
    for n in names + ["Helvetica.ttc"]:
        for d in ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/"):
            try:
                return ImageFont.truetype(d + n, sz)
            except OSError:
                continue
    return ImageFont.load_default()


def _drawing(path: Path):
    from PIL import Image, ImageDraw
    W, H = 1700, 1080
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    ink = (20, 20, 20)
    d.rectangle((25, 25, W - 25, H - 25), outline=ink, width=3)
    d.text((45, 40), "P&ID - UNIT 200 CRUDE PREHEAT", font=_font(26, True), fill=ink)
    d.text((W - 480, 46), "DWG: PID-U200-001  REV: 4", font=_font(18), fill=ink)
    d.line((25, 88, W - 25, 88), fill=ink, width=2)

    def tank(x1, y1, x2, y2, tag, sub=""):
        d.rectangle((x1, y1, x2, y2), outline=ink, width=3)
        d.arc((x1, y1 - 38, x2, y1 + 38), 180, 360, fill=ink, width=3)
        d.text((x1 + 30, (y1 + y2) // 2), tag, font=_font(22, True), fill=ink)
        if sub:
            d.text((x1 + 22, (y1 + y2) // 2 + 30), sub, font=_font(14), fill=ink)

    def valve(cx, cy, tag, w=28, h=22):
        d.polygon([(cx - w, cy - h), (cx - w, cy + h), (cx, cy)], outline=ink, width=4)
        d.polygon([(cx + w, cy - h), (cx + w, cy + h), (cx, cy)], outline=ink, width=4)
        d.line((cx - w - 60, cy, cx - w, cy), fill=ink, width=3)
        d.line((cx + w, cy, cx + w + 60, cy), fill=ink, width=3)
        d.text((cx - 40, cy + 30), tag, font=_font(15), fill=ink)

    def pump(cx, cy, tag, r=44):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink, width=3)
        d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r)], outline=ink, width=3)
        d.text((cx - 38, cy + r + 10), tag, font=_font(15), fill=ink)

    def psv(cx, cy, tag):
        d.line((cx, cy, cx, cy - 70), fill=ink, width=3)
        d.polygon([(cx, cy - 70), (cx - 25, cy - 106), (cx + 25, cy - 106)],
                  outline=ink, width=3)
        d.ellipse((cx - 30, cy - 154, cx + 30, cy - 106), outline=ink, width=3)
        d.text((cx - 42, cy - 140), tag, font=_font(14), fill=ink)

    def instr(cx, cy, code, tag):
        d.ellipse((cx - 28, cy - 28, cx + 28, cy + 28), outline=ink, width=3)
        d.line((cx - 28, cy, cx + 28, cy), fill=ink, width=2)
        d.text((cx - 14, cy - 25), code, font=_font(15), fill=ink)
        d.text((cx - 40, cy + 34), tag, font=_font(13), fill=ink)

    tank(90, 380, 280, 700, "V-201", "SURGE DRUM")
    psv(185, 380, "PSV-201")
    instr(330, 420, "LT", "LT-201")
    d.line((280, 560, 340, 560), fill=ink, width=3)
    d.line((340, 440, 340, 680), fill=ink, width=3)
    d.line((340, 440, 400, 440), fill=ink, width=3)
    d.line((340, 680, 400, 680), fill=ink, width=3)
    valve(470, 440, "HV-201")
    valve(470, 680, "HV-202")
    pump(620, 440, "P-205A")
    pump(620, 680, "P-205B")
    d.line((664, 440, 730, 440), fill=ink, width=3)
    d.line((664, 680, 730, 680), fill=ink, width=3)
    valve(800, 440, "HV-203")
    valve(800, 680, "HV-204")
    d.line((860, 440, 930, 440), fill=ink, width=3)
    d.line((860, 680, 930, 680), fill=ink, width=3)
    d.line((930, 440, 930, 680), fill=ink, width=3)
    d.rectangle((930, 480, 1180, 640), outline=ink, width=3)
    d.line((930, 560, 1180, 560), fill=ink, width=2)
    d.text((1000, 505), "E-203", font=_font(22, True), fill=ink)
    instr(1055, 400, "TI", "TI-203")
    d.line((1180, 560, 1250, 560), fill=ink, width=3)
    valve(1320, 560, "HV-205")
    d.line((1380, 560, 1440, 560), fill=ink, width=3)
    tank(1440, 430, 1630, 700, "T-206", "PRODUCT")
    psv(1535, 430, "PSV-206")
    tank(90, 120, 270, 300, "V-202", "REFLUX")
    psv(180, 120, "PSV-202")
    d.line((270, 210, 340, 210), fill=ink, width=3)
    valve(410, 210, "HV-206")
    d.line((470, 210, 560, 210), fill=ink, width=3)
    d.rectangle((560, 150, 760, 280), outline=ink, width=3)
    d.text((620, 200), "E-204", font=_font(20, True), fill=ink)
    img.save(path)


def _scan(path: Path):
    from PIL import Image, ImageDraw
    import random
    W, H = 1240, 1600
    img = Image.new("RGB", (W, H), (253, 251, 246))
    d = ImageDraw.Draw(img)
    fh, fb, fs = _font(32, True), _font(21), _font(18)
    d.text((60, 52), "UNIT 200 - FIELD INSPECTION SHEET", font=fh, fill=(20, 20, 20))
    d.line((60, 98, 1180, 98), fill=(60, 60, 60), width=2)
    d.text((60, 120), "Report No: MRPL/INSP/2026/0412", font=fb, fill=(30, 30, 30))
    d.text((700, 120), "Date: 08-Sep-2026", font=fb, fill=(30, 30, 30))
    y = 200
    d.text((60, y), "AS-FOUND RELIEF SETTINGS", font=fb, fill=(0, 0, 0)); y += 44
    for tag, found, req, note in RELIEF:
        d.text((80, y), f"{tag}   as-found {found} barg   required {req} barg",
               font=fs, fill=(25, 25, 25))
        y += 34
    y += 34
    d.text((60, y), "THICKNESS READINGS", font=fb, fill=(0, 0, 0)); y += 44
    for t, desc, n, m, mr, mo in EQUIP:
        txt = (f"{t}   nominal {n} mm   measured "
               + (f"{m} mm over {mo} months" if m is not None else "NOT SURVEYED"))
        d.text((80, y), txt, font=fs, fill=(25, 25, 25))
        y += 34
    y += 40
    d.text((60, y), "NOTES", font=fb, fill=(0, 0, 0)); y += 42
    for line in ["E-204 could not be isolated; survey deferred to Mar-2027.",
                 "PSV-202 operating under deviation DA-2026-207.",
                 "SOP-220 Rev 2 is superseded - use Rev 3."]:
        d.text((80, y), line, font=fs, fill=(25, 25, 25)); y += 34
    d.text((80, y + 50), "Inspected by: R. Nair (API 510 #48112)", font=fs,
           fill=(25, 25, 25))
    img = img.rotate(0.45, resample=Image.BICUBIC, fillcolor=(253, 251, 246))
    px = img.load()
    random.seed(11)
    for _ in range((W * H) // 110):
        x, yy = random.randrange(W), random.randrange(H)
        r, g, b = px[x, yy]
        dd = random.randint(-26, 0)
        px[x, yy] = (max(0, r + dd), max(0, g + dd), max(0, b + dd))
    img.save(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    a = ap.parse_args()
    r = build(Path(a.dest).expanduser())
    print(f"{r['dest']}\n")
    for f in r["files"]:
        print("  " + f)
    print(f"\n{len(r['files'])} files")
    print("\nHard questions this folder can answer:")
    for q in [
        "Which relief valves are non-compliant?  (PSV-201 only - PSV-202 is "
        "covered by DA-2026-207 and PSV-203 passes under Rev 3, not Rev 2)",
        "What is the long-term corrosion rate of V-201?  (2.4 mm over 7 years "
        "= 0.34 mm/yr from the history sheet)",
        "What is the measured thickness of E-204?  (it was never surveyed - "
        "must be refused)",
        "What must be closed to isolate V-201?  (HV-201 and HV-202 - two paths)",
        "Which charge pump is corroding faster?  (P-205A, 0.48 vs 0.16 mm/yr)",
    ]:
        print("  - " + q)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
