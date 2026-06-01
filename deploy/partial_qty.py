"""
SmartFridge 部分取出量辅助模块。

本模块刻意不依赖硬件，因此可以在 PC/WSL 环境下进行测试。
板级运行时代码可以导入这些辅助函数，而无需引入 GPIO、Flask 或摄像头等硬件依赖。
"""
import json
import os


# 液位等级数值定义，用于计算液位变化量
# 数值越大表示液位越高，delta = before_level - after_level 即为液位下降量
LEVEL_ORDER = {
    "empty": 0,
    "low": 1,
    "half": 2,
    "three_quarters": 3,
    "full": 4,
}

# 默认液位阈值配置，按 min_ratio 阈值从高到低排列
# 优先级：ratio >= 0.80 → full，>= 0.60 → three_quarters，以此类推
DEFAULT_LEVELS = [
    {"name": "full", "min_ratio": 0.80},
    {"name": "three_quarters", "min_ratio": 0.60},
    {"name": "half", "min_ratio": 0.35},
    {"name": "low", "min_ratio": 0.10},
    {"name": "empty", "min_ratio": 0.00},
]


def normalize_level(value):
    """
    将各种格式的液位值归一化为标准等级名称。

    支持别名映射（如 "almost_full" → "full"），并过滤无效值返回 "unknown"。
    如果值无法识别或为空，均返回 "unknown"。

    Args:
        value: 原始液位值（字符串、数字或 None）

    Returns:
        str: 标准液位名称（"empty"/"low"/"half"/"three_quarters"/"full"），
             无法识别时返回 "unknown"
    """
    if value is None:
        return "unknown"
    level = str(value).strip()
    if not level:
        return "unknown"

    # 别名映射表：将各种表达方式统一为标准名称
    aliases = {
        "almost_full": "full",
        "3/4": "three_quarters",
        "quarter": "low",
        "needs_manual": "unknown",
        "需人工确认": "unknown",
    }
    level = aliases.get(level, level)
    return level if level in LEVEL_ORDER else "unknown"


def ratio_to_level(ratio, levels=None):
    """
    将 0-1 的液位比例值转换为液位等级名称。

    根据 levels 列表中的 min_ratio 阈值，按顺序找到第一个满足条件的等级。

    Args:
        ratio: 液位比例值（0.0-1.0），None 或无效值返回 "unknown"
        levels: 可选的自定义液位配置列表，格式同 DEFAULT_LEVELS；
                传入 None 则使用 DEFAULT_LEVELS

    Returns:
        str: 对应的液位等级名称，未找到匹配则返回 "unknown"
    """
    if ratio is None:
        return "unknown"
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        return "unknown"

    # 遍历 levels 列表，返回第一个 ratio >= min_ratio 的等级
    for item in levels or DEFAULT_LEVELS:
        if r >= float(item.get("min_ratio", 0)):
            return item.get("name", "unknown")
    return "unknown"


def parse_detection_details_from_file(det_file):
    """
    从冰箱 AI 检测结果 JSON 文件中读取检测详情。

    支持两种格式：
    - 旧格式：仅包含 {name, confidence}
    - 新格式：包含 bbox/frame_path/qty_estimate 等扩展字段

    注意：人员检测 (name == "person") 会被过滤排除，与 parse_detections() 行为保持一致。

    Args:
        det_file: JSON 文件路径，文件不存在或解析失败时返回空列表

    Returns:
        list[dict]: 检测详情列表，每项包含 name、confidence 及可选的
                   bbox、frame_path、qty_estimate、level、ratio 字段
    """
    try:
        if not det_file or not os.path.exists(det_file):
            return []
        with open(det_file) as f:
            payload = json.load(f)
    except Exception:
        return []

    # 记录帧路径作为默认值（当单个检测无 frame_path 时使用）
    root_frame_path = payload.get("frame_path")
    details = []

    for det in payload.get("detections", []):
        name = det.get("name", "unknown")

        # 过滤人员检测
        if name == "person":
            continue

        detail = {
            "name": name,
            "confidence": det.get("confidence"),
        }

        # 提取 bbox：优先使用 bbox 字段，否则尝试从 x1/y1/x2/y2 组合
        if det.get("bbox") is not None:
            detail["bbox"] = det.get("bbox")
        elif all(k in det for k in ("x1", "y1", "x2", "y2")):
            detail["bbox"] = [det.get("x1"), det.get("y1"), det.get("x2"), det.get("y2")]

        # 提取 frame_path：优先用检测自己的路径，否则用帧级默认路径
        frame_path = det.get("frame_path") or root_frame_path
        if frame_path:
            detail["frame_path"] = frame_path

        # 提取液位相关信息（优先级：qty_estimate > level > ratio）
        for key in ("qty_estimate", "level", "ratio"):
            if det.get(key) is not None:
                detail[key] = det.get(key)

        details.append(detail)

    return details


