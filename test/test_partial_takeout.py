#!/usr/bin/env python3
"""
SmartFridge partial takeout tests.

These tests exercise the non-hardware logic for:
- parsing detection details
- comparing liquid-level states
- generating a partial_take_out event
- falling back to needs_review when visual evidence is missing
"""
import json
import os
import tempfile

PROJECT = "/home/jing/my-project/smartfridge"
os.chdir(PROJECT)
if PROJECT not in os.sys.path:
    os.sys.path.insert(0, PROJECT)

from deploy.partial_qty import (  # noqa: E402
    LEVEL_ORDER,
    build_count_map,
    compare_liquid_levels,
    detail_level,
    parse_detection_details_from_file,
)


def assert_equal(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value, label):
    if not value:
        raise AssertionError(f"{label}: expected truthy value, got {value!r}")


def write_det_file(payload):
    fd, path = tempfile.mkstemp(prefix="smartfridge_det_", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(payload, f)
    return path


def write_bottle_frame(level_ratio, size=(160, 240), bbox=(40, 20, 120, 220)):
    from PIL import Image, ImageDraw

    fd, path = tempfile.mkstemp(prefix="smartfridge_bottle_", suffix=".png")
    os.close(fd)
    img = Image.new("RGB", size, (245, 245, 245))
    draw = ImageDraw.Draw(img)
    x1, y1, x2, y2 = bbox
    inner = (x1 + 10, y1 + 14, x2 - 10, y2 - 10)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=16, outline=(130, 130, 130), width=3, fill=(250, 250, 250))
    liquid_top = inner[3] - int((inner[3] - inner[1]) * level_ratio)
    draw.rectangle((inner[0], liquid_top, inner[2], inner[3]), fill=(35, 120, 210))
    draw.line((inner[0], liquid_top, inner[2], liquid_top), fill=(20, 80, 150), width=2)
    draw.rounded_rectangle((x1, y1, x2, y2), radius=16, outline=(110, 110, 110), width=3)
    img.save(path)
    return path, list(bbox)


def test_parse_detection_details_keeps_bbox_frame_and_level():
    path = write_det_file({
        "frame_path": "/tmp/current_frame.jpg",
        "detections": [
            {
                "name": "bottle",
                "confidence": 0.91,
                "bbox": [10, 20, 110, 260],
                "qty_estimate": "half",
            },
            {"name": "person", "confidence": 0.8},
        ],
    })
    try:
        details = parse_detection_details_from_file(path)
    finally:
        os.remove(path)

    assert_equal(len(details), 1, "person should be filtered")
    assert_equal(details[0]["name"], "bottle", "name")
    assert_equal(details[0]["bbox"], [10, 20, 110, 260], "bbox")
    assert_equal(details[0]["frame_path"], "/tmp/current_frame.jpg", "frame_path")
    assert_equal(details[0]["qty_estimate"], "half", "qty_estimate")


def test_build_count_map_matches_existing_parse_detections_behavior():
    details = [
        {"name": "bottle"},
        {"name": "bottle"},
        {"name": "apple"},
        {"name": "person"},
    ]
    assert_equal(build_count_map(details), {"bottle": 2, "apple": 1}, "count map")


def test_detail_level_estimates_level_from_image_bbox():
    frame_path, bbox = write_bottle_frame(0.46)
    try:
        result = detail_level({"name": "bottle", "confidence": 0.92, "frame_path": frame_path, "bbox": bbox})
    finally:
        os.remove(frame_path)

    assert_equal(result["level"], "half", "image level")
    assert_true(result["confidence"] >= 0.65, "image confidence")
    assert_true("图像估算" in result["reason"], "image reason")


