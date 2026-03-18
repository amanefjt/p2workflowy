# Task: Remove Inline Running Headers & Cleanup

## Task 1: Fix Empty Line Preservation Bug in `pdf_ingester.py`
- [x] Step 1: Implement `header_removed` flag to skip append correctly
- [x] Step 2: Verify with `tests/test_debug_header.py`

## Task 2: Strengthen Assumptions & Verification
- [x] Step 1: Add assertions to `test_debug_header2.py`
- [x] Step 2: Run all tests to verify current state

## Task 3: Investigate & Fix "Unlabeled Section" Issue
- [x] Step 1: Analyze `coreprompts.json` and `phase3_structure.py` (Investigation done)
- [x] Step 2: Implement `normalize_heading` enhancement and diagnostic logs
- [x] Step 3: Add regression test for Unlabeled Section

## Final Review
- [x] Dispatch final code reviewer subagent
- [x] Cleanup (Remove temp files)
- [x] Finalize Stable Release (Commit, Mission/Rules, Tagging)