def build_count_map(details):
    """
    将检测详情列表聚合为 {物品名: 数量} 的统计字典。

    统计时自动过滤 person 类型（与 parse_detection_details_from_file 行为一致）。

    Args:
        details: 检测详情列表，每项需包含 name 字段

    Returns:
        dict: 物品名到检测数量的映射，例如 {"milk": 2, "juice": 1}
    """
    counts = {}
    for det in details or []:
        name = det.get("name", "unknown")
        if name == "person":
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def detail_level(detail, liquid_config=None):
    """
    从单条检测详情中提取液位等级信息。

    液位来源优先级：qty_estimate > level > ratio。
    如果检测详情中缺少液位字段（仅有 bbox/frame_path），返回 "unknown"——
    此时需要后续图像处理补充实现。

    Args:
        detail: 单条检测详情字典，可能包含 qty_estimate/level/ratio/confidence/bbox/frame_path
        liquid_config: 可选的液位配置（会覆盖默认阈值），格式为 {"levels": [...], "min_confidence": 0.65}

    Returns:
        dict: 包含以下键的液位信息字典：
              - level (str): 液位等级名称
              - confidence (float): 置信度，0.0-1.0
              - reason (str): 液位判断结果说明（用于调试/日志）
    """
    if not detail:
        return {
            "level": "unknown",
            "confidence": 0.0,
            "reason": "缺少检测详情，无法判断液位",
        }

    # 按优先级从检测详情中提取液位
    if detail.get("qty_estimate") is not None:
        level = normalize_level(detail.get("qty_estimate"))
    elif detail.get("level") is not None:
        level = normalize_level(detail.get("level"))
    elif detail.get("ratio") is not None:
        # ratio 方式需要配合液位配置转换（使用自定义 levels 或默认）
        levels = (liquid_config or {}).get("levels") if liquid_config else None
        level = ratio_to_level(detail.get("ratio"), levels)
    else:
        level = "unknown"

    # 确保 confidence 为有效浮点数
    conf = detail.get("confidence")
    try:
        conf = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0

    # 当液位无法判断时，提供详细原因（帮助人工复核）
    if level == "unknown":
        if not detail.get("frame_path"):
            reason = "缺少 frame_path，无法判断液位"
        elif not detail.get("bbox"):
            reason = "缺少 bbox，无法判断液位"
        else:
            reason = "无法判断液位，需要人工确认"
        return {"level": "unknown", "confidence": conf, "reason": reason}

    return {"level": level, "confidence": conf, "reason": f"检测到液位状态 {level}"}


def select_best_detail(details, name):
    """
    从多条检测详情中选出置信度最高的指定物品检测结果。

    用于当同一物品被多次检测时，选取最可信的那条。

    Args:
        details: 检测详情列表
        name: 要筛选的物品名称

    Returns:
        dict 或 None: 置信度最高的检测详情，未找到匹配时返回 None
    """
    candidates = [d for d in (details or []) if d.get("name") == name]
    if not candidates:
        return None

    def score(detail):
        try:
            return float(detail.get("confidence") or 0)
        except (TypeError, ValueError):
            return 0.0

    # 按置信度降序排序，返回第一个（最高分）
    return sorted(candidates, key=score, reverse=True)[0]


def is_liquid_level_item(name, package_map):
    """
    判断某个物品是否为液位类型（而非计件类型）。

    液位类型物品通过 qty_type == "liquid_level" 区分，需要特别的液位变化检测逻辑。

    Args:
        name: 物品名称（item_key）
        package_map: 物品配置字典，格式为 {物品名: {"qty_type": "liquid_level"|"count"|...}}

    Returns:
        bool: 是液位类型返回 True，否则返回 False
    """
    info = (package_map or {}).get(name, {})
    return info.get("qty_type") == "liquid_level"


