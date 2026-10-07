# Derived scans

Copies of `../submissions.pdf` with pages dropped, repeated or reordered. `make.py` rebuilds every variant from the original scan and `../ground-truth.json`, so edit the spec there instead of the outputs.

```
uv run python samples/cs101-quiz5/variants/make.py
```

Each variant directory holds `submissions.pdf` and `ground-truth.json`:

- `source_pages`: the original scan page behind each variant page, in order.
- `scan_pages`: per variant page, the original page it came from, the student whose paper it physically is, version, printed page, and whether it is upside down or a scratch sheet.
- `submissions`: the grouping a grader would arrive at after fixing the scan. A scratch sheet with no student in front of it is left out.
- `expected_flags`: flags the pipeline must raise, with the variant pages they apply to. A flag listed here is not a false flag. Where the note names an alternative, either outcome passes.

Names, IDs and answers are not repeated here. Look them up by student in `../ground-truth.json`.

| Variant | Scenario |
| --- | --- |
| `missing-page-1-priya` | A page 2 with no page 1 in front of it opens the batch. |
| `missing-page-2-marcus` | A page 1 followed by the next student's page 1. |
| `duplicate-page-2-jon` | The same page fed twice in a row. |
| `swapped-page-2-across-versions` | Two groups that each hold a version A page and a version B page. |
| `interleaved-priya-jon` | Two same-version students' pages alternate. |
| `scratch-sheet-first` | A scratch sheet before any page 1. |
