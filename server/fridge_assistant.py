"""SmartFridge RAG assistant.

This module keeps the AI feature usable on an edge/server runtime even when
DeepSeek API credentials or LangChain packages are not installed yet. When the
AI stack is ready, it uses LangChain + DeepSeek with retrieved local diet
knowledge; otherwise it falls back to deterministic inventory-based advice.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_PATH = BASE_DIR / "rag" / "healthy_diet_knowledge.md"

DEFAULT_QUESTION = "请根据当前冰箱库存推荐健康饮食。"
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# 库存数量/状态的中文标签映射，用于UI展示
QTY_LABELS = {
    "full": "几乎满",
    "three_quarters": "约3/4",
    "half": "约一半",
    "low": "少量",
    "empty": "几乎空",
    "unknown": "无法判断",
    "needs_manual": "需人工确认",
    "put_in": "有新增",
    "take_out": "有取出",
}

# 食材分组关键词，用于根据食材名称/分类判断其所属的食物组
FOOD_GROUP_KEYWORDS = {
    "vegetables": ["菜", "西兰花", "胡萝卜", "番茄", "黄瓜", "生菜", "菠菜", "白菜", "蘑菇", "蔬菜"],
    "fruit": ["苹果", "香蕉", "橙", "葡萄", "西瓜", "草莓", "梨", "水果"],
    "protein": ["鸡蛋", "蛋", "鸡肉", "牛肉", "鱼", "虾", "豆腐", "豆", "奶酪", "肉"],
    "dairy": ["牛奶", "酸奶", "奶酪", "芝士", "乳", "奶"],
    "staple": ["面包", "米饭", "面条", "馒头", "燕麦", "土豆", "玉米", "三明治", "披萨"],
    "drink": ["饮品", "饮料", "果汁", "牛奶", "水", "酸奶", "瓶装"],
    "snack": ["蛋糕", "甜甜圈", "巧克力", "饼干", "披萨", "热狗", "薯片", "甜"],
}

# 系统级提示词，定义AI助手的角色定位和行为约束
SYSTEM_PROMPT = """你是 SmartFridge 智能冰箱助手，负责根据冰箱实时库存给用户推荐健康饮食。
请严格遵守：
1. 优先使用当前库存，不要编造冰箱里没有的食材。
2. 如果库存不足，可以明确说明还缺少什么，并给出可替代方案。
3. 结合 RAG 检索到的健康饮食知识，给出早餐、午餐、晚餐或加餐建议。
4. 关注食材数量、液位和剩余状态，例如牛奶只有约一半时不要安排多人份。
5. 回答要简洁、中文、适合普通家庭执行，必要时提醒这不是医疗诊断。
6. 使用 Markdown，结构包含"推荐搭配""为什么这样搭配""注意事项"。
"""


@dataclass
class RagChunk:
    title: str
    content: str
    score: int = 0


def _safe_str(value: Any, default: str = "") -> str:
    """安全地将任意值转为字符串，None或空串返回default。"""
    if value is None:
        return default
    return str(value).strip()


def _safe_int(value: Any, default: int = 1) -> int:
    """安全地将任意值转为整数，转换失败时返回default。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def qty_label(value: Any) -> str:
    """将qty_estimate值转换为可读的中文标签。"""
    text = _safe_str(value)
    return QTY_LABELS.get(text, text or "未知")


