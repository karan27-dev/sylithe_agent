# Skills

A skill is domain knowledge the model does not have and cannot infer from the
passages: which formula applies, what a standard requires, which mistakes are
common in this kind of question.

## An example may carry no real tags and no real numbers

This is not style. Measured: the corrosion-rate skill shipped a worked example
reading `nominal_mm = 12.0`, `measured_mm = 11.2`, `months = 24`,
`rate = 0.40 mm/yr`. Asked for the corrosion rate of a vessel in a different
unit entirely, the system answered "0.40 mm/yr, calculated from a 0.8 mm loss
over 2 years ... measured thickness of 11.2 mm". Every one of those figures
came from this file. It read as a hallucination and was a copy.

The relief-device skill did the same: its example named `SOP-114 requires 14.0
barg` and `NCR-2026-0088`, and both appeared in an answer about a different
relief valve on a different unit.

A worked example teaches a METHOD. Any number inside it can and will be lifted
as a FACT. So examples use `<angle bracket placeholders>` and nothing that
could be mistaken for plant data. tools/skills.py enforces this on load.

That same file also carried the wrong formula - rate from nominal minus
measured, rather than between two surveys - so it was teaching an incorrect
method and supplying incorrect numbers to go with it.

Each is a YAML file. No code changes are needed to add one.

```yaml
name: corrosion-rate
when: ["corrosion rate", "remaining life", "thickness loss"]
rules:
  - Corrosion rate = (nominal - measured) / (interval in years).
example: |
  loss = 12.0 - 11.2
  rate = loss / (24 / 12)
checks:
  - pattern: "mm/yr"
    why: A corrosion rate without units is not an answer.
```

`when` is matched against the question. Matching skills are injected into the
prompt as domain rules plus a worked example - in that order, because this
model family follows an example far more reliably than a rule. That was
measured twice on this project: the router went from 83% to 100% on few-shot
examples alone, and the code lane stopped inventing numbers only once it was
shown one worked calculation.

`checks` run on the finished answer and are reported, never used to silently
rewrite it.