def test_compare_liquid_levels_uses_image_bbox_when_no_level_field():
    before_path, before_bbox = write_bottle_frame(0.86)
    after_path, after_bbox = write_bottle_frame(0.46)
    try:
        before = [{"name": "bottle", "confidence": 0.93, "frame_path": before_path, "bbox": before_bbox}]
        after = [{"name": "bottle", "confidence": 0.91, "frame_path": after_path, "bbox": after_bbox}]
        package_map = {"bottle": {"qty_type": "liquid_level"}}
        display_map = {"bottle": "瓶装饮品"}
        category_map = {"bottle": {"c1": "乳品饮品", "c2": "包装饮品"}}

        events = compare_liquid_levels(before, after, package_map, display_map, category_map)
    finally:
        os.remove(before_path)
        os.remove(after_path)

    assert_equal(len(events), 1, "event count")
    event = events[0]
    assert_equal(event["action"], "partial_take_out", "action")
    assert_equal(event["review_status"], "confirmed", "review_status")
    assert_equal(event["before_qty_estimate"], "full", "before image level")
    assert_equal(event["after_qty_estimate"], "half", "after image level")
    assert_true("0." in event["reason"], "reason includes ratios")


def test_compare_liquid_levels_generates_partial_take_out():
    before = [{"name": "bottle", "qty_estimate": "full", "confidence": 0.9}]
    after = [{"name": "bottle", "qty_estimate": "half", "confidence": 0.86}]
    package_map = {"bottle": {"qty_type": "liquid_level"}}
    display_map = {"bottle": "瓶装饮品"}
    category_map = {"bottle": {"c1": "乳品饮品", "c2": "包装饮品"}}

    events = compare_liquid_levels(before, after, package_map, display_map, category_map)

    assert_equal(len(events), 1, "event count")
    event = events[0]
    assert_equal(event["action"], "partial_take_out", "action")
    assert_equal(event["food_name"], "瓶装饮品", "food_name")
    assert_equal(event["review_status"], "confirmed", "review_status")
    assert_equal(event["before_qty_estimate"], "full", "before level")
    assert_equal(event["after_qty_estimate"], "half", "after level")
    assert_equal(event["qty_estimate"], "half", "current level")
    assert_true(event["confidence"] >= 0.65, "confidence")


def test_compare_liquid_levels_missing_after_level_needs_review():
    before = [{"name": "bottle", "qty_estimate": "full", "confidence": 0.9}]
    after = [{"name": "bottle", "confidence": 0.82}]
    package_map = {"bottle": {"qty_type": "liquid_level"}}

    events = compare_liquid_levels(before, after, package_map, {}, {})

    assert_equal(len(events), 1, "event count")
    event = events[0]
    assert_equal(event["action"], "partial_take_out", "action")
    assert_equal(event["review_status"], "needs_review", "review_status")
    assert_equal(event["qty_estimate"], "unknown", "qty_estimate")
    assert_true("无法判断" in event["reason"], "reason explains fallback")


def test_level_order_only_allows_downward_auto_confirmation():
    assert_true(LEVEL_ORDER["full"] > LEVEL_ORDER["half"], "full above half")
    before = [{"name": "bottle", "qty_estimate": "low", "confidence": 0.9}]
    after = [{"name": "bottle", "qty_estimate": "full", "confidence": 0.9}]
    package_map = {"bottle": {"qty_type": "liquid_level"}}

    events = compare_liquid_levels(before, after, package_map, {}, {})

    assert_equal(len(events), 1, "event count")
    assert_equal(events[0]["review_status"], "needs_review", "upward change needs review")


def test_server_confirm_take_out_decrements_inventory():
    from server.app import app, get_db  # noqa: E402

    name = "测试确认取出"
    with get_db() as db:
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))
        db.execute(
            "INSERT INTO inventory(name,count,qty_type,qty_estimate) VALUES(?,?,?,?)",
            (name, 3, "count", None),
        )
        db.execute(
            "INSERT INTO events(id,action,food_name,count,review_status,qty_type,qty_estimate) "
            "VALUES(?,?,?,?,?,?,?)",
            (999901, "take_out", name, 1, "needs_review", "count", None),
        )

    client = app.test_client()
    resp = client.post("/api/edit", json={"action": "confirm_event", "event_id": 999901})
    assert_equal(resp.status_code, 200, "confirm response")

    with get_db() as db:
        item = db.execute("SELECT count FROM inventory WHERE name=?", (name,)).fetchone()
        event = db.execute("SELECT review_status FROM events WHERE id=999901").fetchone()
        assert_equal(item["count"], 2, "take_out confirmation decrements count")
        assert_equal(event["review_status"], "confirmed", "event confirmed")
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))


