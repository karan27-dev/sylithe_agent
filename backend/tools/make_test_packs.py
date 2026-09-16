"""
Build two blind test packs plus the answer key, from one set of constants.

The packs go to the user; the key stays with me. Every expected answer below is
DERIVED from the same constants that write the documents, so the key cannot
drift from the corpus - if a number changes here, both change together.

PACK A - Unit 12, easy. Six documents, one drawing, plain single-document
facts. It exists to establish a floor: if this does not pass, nothing else
means anything.

PACK B - Unit 33, hard. Fourteen documents with the traps a real inspection
engineer survives:
  * a superseded procedure revision beside the current one, different verdicts
  * a deviation that rescues one item, with a floor, an expiry and a scope
    limit that explicitly excludes its look-alike
  * two relief valves whose certificates look identical - one inside the
    tolerance band, one outside
  * equipment referenced everywhere and never surveyed, so refusal is the
    only correct answer
  * a corrosion rate that is only right if BOTH surveys and the governing CML
    are used
  * an image-only PDF, so OCR must actually run
  * a P&ID whose isolation answer includes a relief device that must NOT be
    closed
"""

from __future__ import annotations

import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path.home() / "Downloads"

# ---------------------------------------------------------------------------
# PACK A constants - Unit 12
# ---------------------------------------------------------------------------
A = dict(
    unit="Unit 12 - Kerosene Merox",
    vessel="D-1201", vessel_service="Kerosene Settler Drum",
    design_p=9.5, design_t=120, material="SA-516 Gr.70", year=2014,
    nominal=12.0, allowance=2.5, tmin=8.0,
    survey_date="18-Feb-2027", measured=10.4,
    psv="PSV-1205", psv_set=9.0, psv_found=9.05, psv_left=9.00,
    psv_tol=3.0, psv_cert="KM-PSV-2027-041",
    pump="P-1210A", inlet="HV-1221", outlet="HV-1222",
    level="LT-1203", control="FV-1215",
)
A["band_lo"] = round(A["psv_set"] * (1 - A["psv_tol"] / 100), 2)
A["band_hi"] = round(A["psv_set"] * (1 + A["psv_tol"] / 100), 2)

# ---------------------------------------------------------------------------
# PACK B constants - Unit 33
# ---------------------------------------------------------------------------
B = dict(
    unit="Unit 33 - Diesel Hydrodesulphuriser (DHDS)",
    vessel="V-3302", vessel_service="HP Separator",
    design_p=62.0, design_t=370, material="SA-387 Gr.11", year=2009,
    nominal=22.0, allowance=3.0, tmin=15.50,
    # two surveys, governing CML is the THINNEST on both - CML-04
    survey_a="06-Apr-2023", survey_b="06-Apr-2027", interval=4.0,
    cml={"CML-01": (20.10, 19.02), "CML-02": (19.85, 18.61),
         "CML-03": (19.40, 18.20), "CML-04": (18.60, 16.60),
         "CML-05": (19.95, 18.95), "CML-06": (20.40, 19.60)},
    exch="E-3304", exch_tmin=9.20, exch_measured=8.75,
    dev="DEV-2027-009", dev_floor=8.40, dev_expiry="31-Aug-2027",
    dev_first_ut="12-Jul-2027",
    psv_ok="PSV-3312", psv_ok_set=64.0, psv_ok_found=65.20, psv_ok_left=64.00,
    psv_bad="PSV-3318", psv_bad_set=18.0, psv_bad_found=19.10,
    tol=3.0, ncr="NCR-2027-066",
    tank="TK-3340",                       # never surveyed - refusal test
    ghost="V-8888",                       # does not exist at all
    pump_a="P-3320A", pump_b="P-3320B",
    inlet="HV-3351", outlet="HV-3352", blind_a="BL-11", blind_b="BL-12",
    o2_rev4=6.0, o2_rev5=3.5, purge_rev4=3, purge_rev5=6,
    blinds_rev4=4, blinds_rev5=8,
    tank_level=48, vib=9.4, vib_trip=8.0, vib_time="01:55 on 05-Apr-2027",
)
B["rate"] = round((B["cml"]["CML-04"][0] - B["cml"]["CML-04"][1])
                  / B["interval"], 3)                       # 0.500 mm/yr
