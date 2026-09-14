# Skills

A skill is domain knowledge the model does not have and cannot infer from the
passages: which formula applies, what a standard requires, which mistakes are
common in this kind of question.

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
