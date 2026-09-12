---
tags: [file]
path: salary_lookup.py
type: python
---

# salary_lookup

## Location
`salary_lookup.py`

## Purpose
Salary benchmarking lookup CLI (429 ln) over BYO JSON data; used by the /apply workflow to sanity-check compensation.

## Connections
### Calls or imports:
- [[convert-salary-excel]] - data produced by it
- [[readme-salary-tool]] - data format doc
- [[job-search-tracker-csv]] - role/sector matching input

### Called by or imported by:
- [[cmd-apply]] - salary sanity check
- [[convert-salary-excel]] - feeds its data
- [[test_convert_salary_excel]] - tests
- [[test_salary_lookup]] - tests

## Notes
Folder hub: [[_Master File Index]] · Index: [[_Master File Index]]
