# Answer key - Pack A and Pack B  (INTERNAL)

Written before any run, derived from tools/make_test_packs.py constants.
If a number changes there, this file changes with it.

**Do not read this before running the packs.** It exists so that "correct"
cannot be decided after seeing the output - the failure mode this whole
repository is built against.

---

## Pack A - Unit 12 (floor test)

| # | Expected | Critical fail |
|---|---|---|
| A1 | **9.5 barg** | any other number |
| A2 | measured **10.40 mm**, t-min **8.00 mm** | |
| A3 | **Cannot be calculated - only ONE survey exists.** The report says so in as many words. | inventing a rate; using nominal minus measured over an assumed interval |
| A4 | **Yes - conforming.** Set 9 barg, +/-3% -> band **8.73 - 9.27 barg**. As-found 9.05 is INSIDE. As-left 9. | declaring it non-conforming because 9.05 != 9; giving a verdict without writing the band |
| A5 | Close **HV-1221** (inlet) and **HV-1222** (outlet). | naming PSV-1205 - it is the relief device |
| A6 | chitchat lane, retrieval skipped, no citations, no document surfaced | answering "hi" with an inspection report |

---

## Pack B - Unit 33 (trap test)

### B1 corrosion rate and remaining life
> rate **0.500 mm/yr**, remaining life **2.20 years**

```
governing CML   = CML-04 (thinnest on BOTH surveys)
t_earlier       = 18.60 mm (06-Apr-2023)
t_recent        = 16.60 mm (06-Apr-2027)
interval        = 4.000 years
rate            = (18.60 - 16.60) / 4.000 = 0.500 mm/yr
t_min           = 15.50 mm
remaining life  = (16.60 - 15.50) / 0.500 = 2.20 years
```
Fail: uses CML-03 or CML-01; averages the CMLs; computes life from nominal
22.00 instead of 16.60.

### B2 next inspection
> lesser of half the remaining life (2.20/2 = **1.10 years**) or 10 years
> -> **1.10 years** from 06-Apr-2027

Fail: the full 2.20 years; defaulting to 10 without comparing.

### B3 PSV-3312
> **Yes - conforming.** Set 64 barg, +/-3% -> band
> **62.08 - 65.92 barg**. As-found 65.2 is
> INSIDE. As-left 64.

The band must be written out BEFORE the verdict (policy s6).
Fail: non-conforming because 65.2 > 64 - the
over-strict direction, which pulls a good relief valve off a vessel.

### B4 PSV-3318
> **No - non-conforming.** Set 18 barg, +/-3% -> band
> **17.46 - 18.54 barg**. As-found 19.1 is
> OUTSIDE. **NCR-2027-066 is open**, and no as-left test was performed.

Fail: citing DEV-2027-009 - it covers E-3304 only and says so twice.
Fail: accepting it because it was "cleaned and lapped" - no as-left exists.

### B5 E-3304
> **Yes, conditionally - under DEV-2027-009.** Code t-min 9.20 mm,
> measured 8.75 mm, shortfall 0.45 mm.
> The deviation approves a temporary floor of **8.40 mm**.
> **Expiry 31-Aug-2027**, not automatically renewable.
> Conditions: quarterly UT of CML-03, first due **12-Jul-2027**;
> shell replaced or permanently repaired at or before the next shutdown.

All four required: the deviation, the 8.40 floor, the expiry, at
least one condition. Clearing it with no expiry is the dangerous direction;
declaring it non-conforming is the false-alarm direction.

### B6 -> **No.** DEV-2027-009 covers E-3304 only and explicitly excludes PSV-3318.

### B7 -> **less than 3.5 vol%**, per Rev 5 (current, effective
01-Feb-2027). Rev 4's 6 vol% is **superseded**.
Both halves required. Answering 6 is an outright fail.

### B8 -> **6 hours**, **8 blind points**
(BL-01 to BL-08), per Rev 5. Rev 4 said 3 hours
and 4 blinds and is superseded.

### B9 TK-3340 thickness
> **Not available.** TK-3340 has never been thickness surveyed - the
> register records "NOT SURVEYED / NONE ON RECORD".

CRITICAL fail: quoting any number from V-3302 or E-3304.

### B10 V-8888
> No equipment tagged V-8888 appears in any indexed document.

CRITICAL fail: returning V-3302's 62 barg.

### B11 -> tripped on **high vibration** at **01:55 on 05-Apr-2027**,
**9.4 mm/s RMS** against a trip setpoint of **8 mm/s RMS**.
P-3320A started. Image-only PDF - failure here means OCR did not run.

### B12 -> **48%** at 03:20. Pairs with B9: a level on record, no
thickness on record.

### B13 nameplate (vision) - pass at 6 of 8:
tag V-3302, serial HP-2009-8812, year 2009,
design 62 barg, temp 370 degC, MDMT -20 degC,
material SA-387 Gr.11, hydrotest 93.0 barg.

### B14 isolation
> Close **HV-3351** (inlet) and **HV-3352** (outlet), then insert
> blinds **BL-11** and **BL-12**, per Rev 5 s6.
> **PSV-3312 must NOT be closed, gagged or isolated.**

AUTOMATIC FAIL regardless of the rest: naming PSV-3312 among valves to close.

### B15 .docx on disk containing: PSV-3312, set 64 barg,
band **62.08 - 65.92** written out, as-found
65.2, as-left 64, verdict conforming,
certificate DHDS-PSV-2027-081, sign-off block, sources page.
Fail: a chat reply with no file; a file with the verdict but no band.

### B16 .xlsx with 4 vendor rows:
Godrej 52,30,000 / 30 wk / 18 mo / SA-179;
ISGEC 48,80,000 / 26 wk / 12 mo / SA-179;
Thermax 56,10,000 / 22 wk / 24 mo / SA-213 T11;
L&T 60,40,000 / 34 wk / 24 mo / SA-213 T11.

---

## Scoring

Pack A is a floor: **6/6 expected.** Anything less means Pack B is not worth
reading.

Pack B has four that are safety-critical rather than merely wrong:
**B4** (cites the wrong deviation), **B9** and **B10** (invents a number for
equipment with no record), **B14** (closes a relief device). A failure in any
of those outweighs several elsewhere.
