#!/usr/bin/env python3
"""SmartFridge assistant tests without network or hardware."""
import os
import sys

PROJECT = "/home/jing/my-project/smartfridge"
os.chdir(PROJECT)
sys.path.insert(0, os.path.join(PROJECT, "server"))
os.environ["DEEPSEEK_API_KEY"] = ""

from fridge_assistant import (  # noqa: E402
    answer_fridge_question,
    format_inventory_context,
    generate_fallback_recommendation,
    retrieve_knowledge,
)

TESTS = []


def test(name):
    def deco(fn):
        TESTS.append((name, fn))
        return fn
    return deco


def sample_inventory():
    return [
        {"name": "牛奶", "count": 1, "category": "饮品", "category_l2": "乳制品", "qty_type": "liquid_level", "qty_estimate": "half"},
        {"name": "鸡蛋", "count": 3, "category": "蛋白质", "qty_type": "count"},
        {"name": "苹果", "count": 2, "category": "水果", "qty_type": "count"},
        {"name": "面包", "count": 1, "category": "主食", "qty_type": "count"},
    ]


@test("库存上下文包含液位与分类")
def _():
    text = format_inventory_context(sample_inventory())
    return "牛奶" in text and "液位 约一半" in text and "乳制品" in text


@test("RAG 能检索到饮食知识")
def _():
    chunks = retrieve_knowledge("牛奶只剩一半，早餐怎么吃？", sample_inventory())
    titles = " ".join(chunk.title for chunk in chunks)
    return bool(chunks) and ("早餐" in titles or "液体" in titles or "均衡" in titles)


@test("离线推荐可用且引用库存")
def _():
    answer = generate_fallback_recommendation(sample_inventory())
    return "推荐搭配" in answer and "牛奶" in answer and "鸡蛋" in answer


@test("总入口无 API Key 时回退本地规则")
def _():
    result = answer_fridge_question(sample_inventory(), "帮我推荐今天早餐", use_llm=False)
    return result["ok"] is True and result["source"] == "fallback_rules" and result["inventory_used"][0]["name"] == "牛奶"


if __name__ == "__main__":
    passed = 0
    for name, fn in TESTS:
        try:
            ok = bool(fn())
        except Exception as exc:
            ok = False
            print(f"  ✗ {name}: {exc}")
        else:
            print(f"  {'✓' if ok else '✗'} {name}")
        passed += 1 if ok else 0
    print(f"\n汇总: {passed}/{len(TESTS)} 通过")
    raise SystemExit(0 if passed == len(TESTS) else 1)