B["life"] = round((B["cml"]["CML-04"][1] - B["tmin"]) / B["rate"], 2)   # 2.20 yr
B["next_iv"] = round(min(B["life"] / 2, 10), 2)                        # 1.10 yr
B["ok_lo"] = round(B["psv_ok_set"] * (1 - B["tol"] / 100), 2)          # 62.08
B["ok_hi"] = round(B["psv_ok_set"] * (1 + B["tol"] / 100), 2)          # 65.92
B["bad_lo"] = round(B["psv_bad_set"] * (1 - B["tol"] / 100), 2)        # 17.46
B["bad_hi"] = round(B["psv_bad_set"] * (1 + B["tol"] / 100), 2)        # 18.54


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------
def _font(px, bold=False):
    for p in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold
              else "/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, px)
            except Exception:
                pass
    return ImageFont.load_default()


def docx(path: Path, title: str, blocks: list[tuple[str, str]]) -> None:
    from docx import Document
    d = Document()
    d.add_heading(title, 0)
    for kind, text in blocks:
        if kind == "h":
            d.add_heading(text, level=1)
        elif kind == "t":
            rows = [r.split("|") for r in text.strip().splitlines()]
            tb = d.add_table(rows=0, cols=len(rows[0]))
            tb.style = "Table Grid"
            for r in rows:
                for c, v in zip(tb.add_row().cells, r):
                    c.text = v.strip()
        else:
            d.add_paragraph(text)
    d.save(path)