def compare_liquid_levels(
    before_details,
    after_details,
    package_map,
    display_map=None,
    category_map=None,
    liquid_config=None,
):
    """
    比较前后两次检测中液位类型物品的液位变化。

    用于检测部分取出场景（如倒出一半牛奶），当物品数量未变但液位下降时，
    生成 partial_take_out 事件。只有置信度足够高且液位下降足够明显的变化
    才会标记为 confirmed，其余标记为 needs_review。

    配置参数（来自 liquid_config）：
    - min_confidence: 判定为确认的最低置信度阈值（默认 0.65）
    - min_level_delta: 判定为确认的最小液位等级差（默认 1，即至少降一个等级）

    Args:
        before_details: 前一次检测的详情列表
        after_details: 后一次检测的详情列表
        package_map: 物品配置映射，用于判断哪些是液位类型物品
        display_map: 可选，物品显示名称映射（item_key → display_name）
        category_map: 可选，物品分类映射（item_key → {c1, c2}）
        liquid_config: 可选，液位检测配置，格式为 {"min_confidence": 0.65, "min_level_delta": 1}

    Returns:
        list[dict]: 液位变化事件列表，每项格式为：
            {
              "action": "partial_take_out",
              "food_name": str,          # 显示名称
              "count": 1,
              "review_status": str,      # "confirmed" 或 "needs_review"
              "confidence": float,
              "category": str,           # 一级分类
              "category_l2": str,        # 二级分类
              "item_key": str,
              "qty_type": "liquid_level",
              "before_qty_estimate": str, # 变化前液位
              "after_qty_estimate": str,  # 变化后液位
              "qty_estimate": str,        # 最终液位
              "reason": str              # 变化描述
            }
    """
    display_map = display_map or {}
    category_map = category_map or {}
    liquid_config = liquid_config or {}

    # 读取液位检测阈值配置
    min_conf = float(liquid_config.get("min_confidence", 0.65))
    min_delta = int(liquid_config.get("min_level_delta", 1))

    # 收集所有液位类型物品的名称
    names = set()
    for detail in before_details or []:
        if is_liquid_level_item(detail.get("name"), package_map):
            names.add(detail.get("name"))
    for detail in after_details or []:
        if is_liquid_level_item(detail.get("name"), package_map):
            names.add(detail.get("name"))

    events = []
    for name in sorted(names):
        # 选取前后两次检测中该物品置信度最高的结果
        before = detail_level(select_best_detail(before_details, name), liquid_config)
        after = detail_level(select_best_detail(after_details, name), liquid_config)

        before_level = before["level"]
        after_level = after["level"]

        # 液位变化的置信度取两次检测的较低值
        confidence = min(before.get("confidence", 0.0), after.get("confidence", 0.0))

        cat = category_map.get(name, {})

        review_status = "needs_review"
        reason = "液位无法判断，需要人工确认"

        # 仅当液位等级可比较时才计算 delta
        if before_level in LEVEL_ORDER and after_level in LEVEL_ORDER:
            delta = LEVEL_ORDER[before_level] - LEVEL_ORDER[after_level]

            if delta >= min_delta and confidence >= min_conf:
                # 液位明显下降且置信度足够 → 确认的部分取出
                review_status = "confirmed"
                reason = f"液位从 {before_level} 下降到 {after_level}"
            elif delta > 0:
                # 液位下降但置信度或幅度不足 → 待复核
                reason = f"液位从 {before_level} 下降到 {after_level}，但置信度不足"
            elif delta < 0:
                # 液位上升（不合常理）→ 需要人工确认（可能是误检或补货）
                reason = f"液位从 {before_level} 上升到 {after_level}，需要人工确认"
            else:
                # delta == 0，液位无变化 → 不生成事件
                continue
        else:
            reason = "无法判断液位，需要人工确认"

        events.append({
            "action": "partial_take_out",
            "food_name": display_map.get(name, name),
            "count": 1,
            "review_status": review_status,
            "confidence": round(confidence, 3),
            "category": cat.get("c1", ""),
            "category_l2": cat.get("c2", ""),
            "item_key": name,
            "qty_type": "liquid_level",
            "before_qty_estimate": before_level,
            "after_qty_estimate": after_level,
            "qty_estimate": after_level,
            "reason": reason,
        })

    return events