def compact_inventory(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从完整库存记录中提取助手和前端必需的关键字段，返回精简列表。

    丢弃原始记录中的冗余字段（原始数据中可能包含传感器数据、
    详细历史等），只保留名称、数量、分类、数量估算方式和更新时间。
    """
    compact: list[dict[str, Any]] = []
    for item in inventory or []:
        name = _safe_str(item.get("name"))
        if not name:
            continue
        compact.append({
            "name": name,
            "count": max(_safe_int(item.get("count"), 1), 0),
            "category": _safe_str(item.get("category")),
            "category_l2": _safe_str(item.get("category_l2")),
            "qty_type": _safe_str(item.get("qty_type"), "count") or "count",
            "qty_estimate": _safe_str(item.get("qty_estimate")),
            "last_updated": _safe_str(item.get("last_updated") or item.get("first_seen")),
        })
    return compact


def _quantity_text(item: dict[str, Any]) -> str:
    """将单个库存项的数量信息格式化为可读文本。

    根据qty_type决定格式化方式：
    - count: 显示件数
    - liquid_level: 显示液位估算
    - packed: 显示包装状态
    """
    qty_type = item.get("qty_type") or "count"
    if qty_type == "count":
        return f"{item.get('count', 1)} 件"
    estimate = qty_label(item.get("qty_estimate"))
    if qty_type == "liquid_level":
        return f"液位 {estimate}"
    if qty_type == "packed":
        return f"包装状态 {estimate}"
    return f"状态 {estimate}"


def format_inventory_context(inventory: list[dict[str, Any]]) -> str:
    """将完整库存列表格式化为AIPrompt中使用的自然语言上下文。

    输出形如：
    当前冰箱库存：
    1. 牛奶：液位 约一半，类别：乳制品，更新时间：2024-01-01 10:00
    2. 鸡蛋：5 件，类别：蛋白质，更新时间：2024-01-01 09:30
    """
    items = compact_inventory(inventory)
    if not items:
        return "当前冰箱库存为空或尚未同步。"

    lines = ["当前冰箱库存："]
    for idx, item in enumerate(items, 1):
        # 拼接一级和二级分类，忽略空值
        category = " / ".join(part for part in [item.get("category"), item.get("category_l2")] if part)
        category_text = f"，类别：{category}" if category else ""
        updated = f"，更新时间：{item['last_updated']}" if item.get("last_updated") else ""
        lines.append(f"{idx}. {item['name']}：{_quantity_text(item)}{category_text}{updated}")
    return "\n".join(lines)


def load_knowledge_base(path: Path | None = None) -> str:
    """加载本地健康饮食知识库文件。

    若文件不存在（边缘设备首次部署时正常），返回内置的通用饮食建议
    作为降级内容，保证系统仍可提供基础的健康提示。
    """
    kb_path = path or KNOWLEDGE_PATH
    try:
        text = kb_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return """
## 通用健康饮食
每餐尽量包含优质蛋白、蔬菜水果和适量主食。优先使用新鲜食材，减少高糖高油零食。
## 液体与乳制品
牛奶、酸奶适合早餐或加餐。若液位较低，应按少量食材规划，不要安排多人份。
""".strip()
    return text.strip()


def _split_knowledge_with_langchain(text: str) -> list[str]:
    """使用LangChain的RecursiveCharacterTextSplitter对知识库文本进行分块。

    按段落标题（##）、句子（。；）等语义边界切分，chunk_size=420保证
    单块内容足够简短以适应Prompt上下文限制，chunk_overlap=60保证块间上下文连续。
    若LangChain未安装则返回空列表，触发备用正则分块逻辑。
    """
    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
    except Exception:
        return []

    splitter = RecursiveCharacterTextSplitter(
        separators=["\n## ", "\n### ", "\n", "。", "；"],
        chunk_size=420,
        chunk_overlap=60,
    )
    return [chunk.strip() for chunk in splitter.split_text(text) if chunk.strip()]


def split_knowledge(text: str) -> list[RagChunk]:
    """将知识库文本拆分为多个RAG块。

    优先尝试LangChain分块（语义更优），若不可用则按 ## 标题用正则拆分。
    每个块提取首行作为title（最大40字符），其余内容作为content。
    """
    if not text.strip():
        return []

    chunks = _split_knowledge_with_langchain(text)
    if not chunks:
        # 按 Markdown 二级标题拆分，无标题时整段为一块
        sections = re.split(r"\n(?=##\s+)", text)
        chunks = [section.strip() for section in sections if section.strip()]

    result: list[RagChunk] = []
    for chunk in chunks:
        first_line = chunk.splitlines()[0].strip("# ").strip() if chunk.splitlines() else "健康饮食知识"
        result.append(RagChunk(title=first_line[:40] or "健康饮食知识", content=chunk))
    return result


def _query_tokens(question: str, inventory: list[dict[str, Any]]) -> set[str]:
    """从用户问题和库存信息中提取检索关键词（分词）。

    提取中文、英文、数字和下划线组成的≥2字符的词作为候选token，
    再根据FOOD_GROUP_KEYWORDS判断是否命中有意义的食物组关键词，
    有则将组名（如"vegetables"）加入token集，用于后续知识块打分。
    """
    text = question or DEFAULT_QUESTION
    for item in compact_inventory(inventory):
        text += " " + " ".join([
            item.get("name", ""), item.get("category", ""), item.get("category_l2", ""), item.get("qty_estimate", ""),
        ])
    raw_tokens = re.findall(r"[一-龥A-Za-z0-9_]+", text.lower())
    # 过滤短token（大多是单字无意义词）
    tokens = {token for token in raw_tokens if len(token) >= 2}
    # 额外检测食物组关键词
    for key, values in FOOD_GROUP_KEYWORDS.items():
        if any(word in text for word in values):
            tokens.add(key)
    return tokens


def retrieve_knowledge(question: str, inventory: list[dict[str, Any]], top_k: int = 4) -> list[RagChunk]:
    """基于用户问题和当前库存，从知识库中检索最相关的top_k个知识块。

    检索策略：
    1. 将知识库分块后，对每个块计算与query_tokens的token命中数作为score。
    2. 标题含"通用"或内容含"均衡"的块获得+1分（通用知识优先）。
    3. 按score降序排列，返回前top_k个命中的块；若无命中块则返回前top_k个（保证有知识可用）。
    """
    chunks = split_knowledge(load_knowledge_base())
    if not chunks:
        return []

    tokens = _query_tokens(question, inventory)
    scored: list[RagChunk] = []
    for chunk in chunks:
        haystack = (chunk.title + "\n" + chunk.content).lower()
        score = sum(1 for token in tokens if token and token in haystack)
        if "通用" in chunk.title or "均衡" in chunk.content:
            score += 1
        scored.append(RagChunk(title=chunk.title, content=chunk.content, score=score))

    scored.sort(key=lambda item: item.score, reverse=True)
    selected = [chunk for chunk in scored if chunk.score > 0][:top_k]
    return selected or scored[: min(top_k, len(scored))]


def _classify_item(item: dict[str, Any]) -> str:
    """根据食材的名称和分类信息判断其所属的食物组。

    遍历FOOD_GROUP_KEYWORDS，若任意关键词出现在名称或分类字段中，
    则判定该食材属于对应组。若均不匹配返回"other"（如调味品等）。
    """
    text = " ".join([
        item.get("name", ""), item.get("category", ""), item.get("category_l2", ""), item.get("qty_type", ""),
    ])
    for group, keywords in FOOD_GROUP_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return group
    return "other"


def _names(items: list[dict[str, Any]], group: str) -> list[str]:
    """返回指定食物组中所有食材的名称列表。"""
    return [item["name"] for item in items if _classify_item(item) == group]


def _first_available(groups: list[list[str]]) -> str:
    """从多个食物组列表中返回第一个非空列表的前3个食材（用、连接）。

    用于优先使用最佳可用食材，例如早餐优先乳制品→主食→水果，
    若某组为空则自动降级到下一组。
    """
    for group in groups:
        if group:
            return "、".join(group[:3])
    return ""


def generate_fallback_recommendation(inventory: list[dict[str, Any]], question: str = DEFAULT_QUESTION) -> str:
    """当LLM不可用时，基于规则的库存分析生成健康饮食建议。

    逻辑：
    1. 将库存按食物组分类（蔬菜、水果、蛋白质、乳制品、主食、零食）。
    2. 分别构建早餐（乳制品+主食+水果）和午/晚餐（蛋白质+蔬菜+主食）的推荐。
    3. 检测液位类食材，生成用量提醒。
    4. 检测营养缺口（如缺少蔬菜、蛋白质），生成补充建议。
    5. 若有零食，提醒其不适合代替正餐。
    返回格式化的Markdown文本。
    """
    items = compact_inventory(inventory)
    if not items:
        return (
            "**当前还没有可用库存。**\n\n"
            "请先同步或手动添加食材，我就能根据库存推荐早餐、午餐和晚餐。"
            "临时建议是：一餐尽量包含蔬菜、优质蛋白和适量主食，少用高糖零食代替正餐。"
        )

    # 按食物组分类提取食材名称
    vegetables = _names(items, "vegetables")
    fruits = _names(items, "fruit")
    proteins = _names(items, "protein")
    dairy = _names(items, "dairy")
    staples = _names(items, "staple")
    snacks = _names(items, "snack")

    # 构建早餐推荐：优先乳制品，再主食，再水果
    breakfast_parts = []
    breakfast_dairy = _first_available([dairy])
    if breakfast_dairy:
        breakfast_parts.append(breakfast_dairy)
    breakfast_staple = _first_available([staples])
    if breakfast_staple:
        breakfast_parts.append(breakfast_staple)
    breakfast_fruit = _first_available([fruits])
    if breakfast_fruit:
        breakfast_parts.append(breakfast_fruit)

    # 构建午/晚餐推荐：优先蛋白质，再蔬菜，再主食
    meal_parts = []
    meal_protein = _first_available([proteins, dairy])
    if meal_protein:
        meal_parts.append(meal_protein)
    meal_veg = _first_available([vegetables])
    if meal_veg:
        meal_parts.append(meal_veg)
    meal_staple = _first_available([staples])
    if meal_staple:
        meal_parts.append(meal_staple)

    # 检测液位类食材，生成用量提醒（如牛奶只剩一半）
    liquid_notes = []
    for item in items:
        if item.get("qty_type") == "liquid_level":
            liquid_notes.append(f"{item['name']}目前{qty_label(item.get('qty_estimate'))}，建议按实际剩余量安排。")

    # 检测营养缺口
    gaps = []
    if not vegetables:
        gaps.append("蔬菜偏少，后续可以补充绿叶菜或菌菇类。")
    if not proteins and not dairy:
        gaps.append("蛋白质食材偏少，后续可以补充鸡蛋、豆腐、鱼肉或瘦肉。")
    if snacks:
        gaps.append(f"{', '.join(snacks[:3])} 更适合少量加餐，不建议代替正餐。")

    # 若无特别提醒，添加通用健康提示
    breakfast = " + ".join(breakfast_parts) if breakfast_parts else "从现有食材中选择一份主食，再搭配蛋白质食材"
    main_meal = " + ".join(meal_parts) if meal_parts else "优先补齐蛋白质、蔬菜和主食后再安排正餐"
    snack = _first_available([fruits, dairy]) or "少量坚果或低糖酸奶"

    notes = liquid_notes + gaps
    if not notes:
        notes.append("当前库存结构比较适合做轻量健康餐，注意控制油盐和甜食摄入。")

    return (
        "**推荐搭配**\n"
        f"1. 早餐：{breakfast}。\n"
        f"2. 午餐/晚餐：{main_meal}。\n"
        f"3. 加餐：{snack}，优先选择水果或乳制品。\n\n"
        "**为什么这样搭配**\n"
        "这样能尽量覆盖蛋白质、膳食纤维和能量来源，比只吃零食或单一主食更稳定。\n\n"
        "**注意事项**\n"
        + "\n".join(f"- {note}" for note in notes)
    )


def build_prompt(inventory: list[dict[str, Any]], question: str, rag_chunks: list[RagChunk]) -> str:
    """组装发送给LLM的完整Prompt。

    包含用户原始问题、格式化后的库存上下文、以及RAG检索到的相关知识片段。
    知识片段以【标题】格式包裹，便于LLM理解知识来源和主题。
    """
    inventory_context = format_inventory_context(inventory)
    rag_context = "\n\n".join(f"【{chunk.title}】\n{chunk.content}" for chunk in rag_chunks) or "暂无额外知识片段。"
    return f"""用户问题：{question or DEFAULT_QUESTION}

{inventory_context}

RAG 检索知识：
{rag_context}

请基于库存和知识库直接给出可执行的健康饮食建议。"""


def _trim_history(history: list[dict[str, str]] | None, max_messages: int = 8) -> list[dict[str, str]]:
    """清洗并截断对话历史，保留最近max_messages条 user/assistant 消息。

    每条消息内容截断至1000字符以避免Prompt超长。仅保留role为user或assistant的记录，
    过滤掉system等特殊角色。
    """
    clean: list[dict[str, str]] = []
    for item in history or []:
        role = item.get("role")
        content = _safe_str(item.get("content"))
        if role in {"user", "assistant"} and content:
            clean.append({"role": role, "content": content[:1000]})
    return clean[-max_messages:]


def ask_deepseek_with_langchain(
    inventory: list[dict[str, Any]],
    question: str,
    history: list[dict[str, str]] | None = None,
) -> tuple[str | None, str, list[RagChunk]]:
    """使用LangChain调用DeepSeek API生成饮食建议。

    流程：
    1. 若未配置DEEPSEEK_API_KEY，直接返回None触发降级。
    2. 导入LangChain依赖，失败时捕获异常并返回降级原因。
    3. 构建ChatOpenAI实例（兼容新版和旧版API参数）。
    4. 组装SystemMessage + 对话历史 + 用户Prompt，调用LLM。
    5. 解析响应内容并返回。

    Returns:
        (answer, reason, rag_chunks)
        - answer: LLM生成的文本，无则None
        - reason: 降级原因或错误信息
        - rag_chunks: 检索到的知识块（即使LLM调用失败也返回，供降级逻辑使用）
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY", "sk-817c136b7cf24e1a8a463c9041eab75d").strip()
    rag_chunks = retrieve_knowledge(question, inventory)
    if not api_key:
        return None, "未配置 DEEPSEEK_API_KEY，已使用本地规则建议。", rag_chunks

    try:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from langchain_openai import ChatOpenAI
    except Exception as exc:
        return None, f"LangChain 依赖未安装或不可用：{type(exc).__name__}。", rag_chunks

    try:
        # 尝试新版API参数，不支持则降级到旧版参数
        try:
            llm = ChatOpenAI(
                model=DEEPSEEK_MODEL,
                api_key=api_key,
                base_url=DEEPSEEK_BASE_URL,
                temperature=0.35,
                timeout=30,
            )
        except TypeError:
            llm = ChatOpenAI(
                model_name=DEEPSEEK_MODEL,
                openai_api_key=api_key,
                openai_api_base=DEEPSEEK_BASE_URL,
                temperature=0.35,
                request_timeout=30,
            )

        # 组装消息列表：SystemMessage定义角色，History提供上下文，HumanMessage是当前问题
        messages: list[Any] = [SystemMessage(content=SYSTEM_PROMPT)]
        for item in _trim_history(history):
            if item["role"] == "user":
                messages.append(HumanMessage(content=item["content"]))
            else:
                messages.append(AIMessage(content=item["content"]))
        messages.append(HumanMessage(content=build_prompt(inventory, question, rag_chunks)))

        response = llm.invoke(messages)
        content = getattr(response, "content", response)
        # 兼容AIMessage返回list类型（如多模态内容）
        if isinstance(content, list):
            content = "\n".join(str(part) for part in content)
        return _safe_str(content), "", rag_chunks
    except Exception as exc:
        return None, f"DeepSeek 调用失败：{type(exc).__name__}。", rag_chunks


def answer_fridge_question(
    inventory: list[dict[str, Any]],
    question: str = DEFAULT_QUESTION,
    history: list[dict[str, str]] | None = None,
    use_llm: bool = True,
) -> dict[str, Any]:
    """SmartFridge助手的统一入口，根据条件选择LLM或规则引擎生成回答。

    策略：
    - use_llm=True 时优先尝试DeepSeek LLM+RAG；
    - LLM调用失败或use_llm=False 时降级到本地规则生成建议。

    Returns:
        包含以下键的字典：
        - ok: 是否成功生成
        - answer: 生成的饮食建议（Markdown格式）
        - source: 答案来源，"deepseek_langchain_rag" 或 "fallback_rules"
        - model: 使用的模型名称
        - fallback_reason: 降级原因（仅在source为fallback_rules时有值）
        - inventory_used: 本次使用的精简库存数据
        - rag_sources: RAG检索到的知识块标题和分数列表
    """
    user_question = _safe_str(question) or DEFAULT_QUESTION
    rag_chunks = retrieve_knowledge(user_question, inventory)
    answer = None
    reason = ""

    if use_llm:
        answer, reason, rag_chunks = ask_deepseek_with_langchain(inventory, user_question, history)

    source = "deepseek_langchain_rag" if answer else "fallback_rules"
    if not answer:
        answer = generate_fallback_recommendation(inventory, user_question)

    return {
        "ok": True,
        "answer": answer,
        "source": source,
        "model": DEEPSEEK_MODEL if source == "deepseek_langchain_rag" else "local-rules",
        "fallback_reason": reason,
        "inventory_used": compact_inventory(inventory),
        "rag_sources": [{"title": chunk.title, "score": chunk.score} for chunk in rag_chunks],
    }