def pdf_text(path: Path, title: str, lines: list[str]) -> None:
    """A real text PDF - pdftotext finds words in it."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    y = 60
    page.insert_text((60, y), title, fontsize=15, fontname="hebo")
    y += 30
    for ln in lines:
        if y > 760:
            page = doc.new_page(); y = 60
        page.insert_text((60, y), ln, fontsize=10)
        y += 16
    doc.save(path)
    doc.close()


def pdf_scanned(path: Path, title: str, lines: list[str]) -> None:
    """Image-only PDF - no text layer, so OCR has to run."""
    img = Image.new("RGB", (1240, 1600), (252, 251, 246))
    dr = ImageDraw.Draw(img)
    dr.text((70, 60), title, font=_font(38, True), fill=(20, 20, 20))
    dr.line((70, 118, 1170, 118), fill=(60, 60, 60), width=2)
    y = 160
    for ln in lines:
        dr.text((70, y), ln, font=_font(26), fill=(25, 25, 25))
        y += 46
    img = img.rotate(-0.6, expand=False, fillcolor=(252, 251, 246))
    img.convert("RGB").save(path, "PDF", resolution=150)


def nameplate(path: Path, rows: list[tuple[str, str]]) -> None:
    img = Image.new("RGB", (1000, 680), (198, 202, 200))
    dr = ImageDraw.Draw(img)
    dr.rectangle((40, 40, 960, 640), fill=(174, 179, 177),
                 outline=(88, 93, 91), width=5)
    dr.text((80, 78), "MANUFACTURER NAMEPLATE", font=_font(38, True),
            fill=(22, 25, 24))
    dr.line((80, 132, 920, 132), fill=(78, 83, 81), width=3)
    y = 176
    for k, v in rows:
        dr.text((90, y), k, font=_font(27), fill=(44, 49, 47))
        dr.text((470, y), v, font=_font(27), fill=(18, 21, 20))
        y += 56
    img.rotate(-1.3, expand=False, fillcolor=(198, 202, 200)).save(
        path, quality=84)


# ---------------------------------------------------------------------------
# P&ID drawing
# ---------------------------------------------------------------------------
def pid(path: Path, dwg: str, unit: str, vessel: str, service: str,
        psv: str, inlet: str, outlet: str, pump: str, level: str,
        control: str | None = None, bypass: str | None = None) -> None:
    W, H = 1600, 1040
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    ink = (18, 18, 18)
    ft, fs = _font(26, True), _font(19)

    d.rectangle((28, 28, W - 28, H - 28), outline=ink, width=3)
    d.text((50, 44), f"P&ID - {unit}", font=ft, fill=ink)
    d.text((W - 430, 48), f"DWG: {dwg}   REV: 1", font=fs, fill=ink)
    d.line((28, 92, W - 28, 92), fill=ink, width=2)

    # vessel
    d.rectangle((150, 330, 430, 690), outline=ink, width=3)
    d.arc((150, 290, 430, 370), 180, 360, fill=ink, width=3)
    d.text((228, 480), vessel, font=ft, fill=ink)
    d.text((176, 520), service.upper()[:18], font=fs, fill=ink)

    # relief device on top
    d.line((290, 330, 290, 236), fill=ink, width=3)
    d.polygon([(290, 236), (258, 190), (322, 190)], outline=ink, width=3)
    d.ellipse((252, 126, 328, 190), outline=ink, width=3)
    d.text((262, 148), psv, font=fs, fill=ink)

    # level instrument
    d.line((430, 500, 520, 500), fill=ink, width=2)
    d.ellipse((520, 468, 592, 532), outline=ink, width=3)
    d.text((530, 490), level, font=fs, fill=ink)

    # inlet line
    d.line((60, 400, 150, 400), fill=ink, width=3)
    d.polygon([(90, 382), (90, 418), (116, 400)], outline=ink, width=3)
    d.polygon([(116, 400), (142, 382), (142, 418)], outline=ink, width=3)
    d.text((74, 340), inlet, font=fs, fill=ink)

    # outlet line -> outlet valve -> pump
    d.line((430, 640, 700, 640), fill=ink, width=3)
    d.polygon([(520, 622), (520, 658), (546, 640)], outline=ink, width=3)
    d.polygon([(546, 640), (572, 622), (572, 658)], outline=ink, width=3)
    d.text((512, 580), outlet, font=fs, fill=ink)
    d.ellipse((700, 596, 790, 686), outline=ink, width=3)
    d.polygon([(700, 641), (745, 596), (745, 686)], outline=ink, width=3)
    d.text((706, 706), pump, font=fs, fill=ink)
    d.line((790, 641), (1000, 641), fill=ink, width=3) if False else None
    d.line((790, 641, 1000, 641), fill=ink, width=3)

    if control:
        d.polygon([(1000, 623), (1000, 659), (1026, 641)], outline=ink, width=3)
        d.polygon([(1026, 641), (1052, 623), (1052, 659)], outline=ink, width=3)
        d.arc((1000, 566, 1052, 618), 180, 360, fill=ink, width=3)
        d.line((1026, 592, 1026, 623), fill=ink, width=2)
        d.text((994, 700), control, font=fs, fill=ink)
        d.line((1052, 641, 1290, 641), fill=ink, width=3)

    if bypass:
        d.line((960, 641, 960, 830), fill=ink, width=3)
        d.line((960, 830, 1330, 830), fill=ink, width=3)
        d.line((1330, 830, 1330, 641), fill=ink, width=3)
        d.polygon([(1120, 812), (1120, 848), (1146, 830)], outline=ink, width=3)
        d.polygon([(1146, 830), (1172, 812), (1172, 848)], outline=ink, width=3)
        d.text((1112, 862), bypass, font=fs, fill=ink)

    d.line((1290, 641, 1480, 641), fill=ink, width=3)
    d.text((1330, 596), "TO UNIT LIMIT", font=fs, fill=ink)
    img.save(path)


# ---------------------------------------------------------------------------
# PACK A - Unit 12, easy
# ---------------------------------------------------------------------------
def build_a(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    a = A

    docx(root / "A1_D-1201_datasheet.docx",
         f"Equipment Datasheet - {a['vessel']}",
         [("p", f"Unit: {a['unit']}. Equipment: {a['vessel']} "
                f"{a['vessel_service']}."),
          ("t", f"Field|Value\n"
                f"Design Pressure|{a['design_p']} barg\n"
                f"Design Temperature|{a['design_t']} degC\n"
                f"Material|{a['material']}\n"
                f"Year of Manufacture|{a['year']}\n"
                f"Nominal Shell Thickness|{a['nominal']:.2f} mm\n"
                f"Corrosion Allowance|{a['allowance']:.2f} mm\n"
                f"Minimum Required Thickness (t-min)|{a['tmin']:.2f} mm")])

    pdf_text(root / "A2_D-1201_thickness_survey.pdf",
             f"Thickness Survey - {a['vessel']}",
             [f"Unit: {a['unit']}",
              f"Survey date: {a['survey_date']}",
              "Method: ultrasonic, Olympus 38DL Plus, 5 MHz twin crystal",
              "",
              "Location            Measured (mm)",
              f"Shell Course 1      {a['measured']:.2f}",
              "Shell Course 2      10.85",
              "Head - top          11.40",
              "",
              f"Governing (thinnest) location is Shell Course 1 at "
              f"{a['measured']:.2f} mm.",
              f"Minimum required thickness (t-min) is {a['tmin']:.2f} mm.",
              "Only one survey exists for this vessel. No corrosion rate can",
              "be derived from a single survey.",
              "Inspector: V. Menon, API 510 #52318"])

    with (root / "A3_PSV-1205_test_certificate.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["field", "value"])
        for k, v in [("certificate", a["psv_cert"]), ("tag", a["psv"]),
                     ("protects", a["vessel"]),
                     ("set_pressure_barg", a["psv_set"]),
                     ("applicable_tolerance", f"+/- {a['psv_tol']:g}% of set pressure"),
                     ("as_found_barg", a["psv_found"]),
                     ("as_left_barg", a["psv_left"]),
                     ("test_date", "20-Feb-2027"),
                     ("test_medium", "Nitrogen")]:
            w.writerow([k, v])

    (root / "A4_shift_log.txt").write_text(
        f"{a['unit']} - SHIFT LOG, A SHIFT\n"
        "=================================\n\n"
        f"18-Feb-2027 06:10  Took over. {a['vessel']} level 58%, "
        f"pressure 7.2 barg. Normal.\n"
        f"18-Feb-2027 09:35  UT survey of {a['vessel']} completed, three "
        "locations.\n"
        f"18-Feb-2027 14:20  {a['pump']} running, discharge steady. "
        "No abnormality.\n"
        f"20-Feb-2027 11:05  {a['psv']} returned from shop test, "
        "certificate filed.\n"
        "20-Feb-2027 18:00  Handover to B shift. Unit stable.\n"
        "SIGNED: A. DSOUZA, SHIFT IN-CHARGE\n")

    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Equipment"
    ws.append(["tag", "description", "unit", "design_barg", "last_survey"])
    for r in [[a["vessel"], a["vessel_service"], "12", a["design_p"],
               a["survey_date"]],
              [a["pump"], "Kerosene Transfer Pump", "12", 14.0, "—"],
              [a["psv"], "Relief Device", "12", a["psv_set"], "20-Feb-2027"]]:
        ws.append(r)
    wb.save(root / "A5_equipment_register.xlsx")

    pid(root / "A6_PID-U12-001.png", "PID-U12-001", a["unit"], a["vessel"],
        a["vessel_service"], a["psv"], a["inlet"], a["outlet"], a["pump"],
        a["level"])


# ---------------------------------------------------------------------------
# PACK B - Unit 33, hard
# ---------------------------------------------------------------------------
def build_b(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    b = B

    rows = "\n".join(f"{k}|{v[0]:.2f}|{v[1]:.2f}" for k, v in b["cml"].items())
    docx(root / "B01_V-3302_thickness_survey.docx",
         f"Vessel Thickness Survey - {b['vessel']}",
         [("p", f"Unit: {b['unit']}. Equipment: {b['vessel']} "
                f"{b['vessel_service']}."),
          ("h", "1. Equipment particulars"),
          ("t", f"Field|Value\n"
                f"Design Pressure|{b['design_p']} barg\n"
                f"Design Temperature|{b['design_t']} degC\n"
                f"Material|{b['material']}\n"
                f"Year of Manufacture|{b['year']}\n"
                f"Nominal Shell Thickness|{b['nominal']:.2f} mm\n"
                f"Corrosion Allowance|{b['allowance']:.2f} mm\n"
                f"Minimum Required Thickness (t-min)|{b['tmin']:.2f} mm\n"
                f"Inspection Code|API 510"),
          ("h", "2. Ultrasonic thickness readings - shell course 1"),
          ("p", "All readings in millimetres. Same CML grid used on both "
                "surveys."),
          ("t", f"CML|Survey A ({b['survey_a']})|Survey B ({b['survey_b']})\n"
                + rows),
          ("h", "3. Remarks"),
          ("p", f"Governing (thinnest) location is CML-04 on both surveys. "
                f"The interval between Survey A and Survey B is exactly "
                f"{b['interval']:.3f} years. No repair, weld overlay or "
                f"re-rating was carried out between the two surveys. "
                f"Corrosion rate and remaining life have deliberately NOT "
                f"been calculated in this report."),
          ("p", "Inspector: S. Bhatt, API 510 #47720")])

    (root / "B02_inspection_policy.md").write_text(
        "# Unit 33 inspection policy - extract\n\n"
        "## 3. Corrosion rate\n\n"
        "The corrosion rate shall be calculated at the **governing CML**, "
        "which is the **thinnest** location, using the earliest and the most "
        "recent survey. Averaging CMLs is not permitted.\n\n"
        "    rate = (t_earlier - t_recent) / interval_years\n\n"
        "## 4. Remaining life\n\n"
        "Remaining life shall be calculated from the **most recent measured "
        "thickness**, never from nominal:\n\n"
        "    remaining life = (t_recent - t_min) / rate\n\n"
        "## 5. Next inspection interval\n\n"
        "The next inspection interval shall be the **lesser of half the "
        "remaining life, or 10 years**.\n\n"
        "## 6. Relief device acceptance\n\n"
        "As-found set pressure shall lie within **+/- 3% of the nameplate "
        "set pressure**. The band shall be written out in full before any "
        "verdict is recorded. A device outside the band is NON-CONFORMING "
        "unless an approved deviation is in force for that specific device.\n\n"
        "## 7. Deviations\n\n"
        "A deviation applies only to the equipment named in it. A deviation "
        "shall never be applied by analogy to similar equipment.\n")

    pdf_text(root / "B03_DHDS-SOP-210_Rev4_SUPERSEDED.pdf",
             "DHDS-SOP-210 Rev 4 - Nitrogen Purging (SUPERSEDED)",
             ["STATUS: SUPERSEDED on 01-Feb-2027. Retained for audit history",
              "only. Replaced by DHDS-SOP-210 Rev 5. Not for field use.",
              "",
              "5.1 Oxygen acceptance criterion",
              f"    Oxygen content shall be less than {b['o2_rev4']:g} vol%.",
              "",
              "5.2 Purge duration",
              f"    Purge shall run for not less than {b['purge_rev4']} hours.",
              "",
              "5.3 Blind points",
              f"    {b['blinds_rev4']} blind points are required."])

    pdf_text(root / "B04_DHDS-SOP-210_Rev5_CURRENT.pdf",
             "DHDS-SOP-210 Rev 5 - Nitrogen Purging (CURRENT)",
             ["STATUS: CURRENT. Effective 01-Feb-2027. This revision",
              "supersedes DHDS-SOP-210 Rev 4 and shall be used for all work",
              "dated on or after that date.",
              "",
              "5.1 Oxygen acceptance criterion",
              f"    Oxygen content shall be less than {b['o2_rev5']:g} vol%.",
              f"    (Reduced from {b['o2_rev4']:g} vol% in Rev 4 following the",
              "     2026 reactor entry review.)",
              "",
              "5.2 Purge duration",
              f"    Purge shall run for not less than {b['purge_rev5']} hours.",
              "",
              "5.3 Blind points",
              f"    {b['blinds_rev5']} blind points are required, BL-01 to "
              f"BL-0{b['blinds_rev5']}.",
              "",
              f"6. Isolation of {b['vessel']} for entry",
              f"    Positive isolation requires closure of the two hand valves",
              f"    {b['inlet']} (inlet) and {b['outlet']} (outlet), followed by",
              f"    insertion of blinds {b['blind_a']} and {b['blind_b']}.",
              f"    {b['psv_ok']} is the relief device protecting {b['vessel']}",
              "    and shall NOT be closed, gagged or isolated as part of any",
              "    isolation activity."])

    for tag, sp, found, left, cert, date in [
        (b["psv_ok"], b["psv_ok_set"], b["psv_ok_found"], b["psv_ok_left"],
         "DHDS-PSV-2027-081", "02-Apr-2027"),
        (b["psv_bad"], b["psv_bad_set"], b["psv_bad_found"], None,
         "DHDS-PSV-2027-082", "02-Apr-2027"),
    ]:
        lines = [f"Certificate: {cert}",
                 f"Unit: {b['unit']}",
                 f"Tag: {tag}",
                 f"Protects: {b['vessel']} {b['vessel_service']}",
                 "Manufacturer / Type: Anderson Greenwood, conventional",
                 f"Set Pressure (nameplate), {tag} = {sp:g} barg",
                 f"Applicable Tolerance, {tag} = API 527 / ASME Sec VIII, "
                 f"+/- {b['tol']:g}% of set pressure",
                 f"Test Date: {date}",
                 "Test Medium: Nitrogen",
                 "",
                 "AS-FOUND TEST RESULTS",
                 f"Governing as-found value = {found:g} barg"]
        if left is not None:
            lines += ["", "AS-LEFT TEST RESULTS",
                      f"Governing as-left value = {left:g} barg",
                      "Valve re-set to nameplate set pressure and sealed."]
        else:
            lines += ["", "AS-LEFT TEST RESULTS",
                      "NOT PERFORMED. Valve cleaned and lapped; no as-left",
                      "test was carried out on this certificate."]
        lines += ["",
                  "NOTE: The acceptance band has deliberately NOT been",
                  "arithmetically evaluated on this certificate. Acceptance",
                  "is determined by the Inspection Engineer against the",
                  "tolerance stated above."]
        pdf_text(root / f"B0{5 if tag == b['psv_ok'] else 6}_{tag}"
                        f"_test_certificate.pdf",
                 f"Relief Device Shop Test Certificate - {tag}", lines)

    docx(root / "B07_DEV-2027-009_deviation_approval.docx",
         f"{b['dev']} - Temporary Deviation Approval",
         [("p", f"Title: Temporary acceptance of measured shell thickness, "
                f"{b['exch']} ONLY."),
          ("p", f"Scope: This approval applies to {b['exch']} shell thickness "
                f"ONLY. It does NOT extend to {b['vessel']}, {b['psv_ok']}, "
                f"{b['psv_bad']}, {b['tank']} or any other equipment on "
                f"Unit 33. A deviation is never applied by analogy."),
          ("p", f"Approved condition: a measured thickness of not less than "
                f"{b['dev_floor']:.2f} mm is accepted as conforming for "
                f"{b['exch']}, notwithstanding the code t-min of "
                f"{b['exch_tmin']:.2f} mm."),
          ("p", f"Expiry: {b['dev_expiry']}. NOT automatically renewable. On "
                f"expiry {b['exch']} reverts to the full code requirement."),
          ("p", f"Conditions: (1) quarterly ultrasonic thickness survey of "
                f"CML-03, first due {b['dev_first_ut']}; (2) the shell shall "
                f"be replaced or permanently repaired at or before the next "
                f"planned shutdown."),
          ("p", "Basis: API 579 Level 2 fitness-for-service assessment "
                "FFS-2027-014."),
          ("p", "Approved by: D. Iyer, Head of Inspection, 18-Apr-2027.")])

    docx(root / "B08_E-3304_shell_survey.docx",
         f"Shell Thickness Survey - {b['exch']}",
         [("p", f"Unit: {b['unit']}. Equipment: {b['exch']} Reactor Feed / "
                f"Effluent Exchanger, shell side."),
          ("t", f"Field|Value\n"
                f"Code minimum thickness (t-min)|{b['exch_tmin']:.2f} mm\n"
                f"Measured, governing CML-03|{b['exch_measured']:.2f} mm\n"
                f"Survey date|10-Apr-2027"),
          ("p", f"The measured thickness is below the code t-min by "
                f"{b['exch_tmin'] - b['exch_measured']:.2f} mm. Refer to "
                f"the Inspection Section before returning to service.")])

    with (root / "B09_ncr_register.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ncr", "raised", "equipment", "description", "status"])
        w.writerow([b["ncr"], "03-Apr-2027", b["psv_bad"],
                    f"As-found {b['psv_bad_found']:g} barg against set "
                    f"{b['psv_bad_set']:g} barg, outside the +/-3% band. "
                    f"No as-left test performed. No deviation in force.",
                    "OPEN"])
        w.writerow(["NCR-2027-061", "11-Apr-2027", b["exch"],
                    "Shell thickness below code t-min; covered by "
                    f"{b['dev']}.", "OPEN"])

    pdf_scanned(root / "B10_shift_log_scanned.pdf",
                f"{b['unit']} - NIGHT SHIFT LOG",
                ["22:05  Took over charge. Unit stable at 78% throughput.",
                 f"       {b['vessel']} level 51%, pressure 54.8 barg.",
                 f"{b['vib_time'][:5]}  {b['pump_b']} tripped on HIGH VIBRATION.",
                 f"       Vibration recorded {b['vib']:g} mm/s RMS at DE bearing.",
                 f"       Trip setpoint is {b['vib_trip']:g} mm/s RMS.",
                 f"       Standby pump {b['pump_a']} started immediately.",
                 "       No process upset. Throughput maintained.",
                 f"03:20  {b['tank']} slop tank level {b['tank_level']}%.",
                 "       Routine transfer lined up. No abnormality.",
                 f"05:40  Handover to day shift. {b['pump_b']} REMAINS OUT",
                 "       OF SERVICE pending vibration investigation.",
                 "SIGNED: N. PILLAI, SHIFT IN-CHARGE UNIT 33"])

    nameplate(root / "B11_V-3302_nameplate_photo.png",
              [("EQUIPMENT TAG", b["vessel"]),
               ("SERIAL NO", "HP-2009-8812"),
               ("YEAR BUILT", str(b["year"])),
               ("DESIGN PRESSURE", f"{b['design_p']:g} barg"),
               ("DESIGN TEMP", f"{b['design_t']} degC"),
               ("MDMT", "-20 degC"),
               ("MATERIAL", b["material"]),
               ("HYDROTEST", "93.0 barg")])

    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active; ws.title = "Equipment"
    ws.append(["tag", "description", "unit", "design_barg", "last_thickness_survey"])
    for r in [[b["vessel"], b["vessel_service"], "33", b["design_p"], b["survey_b"]],
              [b["exch"], "Feed/Effluent Exchanger", "33", 68.0, "10-Apr-2027"],
              [b["tank"], "Slop Tank", "33", "ATMOSPHERIC",
               "NOT SURVEYED / NONE ON RECORD"],
              [b["pump_a"], "Charge Pump A", "33", 78.0, "—"],
              [b["pump_b"], "Charge Pump B", "33", 78.0, "—"]]:
        ws.append(r)
    ws2 = wb.create_sheet("Relief devices")
    ws2.append(["tag", "protects", "set_barg", "last_test"])
    ws2.append([b["psv_ok"], b["vessel"], b["psv_ok_set"], "02-Apr-2027"])
    ws2.append([b["psv_bad"], b["vessel"], b["psv_bad_set"], "02-Apr-2027"])
    wb.save(root / "B12_equipment_register.xlsx")

    with (root / "B13_E-3304_bundle_quotations.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vendor", "price_inr", "delivery_weeks", "warranty_months",
                    "material"])
        for r in [["Godrej Process Equipment", 5230000, 30, 18, "SA-179"],
                  ["ISGEC Heavy Engineering", 4880000, 26, 12, "SA-179"],
                  ["Thermax Ltd", 5610000, 22, 24, "SA-213 T11"],
                  ["L&T Heavy Engineering", 6040000, 34, 24, "SA-213 T11"]]:
            w.writerow(r)

    pid(root / "B14_PID-U33-014.png", "PID-U33-014", b["unit"], b["vessel"],
        b["vessel_service"], b["psv_ok"], b["inlet"], b["outlet"],
        b["pump_a"], "LT-3305", control="FV-3360", bypass="HV-3365")


def main() -> int:
    a_dir = OUT / "PACK-A-Unit12-easy"
    b_dir = OUT / "PACK-B-Unit33-hard"
    build_a(a_dir)
    build_b(b_dir)
    for d in (a_dir, b_dir):
        n = len(list(d.iterdir()))
        print(f"{d}  ({n} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
