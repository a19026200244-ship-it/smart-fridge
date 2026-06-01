# Partial Takeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first working version of SmartFridge partial takeout handling while preserving existing count-based inventory behavior.

**Architecture:** Keep `deploy/fridge_mgr.py` as the board runtime, but move testable partial-quantity helpers into `deploy/partial_qty.py`. The board compares count changes first, then compares liquid-level state only when count is unchanged and detection details are available. The Flask server stores and displays partial-state fields.

**Tech Stack:** Python 3, JSON config, SQLite via Flask server, existing script-style tests.

---

### Task 1: Partial Quantity Helper

**Files:**
- Create: `deploy/partial_qty.py`
- Test: `test/test_partial_takeout.py`

- [ ] Add failing tests for detection detail parsing, level comparison, and missing-frame fallback.
- [ ] Implement pure helper functions without GPIO or Flask dependencies.
- [ ] Run `python3 test/test_partial_takeout.py`.

### Task 2: Board Runtime Integration

**Files:**
- Modify: `deploy/fridge_mgr.py`
- Modify: `config/board.json`
- Test: `test/test_partial_takeout.py`

- [ ] Add `parse_detection_details()` and keep old `parse_detections()` behavior.
- [ ] Add `partial_take_out` event fields to `evt_add()`.
- [ ] Compare liquid-level state after normal count-diff handling.
- [ ] Update `bottle` to `qty_type=liquid_level` and add liquid-level config.

### Task 3: Server Sync and UI

**Files:**
- Modify: `server/app.py`
- Modify: `server/templates/index.html`
- Test: `test/test_partial_takeout.py`

- [ ] Add SQLite columns `before_qty_estimate`, `after_qty_estimate`, `reason`.
- [ ] Fix confirmed `take_out` review events to decrement inventory.
- [ ] Sync and display partial-takeout fields.
- [ ] Add manual state correction for review events.

### Task 4: Verification

**Files:**
- Test: `test/test_event_enhanced.py`
- Test: `test/test_partial_remove.py`
- Test: `test/test_regression.py`
- Test: `test/test_config.py`

- [ ] Run all existing script tests.
- [ ] Report any environment-only limitations separately from code failures.