def test_server_confirm_partial_takeout_updates_qty_estimate_only():
    from server.app import app, get_db  # noqa: E402

    name = "测试部分取出饮品"
    with get_db() as db:
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))
        db.execute(
            "INSERT INTO inventory(name,count,qty_type,qty_estimate) VALUES(?,?,?,?)",
            (name, 1, "liquid_level", "full"),
        )
        db.execute(
            "INSERT INTO events(id,action,food_name,count,review_status,qty_type,qty_estimate,"
            "before_qty_estimate,after_qty_estimate,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (999902, "partial_take_out", name, 1, "needs_review", "liquid_level",
             "half", "full", "half", "测试液位下降"),
        )

    client = app.test_client()
    resp = client.post("/api/edit", json={"action": "confirm_event", "event_id": 999902})
    assert_equal(resp.status_code, 200, "confirm response")

    with get_db() as db:
        item = db.execute("SELECT count,qty_estimate FROM inventory WHERE name=?", (name,)).fetchone()
        event = db.execute("SELECT review_status FROM events WHERE id=999902").fetchone()
        assert_equal(item["count"], 1, "partial_take_out keeps count")
        assert_equal(item["qty_estimate"], "half", "partial_take_out updates level")
        assert_equal(event["review_status"], "confirmed", "event confirmed")
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))


def test_server_correct_partial_event_marks_corrected():
    from server.app import app, get_db  # noqa: E402

    name = "测试人工修正饮品"
    with get_db() as db:
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))
        db.execute(
            "INSERT INTO inventory(name,count,qty_type,qty_estimate) VALUES(?,?,?,?)",
            (name, 1, "liquid_level", "full"),
        )
        db.execute(
            "INSERT INTO events(id,action,food_name,count,review_status,qty_type,qty_estimate,"
            "before_qty_estimate,after_qty_estimate,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (999903, "partial_take_out", name, 1, "needs_review", "liquid_level",
             "unknown", "full", "unknown", "测试需要人工确认"),
        )

    client = app.test_client()
    resp = client.post(
        "/api/edit",
        json={"action": "correct_partial_event", "event_id": 999903, "qty_estimate": "low"},
    )
    assert_equal(resp.status_code, 200, "correct response")

    with get_db() as db:
        item = db.execute("SELECT count,qty_estimate FROM inventory WHERE name=?", (name,)).fetchone()
        event = db.execute("SELECT review_status,qty_estimate,after_qty_estimate FROM events WHERE id=999903").fetchone()
        assert_equal(item["count"], 1, "manual partial correction keeps count")
        assert_equal(item["qty_estimate"], "low", "manual partial correction updates inventory")
        assert_equal(event["review_status"], "corrected", "event marked corrected")
        assert_equal(event["qty_estimate"], "low", "event qty_estimate updated")
        assert_equal(event["after_qty_estimate"], "low", "event after level updated")
        db.execute("DELETE FROM inventory WHERE name=?", (name,))
        db.execute("DELETE FROM events WHERE food_name=?", (name,))


if __name__ == "__main__":
    tests = [
        test_parse_detection_details_keeps_bbox_frame_and_level,
        test_build_count_map_matches_existing_parse_detections_behavior,
        test_detail_level_estimates_level_from_image_bbox,
        test_compare_liquid_levels_uses_image_bbox_when_no_level_field,
        test_compare_liquid_levels_generates_partial_take_out,
        test_compare_liquid_levels_missing_after_level_needs_review,
        test_level_order_only_allows_downward_auto_confirmation,
        test_server_confirm_take_out_decrements_inventory,
        test_server_confirm_partial_takeout_updates_qty_estimate_only,
        test_server_correct_partial_event_marks_corrected,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
            passed += 1
        except Exception as exc:
            print(f"FAIL {test.__name__}: {exc}")
            raise
    print(f"{passed}/{len(tests)} partial takeout tests passed")
