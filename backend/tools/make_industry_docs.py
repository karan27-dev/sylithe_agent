"""
Ten unseen plant documents for an end-to-end agent benchmark.

These are NOT extra copies of the existing corpus. They describe a different
unit (CDU-7, tags in the 7xxx range) so nothing collides with TK-4102 /
PSV-2041 / SOP-114, and every number is internally consistent across the ten
files. The point is to ask industry questions whose answers can only be got by
reading more than one document correctly - and to include the traps a real
inspection engineer has to survive:

  * a SUPERSEDED procedure revision that gives a different verdict than the
    CURRENT one (PSV-7303 passes under Rev 2 and fails under Rev 3)
  * a deviation approval that makes an apparent failure acceptable, but only
    until a stated date and only above a stated floor (PSV-7301)
  * an identical-looking case with NO approval, which must stay a failure
    (PSV-7302)
  * equipment that is referenced everywhere but never surveyed, so the only
    correct answer is to refuse (E-7204)
  * a corrosion rate that is only right if BOTH surveys are used (V-7101)

Ground truth is written down in bench/industry_truth.md, derived here, not
from whatever the system happens to answer.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "corpus"


def _docx(name: str, title: str, blocks: list[tuple[str, str]]) -> None:
    from docx import Document
    d = Document()
    d.add_heading(title, 0)
    for kind, text in blocks:
        if kind == "h":
            d.add_heading(text, level=1)
        elif kind == "t":                       # pipe-separated table
            rows = [r.split("|") for r in text.strip().splitlines()]
            tb = d.add_table(rows=0, cols=len(rows[0]))
            tb.style = "Table Grid"
            for r in rows:
                cells = tb.add_row().cells
                for c, v in zip(cells, r):
                    c.text = v.strip()
        else:
            d.add_paragraph(text)
    d.save(OUT / name)


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # 1 -- vessel inspection: two surveys five years apart, so a rate is
    #      computable; one course is the governing one.
    _docx("ind_01_inspection_V-7101.docx",
          "Inspection Report IR-2026-7101 - Vessel V-7101",
          [("p", "Unit: CDU-7 Crude Distillation. Equipment: V-7101 Overhead "
                 "Accumulator. Service: Class-A hydrocarbon. "
                 "Design pressure 21.0 barg. Design temperature 165 degC."),
           ("p", "Nominal shell thickness 14.0 mm. Corrosion allowance 3.0 mm. "
                 "Minimum allowable thickness t-min 11.0 mm per API 510."),
           ("h", "Ultrasonic thickness survey"),
           ("t", "Location|Survey 15-Mar-2021 (mm)|Survey 15-Mar-2026 (mm)\n"
                 "Shell Course-1|13.1|11.6\n"
                 "Shell Course-2|13.4|12.7\n"
                 "Shell Course-3|13.6|13.2\n"
                 "Head - top|13.9|13.6"),
           ("p", "Course-1 is the governing location. The interval between the "
                 "two surveys is exactly 5.0 years."),
           ("p", "Inspector: M. Rao, API 510 cert 41882. "
                 "Next external inspection due 15-Mar-2028."),
           ("p", "NOTE: Heat exchanger E-7204 was scheduled for thickness "
                 "survey in this campaign but the survey was NOT carried out - "
                 "scaffolding was unavailable. No thickness data exists for "
                 "E-7204 in this report or any other.")])

    # 2 -- PSV shop test results
    (OUT / "ind_02_psv_test_certificate.csv").write_text(
        "tag,service_class,specified_set_barg,as_found_barg,as_left_barg,"
        "test_date,certificate\n"
        "PSV-7301,Class-A,20.0,18.2,20.0,2026-02-11,PSVC-2026-0331\n"
        "PSV-7302,Class-A,20.0,15.4,20.0,2026-02-11,PSVC-2026-0332\n"
        "PSV-7303,Class-B,16.0,15.8,16.0,2026-02-12,PSVC-2026-0333\n"
        "PSV-7304,Class-A,20.0,20.4,20.0,2026-02-12,PSVC-2026-0334\n")

    # 3 -- the OLD procedure. Kept on file, and it gives a different verdict.
    (OUT / "ind_03_SOP-7201_rev2_SUPERSEDED.md").write_text(
        "# SOP-7201 Rev 2 - Relief Device Set Pressure Verification\n\n"
        "**STATUS: SUPERSEDED on 01-Jan-2026. Do not use for new "
        "assessments. Retained for audit history only. Replaced by "
        "SOP-7201 Rev 3.**\n\n"
        "## 4.1 Acceptance\n\n"
        "As-found set pressure shall be within +/- 3% of the specified set "
        "pressure.\n\n"
        "## 4.2 Minimum specified set pressure\n\n"
        "- Class-A hydrocarbon service: **16.0 barg**\n"
        "- Class-B hydrocarbon service: 16.0 barg\n")

    # 4 -- the CURRENT procedure. Raises the Class-A floor.
    (OUT / "ind_04_SOP-7201_rev3_CURRENT.md").write_text(
        "# SOP-7201 Rev 3 - Relief Device Set Pressure Verification\n\n"
        "**STATUS: CURRENT. Effective 01-Jan-2026. This revision supersedes "
        "SOP-7201 Rev 2 and shall be used for all assessments dated on or "
        "after that date.**\n\n"
        "## 4.1 Acceptance\n\n"
        "As-found set pressure shall be within +/- 3% of the specified set "
        "pressure. A device outside this band is NON-CONFORMING unless an "
        "approved deviation is in force.\n\n"
        "## 4.2 Minimum specified set pressure\n\n"
        "- Class-A hydrocarbon service: **20.0 barg** (raised from 16.0 barg "
        "in Rev 2 following the 2025 overpressure study)\n"
        "- Class-B hydrocarbon service: 16.0 barg (unchanged)\n\n"
        "## 4.3 Set pressure shall never exceed vessel design pressure.\n")

    # 5 -- the deviation that rescues exactly one device, with a floor and a
    #      hard expiry. Applies to PSV-7301 and to nothing else.
    _docx("ind_05_MOC-2026-044_deviation.docx",
          "MOC-2026-044 - Temporary Deviation Approval",
          [("p", "Title: Temporary acceptance of as-found set pressure, "
                 "PSV-7301 only."),
           ("p", "Scope: This approval applies to PSV-7301 ONLY. It does not "
                 "extend to PSV-7302, PSV-7303, PSV-7304 or any other relief "
                 "device on CDU-7."),
           ("p", "Approved condition: an as-found set pressure of not less "
                 "than 18.0 barg is accepted as conforming for PSV-7301, "
                 "notwithstanding SOP-7201 Rev 3 clause 4.2."),
           ("p", "Expiry: 30-Jun-2027. On expiry PSV-7301 reverts to the full "
                 "SOP-7201 Rev 3 requirement of 20.0 barg."),
           ("p", "Basis: overhead accumulator operating envelope re-rated by "
                 "study OPS-2025-118; relief load unchanged."),
           ("p", "Approved by: S. Nair, Head of Process Safety, 22-Feb-2026. "
                 "Reference NCR: none - raised proactively.")])

    # 6 -- the register. PSV-7302 is the only genuine open failure.
    (OUT / "ind_06_ncr_register.csv").write_text(
        "ncr,raised,equipment,description,status,closed,reference\n"
        "NCR-2026-0412,2026-02-13,PSV-7302,As-found set pressure 15.4 barg "
        "against specified 20.0 barg - outside +/-3% band per SOP-7201 Rev 3. "
        "No deviation in force.,OPEN,,PSVC-2026-0332\n"
        "NCR-2026-0408,2026-01-27,V-7101,Course-1 thickness approaching t-min "
        "- monitor at reduced interval.,OPEN,,IR-2026-7101\n"
        "NCR-2025-0377,2025-11-04,P-7110B,Mechanical seal leak - seal "
        "replaced and unit returned to service.,CLOSED,2025-11-09,WO-2025-8841\n")

    # 7 -- line list
    (OUT / "ind_07_line_list.csv").write_text(
        "line_number,service,from,to,size_in,design_barg,design_degC,material,"
        "insulation\n"
        '12"-P-7105-A1A,Overhead vapour,V-7101,E-7204,12,21.0,165,'
        "A106 Gr B,Hot\n"
        '8"-P-7106-A1A,Accumulator drain,V-7101,P-7110A,8,21.0,120,'
        "A106 Gr B,None\n"
        '6"-P-7107-B2C,Reflux to tower,P-7110A,T-7001,6,28.0,140,'
        "A312 TP316L,Hot\n"
        '10"-P-7108-A1A,Cooling water return,E-7204,CW Header,10,10.0,60,'
        "A106 Gr B,None\n")

    # 8 -- HAZOP actions
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "HAZOP actions"
    ws.append(["action", "node", "finding", "owner", "due", "status"])
    for r in [
        ["H-7-14", "Node 2 - Accumulator", "Confirm PSV-7301 relief load "
         "against re-rated envelope", "S. Nair", "2026-06-30", "CLOSED"],
        ["H-7-18", "Node 2 - Accumulator", "Replace PSV-7302 and close "
         "NCR-2026-0412 before next startup", "A. Menon", "2026-10-15", "OPEN"],
        ["H-7-22", "Node 3 - Overhead condenser", "Carry out first thickness "
         "survey of E-7204 - no baseline exists", "M. Rao", "2026-11-30",
         "OPEN"],
        ["H-7-27", "Node 1 - Tower", "Verify T-7001 reflux line material "
         "against chloride SCC review", "A. Menon", "2027-01-31", "OPEN"],
    ]:
        ws.append(r)
    wb.save(OUT / "ind_08_hazop_actions.xlsx")

    # 9 -- shift log
    (OUT / "ind_09_shift_log_CDU7.txt").write_text(
        "CDU-7 SHIFT LOG - B SHIFT\n"
        "=========================\n\n"
        "11-Feb-2026 0620  PSV-7301 and PSV-7302 removed for shop test, "
        "spares fitted. Certificates to follow.\n"
        "11-Feb-2026 1410  Shop reports PSV-7302 as-found 15.4 barg. "
        "Flagged to inspection - well outside band.\n"
        "12-Feb-2026 0705  PSV-7303 and PSV-7304 returned from shop, "
        "both reset to specified. Installed on unit.\n"
        "13-Feb-2026 0930  NCR-2026-0412 raised against PSV-7302. "
        "A. Menon informed.\n"
        "15-Mar-2026 1100  UT survey of V-7101 completed, four locations. "
        "E-7204 survey NOT done - no scaffolding.\n"
        "22-Feb-2026 1615  MOC-2026-044 approved for PSV-7301. "
        "Copy filed with inspection.\n")

    # 10 -- work orders
    (OUT / "ind_10_work_orders.md").write_text(
        "# CDU-7 maintenance work orders - open backlog\n\n"
        "| WO | equipment | description | priority | raised | status |\n"
        "|---|---|---|---|---|---|\n"
        "| WO-2026-9104 | PSV-7302 | Replace relief device, "
        "as-found set pressure non-conforming (NCR-2026-0412) | P1 | "
        "2026-02-13 | OPEN |\n"
        "| WO-2026-9118 | V-7101 | Erect scaffolding and repeat Course-1 UT "
        "at 12-month interval | P2 | 2026-03-16 | OPEN |\n"
        "| WO-2026-9125 | E-7204 | Scaffolding and baseline thickness survey "
        "(HAZOP action H-7-22) | P2 | 2026-04-02 | OPEN |\n"
        "| WO-2025-8841 | P-7110B | Mechanical seal replacement | P1 | "
        "2025-11-04 | CLOSED |\n")

    print(f"10 documents -> {OUT}")


if __name__ == "__main__":
    build()
