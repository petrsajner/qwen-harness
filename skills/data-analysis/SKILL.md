---
name: data-analysis
description: Practical workflow for exploring tables, CSV, JSON, logs, or survey data — sanity checks, aggregation, descriptive statistics, and honest reporting of patterns.
---

# Data Analysis

Let the data's shape drive the work; adapt depth to the question's importance.

- First ask what decision or question the analysis serves; write it down before touching data.
- Profile the raw data before computing: row counts, column types, missing values, obvious
  duplicates, and units. Fix or flag problems early.
- Sanity-check with small slices: print a few rows and one hand-computed aggregate before
  trusting bulk operations.
- Aggregate before you model: group, count, sum, and normalize — most questions end here.
- Watch classic traps: averages over skewed distributions, Simpson's paradox in combined groups,
  correlation read as causation, and time zones in timestamps.
- Prefer simple descriptive statistics (median, quartiles, shares) over opaque scores.
- When visualizing, match chart to question: change over time → line, comparison → bar,
  relationship → scatter, part-to-whole → share of a known total.
- Report uncertainty and sample size with every pattern; distinguish "in this data" from
  "in general".
- Keep the pipeline reproducible: prefer scripts or documented steps over one-off edits, so
  the same numbers can be regenerated.

The user decides what the numbers mean for their situation.
