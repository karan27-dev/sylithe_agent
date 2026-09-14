"""
Five self-contained test sets: a drawing, its documents, and known answers.

Why generate rather than collect. A benchmark is only worth the truth behind
it, and the answers here are computed from the numbers before anything is
written - so "0.40 mm/yr" is not what the system said last week, it is what the
arithmetic gives. That distinction already bit this project once: a P&ID
expectation was written from the system's own output, a spurious graph edge was
inventing a second path, and when the graph was fixed the benchmark called the
correct answer wrong.

  set-1  easy      one tank, one valve, one relief device
  set-2  easy      pump and filter in series
  set-3  medium    two parallel pumps, shared header
  set-4  medium    exchanger train, two isolation points, cross-document spec
  set-5  complex   three-branch manifold, two relief devices, conflicting specs

Each set gets its own folder so it can be indexed alone - the interesting
questions are the ones that need two documents, and mixing all five together
would let an answer come from the wrong set.

    python -m tools.make_datasets
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "datasets"


def _f(sz, bold=False):
    names = (["Arial Bold.ttf", "Helvetica.ttc"] if bold
             else ["Arial.ttf", "Helvetica.ttc"])
    for n in names:
        for d in ("/System/Library/Fonts/Supplemental/", "/System/Library/Fonts/"):
            try:
                return ImageFont.truetype(d + n, sz)
            except OSError:
                continue
    return ImageFont.load_default()


@dataclass
class Q:
    ask: str
    expect: list[str]                      # substrings that must appear
    reject: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class DataSet:
    id: str
    level: str
    title: str
    questions: list[Q] = field(default_factory=list)


# ---------------------------------------------------------------------------
# drawing helpers
# ---------------------------------------------------------------------------

def _canvas(w=1500, h=1000, title="", dwg=""):
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((25, 25, w - 25, h - 25), outline=(20, 20, 20), width=3)
    d.text((45, 40), title, font=_f(26, True), fill=(20, 20, 20))
    d.text((w - 470, 44), dwg, font=_f(18), fill=(20, 20, 20))
    d.line((25, 85, w - 25, 85), fill=(20, 20, 20), width=2)
    return img, d


def _valve(d, cx, cy, tag, w=32, h=25, stub=70):
    ink = (20, 20, 20)
    d.polygon([(cx - w, cy - h), (cx - w, cy + h), (cx, cy)], outline=ink, width=4)
    d.polygon([(cx + w, cy - h), (cx + w, cy + h), (cx, cy)], outline=ink, width=4)
    d.line((cx - w - stub, cy, cx - w, cy), fill=ink, width=3)
    d.line((cx + w, cy, cx + w + stub, cy), fill=ink, width=3)
    d.text((cx - 44, cy + 34), tag, font=_f(17), fill=ink)


def _tank(d, x1, y1, x2, y2, tag, sub=""):
    ink = (20, 20, 20)
    d.rectangle((x1, y1, x2, y2), outline=ink, width=3)
    d.arc((x1, y1 - 40, x2, y1 + 40), 180, 360, fill=ink, width=3)
    d.text((x1 + 40, (y1 + y2) // 2), tag, font=_f(24, True), fill=ink)
    if sub:
        d.text((x1 + 30, (y1 + y2) // 2 + 34), sub, font=_f(16), fill=ink)


def _pump(d, cx, cy, tag, r=52):
    ink = (20, 20, 20)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink, width=3)
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r)], outline=ink, width=3)
    d.text((cx - 42, cy + r + 12), tag, font=_f(17), fill=ink)


def _psv(d, cx, cy, tag):
    ink = (20, 20, 20)
    d.line((cx, cy, cx, cy - 78), fill=ink, width=3)
    d.polygon([(cx, cy - 78), (cx - 28, cy - 118), (cx + 28, cy - 118)],
              outline=ink, width=3)
    d.ellipse((cx - 34, cy - 172, cx + 34, cy - 118), outline=ink, width=3)
    d.text((cx - 46, cy - 156), tag, font=_f(16), fill=ink)


def _instr(d, cx, cy, code, tag):
    ink = (20, 20, 20)
    d.ellipse((cx - 32, cy - 32, cx + 32, cy + 32), outline=ink, width=3)
    d.line((cx - 32, cy, cx + 32, cy), fill=ink, width=2)
    d.text((cx - 16, cy - 28), code, font=_f(17), fill=ink)
    d.text((cx - 44, cy + 38), tag, font=_f(15), fill=ink)


def _arrow(d, x1, y1, x2, y2, label=""):
    ink = (20, 20, 20)
    d.line((x1, y1, x2, y2), fill=ink, width=3)
    d.polygon([(x2, y2 - 9), (x2, y2 + 9), (x2 + 22, y2)], fill=ink)
    if label:
        d.text((x1 + 20, y1 - 30), label, font=_f(16), fill=ink)


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def _report(path: Path, unit, rows, obs, insp="R. Nair"):
    from docx import Document
    doc = Document()
    doc.add_heading("EQUIPMENT INSPECTION REPORT", 0)
    doc.add_paragraph(f"Unit: {unit}    Date: 12-Sep-2026    Inspector: {insp}")
    doc.add_heading("1. Equipment", 1)
    t = doc.add_table(rows=1, cols=4)
    t.style = "Table Grid"
    for i, h in enumerate(["Tag", "Description", "Nominal (mm)", "Measured (mm)"]):
        t.rows[0].cells[i].text = h
    for r in rows:
        c = t.add_row().cells
        for i, v in enumerate(r):
            c[i].text = str(v)
    doc.add_heading("2. Observations", 1)
    for o in obs:
        doc.add_paragraph(o, style="List Bullet")
    doc.save(path)


def _sop(path: Path, name, clauses):
    from docx import Document
    doc = Document()
    doc.add_heading(name, 0)
    doc.add_paragraph("Revision 6    Owner: Inspection Department")
    for head, body in clauses:
        doc.add_heading(head, 1)
        doc.add_paragraph(body)
    doc.save(path)


def _sheet(path: Path, header, rows, notes=()):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active; ws.title = "Readings"
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(list(r))
    if notes:
        ws.append([])
        for n in notes:
            ws.append([n])
    wb.save(path)


# ---------------------------------------------------------------------------
# the five sets
# ---------------------------------------------------------------------------

def set1(d: Path) -> DataSet:
    """Easy: one tank, one isolation valve, one relief device."""
    img, dr = _canvas(title="P&ID - V-101 STORAGE", dwg="DWG: SET1-001 REV 1")
    _tank(dr, 150, 330, 430, 660, "V-101", "STORAGE DRUM")
    _psv(dr, 290, 330, "PSV-101")
    _instr(dr, 500, 400, "LT", "LT-101")
    dr.line((430, 560, 500, 560), fill=(20, 20, 20), width=3)
    _valve(dr, 660, 560, "HV-101")
    dr.line((760, 560, 1100, 560), fill=(20, 20, 20), width=3)
    _arrow(dr, 1100, 560, 1330, 560, "TO UNIT 2")
    img.save(d / "PID-SET1.png")

    _report(d / "inspection_V-101.docx", "SET-1",
            [("V-101", "Storage Drum", 10.0, 9.4),
             ("PSV-101", "Relief Valve", "-", "-")],
            ["Shell thickness at course-1 measured 9.4 mm against nominal "
             "10.0 mm. Interval since last survey: 24 months.",
             "PSV-101 as-found set pressure 9.0 barg. SOP-201 requires 10.0 barg.",
             "Minimum required thickness per API 653 calculation: 8.6 mm."])
    _sop(d / "SOP-201.docx", "SOP-201 - Relief Device Settings",
         [("4. Set Pressure",
           "All relief valves on storage drums in this unit shall be set at "
           "10.0 barg. A lower as-found setting is a non-conformance.")])
    # loss 0.6 over 24 months -> 0.30 mm/yr ; life (9.4-8.6)/0.30 = 2.7 yr
    return DataSet("set-1", "easy", "One drum, one valve, one relief device", [
        Q("What is the shell thickness of V-101 and how does it compare to nominal?",
          ["9.4", "10.0"]),
        Q("Does the PSV-101 set pressure meet SOP-201?",
          ["9.0", "10.0"], ["meets", "compliant"],
          "9.0 against a required 10.0 is a non-conformance."),
        Q("What must be closed to isolate V-101 on this P&ID?",
          ["HV-101"], ["PSV-101"],
          "One valve on the path out. A relief device is never an isolation point."),
        Q("Calculate the corrosion rate for V-101.", ["0.30"]),
    ])


def set2(d: Path) -> DataSet:
    """Easy: pump and filter in series."""
    img, dr = _canvas(title="P&ID - P-201 TRANSFER", dwg="DWG: SET2-004 REV 2")
    _tank(dr, 130, 350, 360, 640, "T-201", "FEED TANK")
    dr.line((360, 540, 440, 540), fill=(20, 20, 20), width=3)
    _valve(dr, 520, 540, "HV-201")
    dr.line((620, 540, 700, 540), fill=(20, 20, 20), width=3)
    _pump(dr, 770, 540, "P-201")
    dr.line((822, 540, 940, 540), fill=(20, 20, 20), width=3)
    _valve(dr, 1030, 540, "HV-202")
    _instr(dr, 940, 380, "PI", "PI-201")
    _arrow(dr, 1130, 540, 1340, 540, "TO STORAGE")
    img.save(d / "PID-SET2.png")

    _report(d / "inspection_P-201.docx", "SET-2",
            [("T-201", "Feed Tank", 12.0, 11.5),
             ("P-201", "Transfer Pump", 14.0, 13.4)],
            ["T-201 shell measured 11.5 mm against nominal 12.0 mm over an "
             "18 month interval.",
             "P-201 casing measured 13.4 mm against nominal 14.0 mm over a "
             "36 month interval.",
             "PI-201 reads 4.2 barg at normal flow; design is 6.0 barg."])
    _sheet(d / "readings_SET2.xlsx",
           ["Tag", "Nominal mm", "Measured mm", "Interval months"],
           [("T-201", 12.0, 11.5, 18), ("P-201", 14.0, 13.4, 36)],
           ["Minimum required thickness: T-201 10.8 mm, P-201 12.5 mm"])
    # T-201: 0.5 / 1.5 yr = 0.333 ; P-201: 0.6 / 3 yr = 0.20
    return DataSet("set-2", "easy", "Pump and filter in series", [
        Q("Which equipment is on this drawing?",
          ["T-201", "P-201", "HV-201", "HV-202"], [],
          "All four must be listed - dropping one reads as 'not present'."),
        Q("What is the corrosion rate of P-201 casing?", ["0.20"]),
        Q("What must be closed to isolate T-201?", ["HV-201"]),
        Q("What pressure does PI-201 read and what is the design pressure?",
          ["4.2", "6.0"]),
    ])


def set3(d: Path) -> DataSet:
    """Medium: two parallel pumps into a shared header."""
    img, dr = _canvas(title="P&ID - PARALLEL TRANSFER PUMPS", dwg="DWG: SET3-011 REV 4")
    _tank(dr, 110, 330, 330, 660, "T-301", "CRUDE FEED")
    _psv(dr, 220, 330, "PSV-301")
    dr.line((330, 500, 420, 500), fill=(20, 20, 20), width=3)
    _valve(dr, 500, 500, "HV-301")
    dr.line((570, 500, 620, 500), fill=(20, 20, 20), width=3)
    dr.line((620, 380, 620, 640), fill=(20, 20, 20), width=3)      # split
    dr.line((620, 380, 700, 380), fill=(20, 20, 20), width=3)
    dr.line((620, 640, 700, 640), fill=(20, 20, 20), width=3)
    _pump(dr, 770, 380, "P-301A")
    _pump(dr, 770, 640, "P-301B")
    dr.line((822, 380, 940, 380), fill=(20, 20, 20), width=3)
    dr.line((822, 640, 940, 640), fill=(20, 20, 20), width=3)
    _valve(dr, 1010, 380, "CV-301A")
    _valve(dr, 1010, 640, "CV-301B")
    dr.line((1080, 380, 1160, 380), fill=(20, 20, 20), width=3)
    dr.line((1080, 640, 1160, 640), fill=(20, 20, 20), width=3)
    dr.line((1160, 380, 1160, 640), fill=(20, 20, 20), width=3)    # header
    _instr(dr, 1260, 300, "FT", "FT-301")
    _arrow(dr, 1160, 500, 1350, 500, "TO CDU")
    img.save(d / "PID-SET3.png")

    _report(d / "inspection_SET3.docx", "SET-3",
            [("T-301", "Crude Feed Tank", 14.0, 12.9),
             ("P-301A", "Transfer Pump A", 16.0, 15.1),
             ("P-301B", "Transfer Pump B", 16.0, 15.7)],
            ["T-301 course-1 measured 12.9 mm against nominal 14.0 mm over "
             "33 months. Minimum required 11.8 mm.",
             "P-301A casing 15.1 mm against nominal 16.0 mm over 27 months.",
             "P-301B casing 15.7 mm against nominal 16.0 mm over 27 months.",
             "PSV-301 as-found 11.5 barg. SOP-301 requires 12.0 barg.",
             "P-301A is the duty pump; P-301B is the installed spare."])
    _sop(d / "SOP-301.docx", "SOP-301 - Relief and Sparing",
         [("4. Relief setting",
           "Relief valves on crude feed tanks shall be set at 12.0 barg."),
          ("7. Sparing",
           "Where a duty and spare pump are installed, the spare shall not be "
           "isolated while the duty pump is in service.")])
    # T-301: 1.1 / 2.75 yr = 0.40 ; A: 0.9/2.25 = 0.40 ; B: 0.3/2.25 = 0.133
    return DataSet("set-3", "medium", "Two parallel pumps, shared header", [
        Q("What is the corrosion rate of T-301?", ["0.40"]),
        Q("Which of the two pumps is corroding faster, P-301A or P-301B?",
          ["P-301A"], [],
          "0.40 against 0.13 mm/yr - it must compare, not just recite."),
        Q("Does PSV-301 meet SOP-301?", ["11.5", "12.0"], ["meets", "compliant"]),
        Q("What must be closed to isolate T-301?", ["HV-301"], ["PSV-301"]),
        Q("Can P-301B be isolated while P-301A is running?",
          ["spare", "not"], [],
          "SOP-301 clause 7 forbids it - the answer is in the SOP, not the drawing."),
    ])


def set4(d: Path) -> DataSet:
    """Medium: exchanger train with two isolation points."""
    img, dr = _canvas(title="P&ID - E-401 EXCHANGER TRAIN", dwg="DWG: SET4-007 REV 3")
    _arrow(dr, 90, 500, 260, 500, "FEED IN")
    _valve(dr, 360, 500, "HV-401")
    dr.line((430, 500, 520, 500), fill=(20, 20, 20), width=3)
    dr.rectangle((520, 400, 780, 600), outline=(20, 20, 20), width=3)
    dr.line((520, 500, 780, 500), fill=(20, 20, 20), width=2)
    dr.text((600, 440), "E-401", font=_f(24, True), fill=(20, 20, 20))
    dr.text((560, 550), "SHELL & TUBE", font=_f(15), fill=(20, 20, 20))
    _instr(dr, 650, 300, "TI", "TI-401")
    dr.line((780, 500, 860, 500), fill=(20, 20, 20), width=3)
    _valve(dr, 940, 500, "HV-402")
    dr.line((1010, 500, 1090, 500), fill=(20, 20, 20), width=3)
    _tank(dr, 1090, 380, 1300, 640, "V-401", "KO DRUM")
    _psv(dr, 1195, 380, "PSV-401")
    img.save(d / "PID-SET4.png")

    _report(d / "inspection_SET4.docx", "SET-4",
            [("E-401", "Shell & Tube Exchanger", 18.0, 16.2),
             ("V-401", "Knock-out Drum", 11.0, 10.6)],
            ["E-401 shell measured 16.2 mm against nominal 18.0 mm over "
             "48 months. Minimum required 15.0 mm.",
             "V-401 shell measured 10.6 mm against nominal 11.0 mm over "
             "24 months. Minimum required 9.8 mm.",
             "TI-401 outlet temperature 168 degC.",
             "PSV-401 as-found 7.4 barg."])
    _sop(d / "SOP-401.docx", "SOP-401 - Exchanger Operating Limits",
         [("3. Outlet temperature",
           "Exchanger outlet temperature shall not exceed 160 degC. Readings "
           "above this require immediate review."),
          ("5. Relief setting",
           "Knock-out drum relief valves shall be set at 8.0 barg.")])
    # E-401: 1.8 / 4 yr = 0.45 ; life (16.2-15.0)/0.45 = 2.67 yr
    return DataSet("set-4", "medium", "Exchanger train, cross-document spec", [
        Q("What is the corrosion rate and remaining life of E-401?",
          ["0.45"], [],
          "1.8 mm over 4 years. Remaining life uses the 15.0 mm minimum."),
        Q("Is the TI-401 temperature within the SOP limit?",
          ["168", "160"], ["within", "acceptable"],
          "168 exceeds 160 - the limit is in the SOP, the reading in the report."),
        Q("Does PSV-401 meet SOP-401?", ["7.4", "8.0"], ["meets"]),
        Q("What must be closed to isolate E-401?",
          ["HV-401", "HV-402"], ["PSV-401"],
          "Two paths reach the exchanger, so both valves are required."),
    ])


def set5(d: Path) -> DataSet:
    """Complex: three-branch manifold, two relief devices, conflicting specs."""
    img, dr = _canvas(1600, 1080, title="P&ID - U-500 DISTRIBUTION MANIFOLD",
                      dwg="DWG: SET5-023 REV 6")
    _tank(dr, 90, 380, 300, 720, "T-501", "SURGE DRUM")
    _psv(dr, 195, 380, "PSV-501")
    _instr(dr, 360, 420, "LT", "LT-501")
    dr.line((300, 600, 400, 600), fill=(20, 20, 20), width=3)
    _valve(dr, 480, 600, "HV-501")
    dr.line((550, 600, 640, 600), fill=(20, 20, 20), width=3)
    _pump(dr, 710, 600, "P-501")
    dr.line((762, 600, 860, 600), fill=(20, 20, 20), width=3)
    dr.line((860, 300, 860, 900), fill=(20, 20, 20), width=3)      # manifold
    for y, tag in ((300, "HV-502"), (600, "HV-503"), (900, "HV-504")):
        dr.line((860, y, 950, y), fill=(20, 20, 20), width=3)
        _valve(dr, 1040, y, tag)
        dr.line((1110, y, 1200, y), fill=(20, 20, 20), width=3)
    _instr(dr, 1270, 300, "FT", "FT-501")
    dr.rectangle((1200, 520, 1420, 680), outline=(20, 20, 20), width=3)
    dr.text((1250, 570), "E-501", font=_f(22, True), fill=(20, 20, 20))
    _psv(dr, 1310, 520, "PSV-502")
    _arrow(dr, 1200, 900, 1420, 900, "TO FLARE")
    img.save(d / "PID-SET5.png")

    _report(d / "inspection_SET5.docx", "U-500",
            [("T-501", "Surge Drum", 16.0, 14.2),
             ("P-501", "Distribution Pump", 15.0, 14.6),
             ("E-501", "Trim Cooler", 13.0, 11.9)],
            ["T-501 course-1 measured 14.2 mm against nominal 16.0 mm over "
             "45 months. Minimum required 12.4 mm.",
             "E-501 shell measured 11.9 mm against nominal 13.0 mm over "
             "30 months. Minimum required 11.2 mm.",
             "PSV-501 as-found 14.8 barg.",
             "PSV-502 as-found 6.2 barg.",
             "LT-501 indicates 78 percent level at survey."])
    _sop(d / "SOP-501.docx", "SOP-501 - Manifold Relief Settings",
         [("4. Surge drum relief",
           "PSV protecting the surge drum shall be set at 15.0 barg."),
          ("5. Cooler relief",
           "PSV protecting a trim cooler shall be set at 6.0 barg."),
          ("8. Level",
           "Surge drum level shall not exceed 75 percent during transfer.")])
    _sheet(d / "history_SET5.xlsx",
           ["Tag", "Survey", "Measured mm"],
           [("T-501", "2022", 15.1), ("T-501", "2026", 14.2),
            ("E-501", "2023", 12.4), ("E-501", "2026", 11.9)],
           ["Nominal: T-501 16.0 mm, E-501 13.0 mm"])
    # T-501: 1.8 / 3.75 = 0.48 ; E-501: 1.1 / 2.5 = 0.44
    return DataSet("set-5", "complex",
                   "Three-branch manifold, two relief devices, conflicting specs", [
        Q("What is the corrosion rate of T-501?", ["0.48"]),
        Q("Which relief valve is non-compliant, PSV-501 or PSV-502?",
          ["PSV-501", "14.8", "15.0"], [],
          "PSV-501 at 14.8 against 15.0 fails; PSV-502 at 6.2 against 6.0 does "
          "not. Two devices, two different limits in the same SOP."),
        Q("Is the LT-501 level within the SOP limit?",
          ["78", "75"], ["within", "acceptable"]),
        Q("What must be closed to isolate T-501?", ["HV-501"], ["PSV-501"]),
        Q("Which branch valves are on the manifold?",
          ["HV-502", "HV-503", "HV-504"], [],
          "All three branches, not just the first."),
        Q("Which corrodes faster, T-501 or E-501?",
          ["T-501"], [],
          "0.48 against 0.44 - close enough that it must actually compute both."),
    ])


BUILDERS = [set1, set2, set3, set4, set5]


def main() -> int:
    import shutil
    if OUT.exists():
        shutil.rmtree(OUT)
    manifest = []
    print(f"{'set':8} {'level':9} {'files':>6} {'questions':>10}  title")
    for fn in BUILDERS:
        d = OUT / fn.__name__.replace("set", "set-")
        d.mkdir(parents=True, exist_ok=True)
        ds = fn(d)
        n = len([f for f in d.iterdir() if f.is_file()])
        manifest.append({**asdict(ds), "folder": str(d)})
        print(f"{ds.id:8} {ds.level:9} {n:6} {len(ds.questions):10}  {ds.title}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    total = sum(len(m["questions"]) for m in manifest)
    print(f"\n{len(manifest)} sets, {total} questions -> {OUT}")
    return 0




# ---------------------------------------------------------------------------
# sets 6-10: hard and very hard
# ---------------------------------------------------------------------------

def set6(d: Path) -> DataSet:
    """Hard: a documented deviation that makes a failing number acceptable."""
    img, dr = _canvas(title="P&ID - R-601 REACTOR FEED", dwg="DWG: SET6-002 REV 5")
    _tank(dr, 120, 360, 340, 680, "V-601", "FEED DRUM")
    _psv(dr, 230, 360, "PSV-601")
    dr.line((340, 560, 430, 560), fill=(20, 20, 20), width=3)
    _valve(dr, 520, 560, "HV-601")
    dr.line((590, 560, 680, 560), fill=(20, 20, 20), width=3)
    _pump(dr, 750, 560, "P-601")
    dr.line((802, 560, 900, 560), fill=(20, 20, 20), width=3)
    _valve(dr, 990, 560, "HV-602")
    dr.line((1060, 560, 1160, 560), fill=(20, 20, 20), width=3)
    dr.rectangle((1160, 440, 1360, 680), outline=(20, 20, 20), width=3)
    dr.text((1210, 540), "R-601", font=_f(24, True), fill=(20, 20, 20))
    _instr(dr, 900, 380, "PT", "PT-601")
    img.save(d / "PID-SET6.png")

    _report(d / "inspection_SET6.docx", "SET-6",
            [("V-601", "Feed Drum", 15.0, 13.1), ("R-601", "Reactor", 20.0, 19.2)],
            ["V-601 course-1 measured 13.1 mm against nominal 15.0 mm over "
             "38 months. Minimum required 12.6 mm.",
             "PSV-601 as-found 17.5 barg.",
             "PT-601 reads 16.9 barg at normal operation."])
    _sop(d / "SOP-601.docx", "SOP-601 - Reactor Feed Relief",
         [("4. Relief setting",
           "Relief valves on reactor feed drums shall be set at 18.0 barg.")])
    from docx import Document
    doc = Document()
    doc.add_heading("DEVIATION APPROVAL DA-2026-114", 0)
    doc.add_paragraph("Ref: PSV-601    Approved: 02-Aug-2026")
    doc.add_paragraph(
        "PSV-601 is approved to remain at 17.5 barg until the next turnaround "
        "on 31-Mar-2027. SOP-601 clause 4 requires 18.0 barg; this deviation "
        "is documented and approved by the Head of Inspection, and is "
        "therefore acceptable until that date.", style="List Bullet")
    doc.save(d / "deviation_DA-2026-114.docx")
    # V-601: 1.9 / 3.1667 yr = 0.60
    return DataSet("set-6", "hard", "A documented deviation overrides the SOP", [
        Q("Is the PSV-601 setting acceptable?",
          ["17.5", "DA-2026-114"], [],
          "17.5 is below the SOP's 18.0, but DA-2026-114 approves it until "
          "31-Mar-2027. The naive answer is 'non-compliant' and it is wrong."),
        Q("What is the corrosion rate of V-601?", ["0.60"]),
        Q("What must be closed to isolate V-601?", ["HV-601"], ["PSV-601"]),
    ])


def set7(d: Path) -> DataSet:
    """Hard: the same tag prefix on two different units."""
    img, dr = _canvas(1600, 1000, title="P&ID - UNITS A AND B",
                      dwg="DWG: SET7-018 REV 2")
    dr.line((800, 110, 800, 940), fill=(120, 120, 120), width=2)
    dr.text((300, 130), "UNIT A", font=_f(20, True), fill=(20, 20, 20))
    dr.text((1100, 130), "UNIT B", font=_f(20, True), fill=(20, 20, 20))
    _tank(dr, 110, 330, 300, 620, "TK-701", "UNIT A")
    dr.line((300, 500, 380, 500), fill=(20, 20, 20), width=3)
    _valve(dr, 470, 500, "HV-701")
    dr.line((540, 500, 700, 500), fill=(20, 20, 20), width=3)
    _tank(dr, 900, 330, 1090, 620, "TK-702", "UNIT B")
    dr.line((1090, 500, 1170, 500), fill=(20, 20, 20), width=3)
    _valve(dr, 1260, 500, "HV-702")
    dr.line((1330, 500, 1450, 500), fill=(20, 20, 20), width=3)
    _psv(dr, 205, 330, "PSV-701")
    _psv(dr, 995, 330, "PSV-702")
    img.save(d / "PID-SET7.png")

    _report(d / "inspection_SET7.docx", "SET-7",
            [("TK-701", "Unit A Tank", 13.0, 12.1), ("TK-702", "Unit B Tank", 13.0, 11.4)],
            ["TK-701 measured 12.1 mm against nominal 13.0 mm over 30 months. "
             "Minimum required 11.0 mm.",
             "TK-702 measured 11.4 mm against nominal 13.0 mm over 30 months. "
             "Minimum required 11.0 mm.",
             "PSV-701 as-found 9.5 barg. PSV-702 as-found 9.5 barg."])
    _sop(d / "SOP-701.docx", "SOP-701 - Unit Relief Settings",
         [("4. Unit A", "Relief valves in Unit A shall be set at 9.5 barg."),
          ("5. Unit B", "Relief valves in Unit B shall be set at 11.0 barg.")])
    # TK-701: 0.9/2.5 = 0.36 ; TK-702: 1.6/2.5 = 0.64
    return DataSet("set-7", "hard", "Same prefix, two units, two different limits", [
        Q("Which tank is corroding faster, TK-701 or TK-702?", ["TK-702"]),
        Q("Which relief valve is non-compliant, PSV-701 or PSV-702?",
          ["PSV-702"], [],
          "Both are set at 9.5. Unit A requires 9.5 (pass), Unit B requires "
          "11.0 (fail). The same number is right in one unit and wrong in the other."),
        Q("What must be closed to isolate TK-702?", ["HV-702"], ["HV-701"]),
    ])


def set8(d: Path) -> DataSet:
    """Very hard: figures only reachable by arithmetic across two documents."""
    img, dr = _canvas(title="P&ID - V-801 SEPARATOR", dwg="DWG: SET8-031 REV 7")
    _tank(dr, 140, 340, 400, 680, "V-801", "SEPARATOR")
    _psv(dr, 270, 340, "PSV-801")
    _instr(dr, 470, 400, "LT", "LT-801")
    dr.line((400, 580, 480, 580), fill=(20, 20, 20), width=3)
    _valve(dr, 570, 580, "HV-801")
    dr.line((640, 580, 740, 580), fill=(20, 20, 20), width=3)
    _pump(dr, 810, 580, "P-801")
    dr.line((862, 580, 980, 580), fill=(20, 20, 20), width=3)
    _valve(dr, 1070, 580, "HV-802")
    _arrow(dr, 1140, 580, 1350, 580, "TO STORAGE")
    img.save(d / "PID-SET8.png")

    _sheet(d / "history_V-801.xlsx",
           ["Tag", "Survey year", "Measured mm"],
           [("V-801", 2018, 17.4), ("V-801", 2022, 16.2), ("V-801", 2026, 15.0)],
           ["Nominal thickness 18.0 mm", "Minimum required 13.8 mm"])
    _sop(d / "SOP-801.docx", "SOP-801 - Re-inspection Intervals",
         [("6. Interval",
           "Re-inspection interval shall be half the calculated remaining "
           "life, and shall never exceed 5 years.")])
    _report(d / "inspection_V-801.docx", "SET-8",
            [("V-801", "Separator", 18.0, 15.0)],
            ["Latest survey 2026 recorded 15.0 mm.",
             "PSV-801 as-found 22.0 barg; SOP requires 22.0 barg.",
             "Refer to the thickness history sheet for previous surveys."])
    # 2018->2026: 2.4 mm over 8 yr = 0.30 mm/yr ; life (15.0-13.8)/0.30 = 4.0 yr
    # interval = half of 4.0 = 2.0 yr
    return DataSet("set-8", "very hard", "Rate from a history table, then an interval", [
        Q("What is the long-term corrosion rate of V-801 using the full history?",
          ["0.30"], [],
          "2.4 mm between 2018 and 2026 is 8 years, not the latest interval."),
        Q("What is the remaining life of V-801?", ["4"], [],
          "(15.0 - 13.8) / 0.30. The minimum is on the history sheet, not the report."),
        Q("What re-inspection interval does SOP-801 require for V-801?",
          ["2"], [],
          "Half of the remaining life - three documents chained together."),
    ])


def set9(d: Path) -> DataSet:
    """Very hard: two documents disagree and the newer one governs."""
    img, dr = _canvas(title="P&ID - C-901 COMPRESSOR", dwg="DWG: SET9-044 REV 9")
    _tank(dr, 110, 380, 300, 660, "V-901", "SUCTION DRUM")
    _psv(dr, 205, 380, "PSV-901")
    dr.line((300, 540, 390, 540), fill=(20, 20, 20), width=3)
    _valve(dr, 480, 540, "HV-901")
    dr.line((550, 540, 650, 540), fill=(20, 20, 20), width=3)
    _pump(dr, 720, 540, "C-901")
    dr.line((772, 540, 880, 540), fill=(20, 20, 20), width=3)
    _valve(dr, 970, 540, "CV-901")
    _instr(dr, 880, 370, "PT", "PT-901")
    _arrow(dr, 1040, 540, 1340, 540, "TO DISCHARGE")
    img.save(d / "PID-SET9.png")

    _report(d / "inspection_SET9.docx", "SET-9",
            [("V-901", "Suction Drum", 22.0, 20.8)],
            ["V-901 measured 20.8 mm against nominal 22.0 mm over 24 months.",
             "PSV-901 as-found 31.0 barg.",
             "PT-901 reads 28.4 barg."])
    _sop(d / "SOP-901-rev3.docx", "SOP-901 Rev 3 - Compressor Relief (SUPERSEDED)",
         [("Status", "This revision was superseded on 01-Jun-2026 by Rev 4."),
          ("4. Relief setting",
           "Compressor suction drum relief valves shall be set at 30.0 barg.")])
    _sop(d / "SOP-901-rev4.docx", "SOP-901 Rev 4 - Compressor Relief (CURRENT)",
         [("Status", "Effective 01-Jun-2026. Supersedes Rev 3."),
          ("4. Relief setting",
           "Compressor suction drum relief valves shall be set at 32.0 barg.")])
    # V-901: 1.2 / 2 yr = 0.60
    return DataSet("set-9", "very hard", "Superseded revision must not be used", [
        Q("Does PSV-901 meet the current SOP?",
          ["32.0", "31.0"], [],
          "Rev 4 is current and requires 32.0, so 31.0 fails. Rev 3's 30.0 "
          "would make it pass - using the superseded document inverts the answer."),
        Q("What is the corrosion rate of V-901?", ["0.60"]),
        Q("What must be closed to isolate V-901?", ["HV-901"], ["PSV-901"]),
    ])


def set10(d: Path) -> DataSet:
    """Very hard: a missing figure must be refused, not guessed."""
    img, dr = _canvas(1600, 1020, title="P&ID - U-1000 CRUDE TRAIN",
                      dwg="DWG: SET10-077 REV 11")
    _tank(dr, 100, 360, 300, 680, "T-1001", "CRUDE FEED")
    _psv(dr, 200, 360, "PSV-1001")
    dr.line((300, 540, 390, 540), fill=(20, 20, 20), width=3)
    _valve(dr, 480, 540, "HV-1001")
    dr.line((550, 540, 640, 540), fill=(20, 20, 20), width=3)
    _pump(dr, 710, 540, "P-1001")
    dr.line((762, 540, 860, 540), fill=(20, 20, 20), width=3)
    dr.line((860, 340, 860, 760), fill=(20, 20, 20), width=3)
    for y, tag in ((340, "HV-1002"), (760, "HV-1003")):
        dr.line((860, y, 950, y), fill=(20, 20, 20), width=3)
        _valve(dr, 1040, y, tag)
        dr.line((1110, y, 1220, y), fill=(20, 20, 20), width=3)
    dr.rectangle((1220, 260, 1420, 420), outline=(20, 20, 20), width=3)
    dr.text((1265, 330), "E-1001", font=_f(20, True), fill=(20, 20, 20))
    dr.rectangle((1220, 680, 1420, 840), outline=(20, 20, 20), width=3)
    dr.text((1265, 750), "E-1002", font=_f(20, True), fill=(20, 20, 20))
    _instr(dr, 660, 360, "FT", "FT-1001")
    img.save(d / "PID-SET10.png")

    _report(d / "inspection_SET10.docx", "U-1000",
            [("T-1001", "Crude Feed Tank", 20.0, 18.4),
             ("E-1001", "Preheat Exchanger", 14.0, 13.1)],
            ["T-1001 measured 18.4 mm against nominal 20.0 mm over 40 months. "
             "Minimum required 17.2 mm.",
             "E-1001 measured 13.1 mm against nominal 14.0 mm over 40 months.",
             "E-1002 was not surveyed during this campaign.",
             "PSV-1001 as-found 26.0 barg."])
    _sop(d / "SOP-1001.docx", "SOP-1001 - Crude Train",
         [("4. Relief", "Crude feed tank relief shall be set at 26.0 barg."),
          ("9. Survey", "Any exchanger not surveyed in a campaign shall be "
                        "scheduled within the following 6 months.")])
    # T-1001: 1.6 / 3.3333 = 0.48
    return DataSet("set-10", "very hard", "Refuse what is absent; act on what is stated", [
        Q("What is the corrosion rate of T-1001?", ["0.48"]),
        Q("What is the measured thickness of E-1002?",
          ["not"], ["13.1", "14.0"],
          "E-1002 was not surveyed. Inventing a thickness here is the single "
          "most dangerous failure the system can make."),
        Q("What must be closed to isolate T-1001?", ["HV-1001"], ["PSV-1001"]),
        Q("Does PSV-1001 meet SOP-1001?", ["26.0"], ["non-compliant", "fails"],
          "26.0 against a required 26.0 - this one PASSES. A system tuned to "
          "find deviations must still recognise compliance."),
    ])


BUILDERS += [set6, set7, set8, set9, set10]


if __name__ == "__main__":
    raise SystemExit(main())
