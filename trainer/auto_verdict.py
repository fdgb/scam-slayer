#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
营销号鉴别训练 — 自动鉴别 + 结果归档模块
读取采集队列 → 调用 Knot AI 鉴别 → 写入案例库 → 更新统计
"""

import json
import os
import subprocess
import re
import time
from datetime import datetime

# ====== 配置区 ======
BASE_DIR = os.path.join(os.path.dirname(__file__), "..")
COLLECTED_DIR = os.path.join(BASE_DIR, "references", "collected_data")
CASE_LIBRARY = os.path.join(BASE_DIR, "references", "case-library.md")
STATS_FILE = os.path.join(COLLECTED_DIR, "stats.json")

# 每次最多鉴别数量（避免单次消耗太多token）
MAX_PER_RUN = 20

QUEUE_FILES = {
    "bilibili": os.path.join(COLLECTED_DIR, "bilibili_queue.json"),
    "wechat": os.path.join(COLLECTED_DIR, "wechat_queue.json"),
    "weibo": os.path.join(COLLECTED_DIR, "weibo_queue.json"),
}
# ====== 配置区结束 ======


def load_queue(filepath: str) -> list:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_queue(filepath: str, data: list):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_prompt(item: dict) -> str:
    """根据数据条目构建鉴别Prompt"""
    platform = item.get("platform", "")

    if platform == "bilibili":
        return f"""请对以下B站视频进行营销号鉴别分析，输出JSON格式结果：

标题：{item.get('title', '')}
UP主：{item.get('author', '')}（UID: {item.get('mid', '')}）
播放量：{item.get('play', 0)}
简介：{item.get('description', '')}
标签：{item.get('tag', '')}
链接：{item.get('url', '')}
搜索关键词：{item.get('search_keyword', '')}

请输出以下JSON：
{{
  "risk_level": "🔴高危/🟡存疑/🟢可信",
  "verdict": "一句话结论",
  "hit_patterns": ["命中的特征1", "命中的特征2"],
  "reason": "详细分析（2-3句）",
  "recommendation": "给老人的行动建议"
}}"""

    elif platform == "wechat":
        return f"""请对以下微信公众号文章进行营销号鉴别分析：

标题：{item.get('title', '')}
公众号：{item.get('account', '')}
摘要：{item.get('summary', '')}
发布时间：{item.get('pub_time', '')}
搜索关键词：{item.get('search_keyword', '')}

请输出以下JSON：
{{
  "risk_level": "🔴高危/🟡存疑/🟢可信",
  "verdict": "一句话结论",
  "hit_patterns": ["命中特征1", "命中特征2"],
  "reason": "详细分析（2-3句）",
  "recommendation": "给老人的行动建议"
}}"""

    elif platform == "weibo":
        return f"""请对以下微博内容进行营销号/谣言鉴别分析：

博主：{item.get('author', '')}
内容：{item.get('text_preview', '')}
转发数：{item.get('reposts', 0)} | 评论数：{item.get('comments', 0)} | 点赞：{item.get('likes', 0)}
搜索关键词：{item.get('search_keyword', '')}

请输出以下JSON：
{{
  "risk_level": "🔴高危/🟡存疑/🟢可信",
  "verdict": "一句话结论",
  "hit_patterns": ["命中特征1", "命中特征2"],
  "reason": "详细分析（2-3句）",
  "recommendation": "给老人的行动建议"
}}"""

    return ""


def call_knot_ai(prompt: str) -> dict:
    """调用 knot-cli 进行鉴别（已弃用，保留接口兼容）"""
    try:
        result = subprocess.run(
            ["knot-cli", "chat", "-p", prompt, "--model", "hunyuan-turbos-latest"],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout.strip()

        # 提取JSON
        json_match = re.search(r'\{[\s\S]*?\}', output)
        if json_match:
            return json.loads(json_match.group())
    except subprocess.TimeoutExpired:
        print("  ⚠️  AI鉴别超时")
    except Exception as e:
        print(f"  ⚠️  AI鉴别失败: {e}")
    return {}


# ====== 规则引擎 v4 ======
# 基于 case-library.md 归纳的 44+ 特征模式，无需外部 AI 即可完成初步鉴别

# 反诈/科普/辟谣关键词（命中则降级为可信）
SAFE_KEYWORDS = [
    "反诈", "防骗", "辟谣", "警惕", "诈骗", "骗局", "曝光", "提醒", "警惕",
    "警方", "公安", "派出所", "刑拘", "查处", "破获", "追回", "挽损",
    "识破", "揭秘", "不要再信", "别再信", "别上当", "别被骗", "千万别",
    "已经有人被骗", "多人被骗", "已被查处", "3·15", "3.15", "央视曝光",
    "如何辨别", "怎么识别", "教你识别", "防范方法", "防骗攻略", "防骗指南",
    "中消协", "市场监管局", "消费者协会", "投诉", "黑猫投诉",
    "不要转账", "立即报警", "保留证据", "不转账",
    "法院判决", "判刑", "刑事", "行政拘留",
    "权威辟谣", "官方通报", "国家金融监管",
    "如何防范", "守住", "别让",
]

# 高危信号关键词（命中则加分）
HIGH_RISK_KEYWORDS = {
    # 偏方/秘方类
    "治病秘方": 5, "不传秘方": 5, "奇效方": 4, "民间偏方": 5, "老祖宗留下": 4,
    "华佗秘方": 5, "偏方奇效": 5, "秘方大全": 5, "偏方大全": 5,
    "口口相传": 3, "很珍贵": 3, "最好背下": 3, "当作口诀": 3,
    # 副业卖课类
    "月入过万": 5, "月入10万": 6, "副业月入": 5, "零门槛": 5,
    "搞钱攻略": 5, "大实话": 3, "扎心的大实话": 4, "复盘": 3,
    "认命后反而": 4, "合规陷阱": 4, "说搞钱": 5,
    # 食物禁忌/健康焦虑类
    "绝对不能吃": 5, "不能吃": 3, "不能一起吃": 4, "可能会失明": 6,
    "关乎寿命": 5, "再饿也要忍住": 4, "10人吃了9人吐": 5,
    # 养生/保健品类
    "防癌食谱": 4, "癌细胞消失": 6, "量子": 4, "太赫兹": 4,
    "转发有礼": 5, "转发朋友圈": 4, "百元好礼": 4,
    # 养老金虚假类
    "中央宣布": 5, "养老金新规": 5, "全体退休人员请注意": 5,
    # AI换脸/克隆类（非科普角度）
    "5秒克隆你的声音": 3, "克隆声音诈骗": 3,
}

# 高危公众号名模式
HIGH_RISK_ACCOUNT_PATTERNS = [
    "偏方", "秘方", "奇效", "大全", "妙招", "生活馆", "搞钱",
    "日记", "变现记", "觉醒", "轻创业", "量子", "能量",
    "食疗", "养生指南", "饮食疗法", "好讲堂",
]

# 可信公众号名/账号特征
SAFE_ACCOUNT_PATTERNS = [
    "公安", "警方", "警察", "反诈", "法院", "检察", "司法",
    "央视", "人民日报", "新华社", "光明日报", "经济日报",
    "市场监管", "消协", "消费者", "疾控", "卫健委",
    "科协", "科学", "辟谣", "求真",
]


def rule_engine_verdict(item: dict) -> dict:
    """规则引擎 v4：基于案例库特征模式的评分鉴别"""
    platform = item.get("platform", "")
    title = item.get("title", "") or ""
    account = item.get("account", "") or item.get("author", "") or ""
    summary = item.get("summary", "") or item.get("description", "") or item.get("text_preview", "") or ""
    search_keyword = item.get("search_keyword", "") or ""

    full_text = f"{title} {account} {summary}"

    # === 第一步：反诈/科普/辟谣检测 ===
    safe_hits = [kw for kw in SAFE_KEYWORDS if kw in full_text]
    # 判断是否为反诈/科普文章：标题含反诈关键词 或 (标题+摘要含2个以上反诈关键词)
    is_safe_article = False
    title_safe_hits = [kw for kw in SAFE_KEYWORDS if kw in title]
    if len(title_safe_hits) >= 1 or len(safe_hits) >= 2:
        is_safe_article = True

    # 排除：标题含"月入过万"但同时在揭露骗局的文章（借力打力型）
    leverage_keywords = ["别再信", "别上当", "大实话", "扎心", "合规陷阱", "醒醒"]
    is_leverage = any(kw in title for kw in leverage_keywords) and any(
        kw in title for kw in ["月入过万", "副业", "搞钱"]
    )
    if is_leverage:
        is_safe_article = False  # 借力打力不是安全文章

    # === 第二步：高危关键词评分 ===
    score = 0
    hit_patterns = []

    for keyword, weight in HIGH_RISK_KEYWORDS.items():
        if keyword in full_text:
            score += weight
            hit_patterns.append(f"关键词「{keyword}」")

    # === 第三步：账号名模式检测 ===
    for pattern in HIGH_RISK_ACCOUNT_PATTERNS:
        if pattern in account:
            score += 3
            hit_patterns.append(f"账号名含「{pattern}」")

    # 安全账号名降分
    for pattern in SAFE_ACCOUNT_PATTERNS:
        if pattern in account:
            score -= 5
            if not is_safe_article:
                hit_patterns.append(f"官方/科普账号「{account}」")

    # === 第四步：特殊模式检测 ===
    # 养老金虚假新规模式
    if "中央宣布" in title and "养老金" in title:
        score += 5
        hit_patterns.append("伪造官方政策声明")

    # 食物禁忌+专病组合
    disease_words = ["糖尿病", "高血压", "肝病", "肾病", "痛风", "胃病"]
    if any(d in title for d in disease_words) and ("不能吃" in title or "绝对不能" in title):
        score += 4
        hit_patterns.append("专病食物禁忌")

    # 偏方数字递增模式
    import re as _re
    num_match = _re.search(r'(\d+)\s*个?秘方', title)
    if num_match and int(num_match.group(1)) >= 46:
        score += 3
        hit_patterns.append(f"偏方数字递增模式（{num_match.group(1)}个秘方）")

    # B站数据异常检测
    if platform == "bilibili":
        play = item.get("play", 0) or 0
        like = item.get("like", 0) or 0
        fav = item.get("favorite", 0) or 0
        if like > 0 and fav > like * 4:
            score += 5
            hit_patterns.append(f"收藏/点赞>{fav/max(like,1):.1f}x异常")
        if play > 100000 and like > 0 and like / play < 0.001:
            score += 4
            hit_patterns.append(f"点赞率{like/play*100:.2f}%极低")
        if not item.get("description", "").strip():
            score += 2
            hit_patterns.append("简介为空")

    # === 第五步：综合判定 ===
    if is_safe_article and score < 5:
        risk_level = "🟢可信"
        verdict_text = "反诈/科普/辟谣内容"
        reason = f"标题含反诈科普关键词（{', '.join(title_safe_hits[:3])}），非营销号内容"
        recommendation = "可信的反诈/科普信息，可转发提醒家人"
    elif score >= 8:
        risk_level = "🔴高危"
        verdict_text = "高度疑似营销号/伪科学内容"
        reason = f"命中{len(hit_patterns)}个高危特征（评分{score}）：{', '.join(hit_patterns[:5])}"
        recommendation = "高度疑似营销号，建议不转发不轻信，提醒家人注意"
    elif score >= 4:
        risk_level = "🟡存疑"
        verdict_text = "存在部分可疑特征，需进一步确认"
        reason = f"命中{len(hit_patterns)}个特征（评分{score}）：{', '.join(hit_patterns[:4])}"
        recommendation = "内容部分可疑，建议搜索核实后再判断"
    else:
        risk_level = "🟢可信"
        verdict_text = "未命中明显营销号特征"
        reason = f"评分{score}，未命中高危特征模式"
        recommendation = "暂无明显风险，但仍需保持警惕"

    return {
        "risk_level": risk_level,
        "verdict": verdict_text,
        "hit_patterns": hit_patterns,
        "reason": reason,
        "recommendation": recommendation,
    }


def deep_review_verdict(item: dict, initial_verdict: dict) -> dict:
    """深度审查二次修正：修正规则引擎的误判和漏判"""
    title = item.get("title", "") or ""
    account = item.get("account", "") or ""
    risk_level = initial_verdict["risk_level"]

    # 修正1：家庭医生/医院/正规医疗机构的偏方辟谣文章误判
    medical_safe = ["家庭医生", "人民医院", "中心医院", "健康报", "医学科普"]
    if any(kw in account for kw in medical_safe) and "偏方" in title:
        if risk_level == "🔴高危":
            initial_verdict["risk_level"] = "🟢可信"
            initial_verdict["verdict"] = "正规医疗机构偏方辟谣/科普内容"
            initial_verdict["reason"] = f"账号「{account}」为正规医疗机构，含偏方关键词但可能是辟谣内容"
            initial_verdict["recommendation"] = "正规医疗机构内容，可信度较高"
            return initial_verdict

    # 修正2：电视台/官方媒体传播偏方 → 高危（官方背书效应更危险）
    official_media = ["卫视", "电视台", "之声", "综合频道"]
    if any(kw in account for kw in official_media) and ("偏方" in title or "秘方" in title):
        initial_verdict["risk_level"] = "🔴高危"
        initial_verdict["verdict"] = "官方媒体传播偏方=官方背书效应，比普通营销号更危险"
        initial_verdict["hit_patterns"].append("官方媒体背书偏方传播")
        initial_verdict["reason"] = f"「{account}」为官方媒体，传播偏方产生背书效应，比普通营销号更危险"
        initial_verdict["recommendation"] = "即使是官方媒体传播偏方也应警惕，官方背书≠科学验证"
        return initial_verdict

    # 修正3：借力打力型 → 高危
    leverage_indicators = ["大实话", "扎心", "合规陷阱", "别再信", "醒醒"]
    has_income_claim = any(kw in title for kw in ["月入过万", "副业", "搞钱"])
    if has_income_claim and any(ind in title for ind in leverage_indicators):
        if risk_level != "🔴高危":
            initial_verdict["risk_level"] = "🔴高危"
            initial_verdict["verdict"] = "借力打力型营销号：揭露陷阱→引流自身"
            initial_verdict["hit_patterns"].append("借力打力话术")
            initial_verdict["reason"] = f"标题同时含收入承诺和揭露性话术，属于借力打力营销号"
            initial_verdict["recommendation"] = "既提'月入过万'又'警告陷阱'=借力打力标准信号"
            return initial_verdict

    # 修正4：NHS等冒用权威 → 高危
    fake_authority = ["NHS健康", "全球健康管家"]
    if any(kw in account for kw in fake_authority):
        if risk_level != "🔴高危":
            initial_verdict["risk_level"] = "🔴高危"
            initial_verdict["verdict"] = "冒用权威机构名称"
            initial_verdict["hit_patterns"].append("冒用权威机构名")
            initial_verdict["reason"] = f"账号名「{account}」冒用权威机构名称"
            initial_verdict["recommendation"] = "冒用权威机构名的账号传播内容不可信"
            return initial_verdict

    # 修正5：游戏攻略类 → 降级为可信（B站"暴富秘籍"多为游戏攻略）
    game_indicators = ["攻略", "一梦江湖", "梦幻西游", "我的世界", "超凡先锋",
                       "菜市场模拟器", "复仇高手", "基岩", "金能赌出",
                       "少东家", "八音窍", "大保底"]
    if any(kw in title for kw in game_indicators):
        initial_verdict["risk_level"] = "🟢可信"
        initial_verdict["verdict"] = "游戏攻略/娱乐内容（非真实暴富）"
        initial_verdict["reason"] = "标题含游戏相关词汇，属于游戏攻略而非真实副业/暴富内容"
        initial_verdict["recommendation"] = "游戏内攻略内容，非营销号"
        initial_verdict["hit_patterns"] = ["游戏攻略内容"]
        return initial_verdict

    # 修正6：标题党辟谣文 → 降级为存疑（标题含虚假声明但内容为辟谣）
    # 常见于养老金假新闻：标题写"中央宣布养老金新规"但内容是"这是谣言，国家没出过"
    summary = item.get("summary", "") or item.get("description", "") or ""
    debunk_keywords = ["这是谣言", "纯属谣言", "别慌", "根本没发过", "从未发布过",
                       "从未出台", "没有官方文件", "制造焦虑", "流量骗局",
                       "别急着转发", "假的", "不存在", "纯属虚构"]
    if "中央宣布" in title and "养老金" in title:
        debunk_hits = [kw for kw in debunk_keywords if kw in summary]
        if len(debunk_hits) >= 1 and risk_level == "🔴高危":
            initial_verdict["risk_level"] = "🟡存疑"
            initial_verdict["verdict"] = "标题党辟谣文：标题传播虚假声明但内容为辟谣"
            initial_verdict["hit_patterns"].append("标题传播假声明+内容辟谣=标题党辟谣")
            initial_verdict["reason"] = f"标题含'中央宣布+养老金新规'虚假声明，但摘要含辟谣关键词（{', '.join(debunk_hits[:2])}），内容为辟谣。标题本身仍在传播虚假信息。"
            initial_verdict["recommendation"] = "虽然内容为辟谣，但标题传播了虚假声明，读者可能只看标题就转发。建议标题应明确标注为辟谣。"
            return initial_verdict

    return initial_verdict


def get_case_id(platform: str, stats: dict) -> str:
    """生成案例ID"""
    prefix_map = {"bilibili": "BL", "wechat": "WX", "weibo": "WB"}
    prefix = prefix_map.get(platform, "XX")
    count = stats.get(f"{platform}_total", 0) + 1
    return f"CASE-{prefix}-{count:03d}"


def append_to_case_library(item: dict, verdict: dict, case_id: str):
    """将鉴别结果追加到案例库Markdown"""
    platform_names = {"bilibili": "B站", "wechat": "微信公众号", "weibo": "微博"}
    platform = platform_names.get(item.get("platform", ""), "未知平台")

    # 构建内容区块
    if item.get("platform") == "bilibili":
        content_block = f"- **标题**：{item.get('title', '')}\n- **UP主**：{item.get('author', '')}（UID: {item.get('mid', '')}）\n- **链接**：{item.get('url', '')}\n- **播放量**：{item.get('play', 0)}"
    elif item.get("platform") == "wechat":
        content_block = f"- **标题**：{item.get('title', '')}\n- **公众号**：{item.get('account', '')}\n- **摘要**：{item.get('summary', '')[:100]}\n- **链接**：{item.get('url', '')}"
    else:
        content_block = f"- **博主**：{item.get('author', '')}\n- **内容**：{item.get('text_preview', '')[:150]}\n- **链接**：{item.get('url', '')}\n- **传播数据**：转发{item.get('reposts', 0)} | 评论{item.get('comments', 0)}"

    risk = verdict.get("risk_level", "🟡存疑")
    date_str = datetime.now().strftime("%Y-%m-%d")

    entry = f"""
---

### {case_id} | {risk} | {platform} | {date_str}

**鉴别结论**：{verdict.get('verdict', '')}

**内容信息**：
{content_block}

**命中特征**：{', '.join(verdict.get('hit_patterns', []))}

**分析**：{verdict.get('reason', '')}

**行动建议**：{verdict.get('recommendation', '')}

**搜索关键词**：`{item.get('search_keyword', '')}`

"""
    with open(CASE_LIBRARY, "a", encoding="utf-8") as f:
        f.write(entry)


def load_stats() -> dict:
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "bilibili_total": 0, "wechat_total": 0, "weibo_total": 0,
        "high_risk": 0, "suspicious": 0, "safe": 0,
        "last_run": ""
    }


def save_stats(stats: dict):
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def run_verdict():
    """主流程：批量鉴别待处理条目"""
    os.makedirs(COLLECTED_DIR, exist_ok=True)
    stats = load_stats()

    total_processed = 0
    total_high_risk = 0

    print(f"\n🤖 自动鉴别任务启动 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"   历史累计：B站{stats['bilibili_total']} | 微信{stats['wechat_total']} | 微博{stats['weibo_total']}\n")

    for platform, queue_file in QUEUE_FILES.items():
        if total_processed >= MAX_PER_RUN:
            break

        queue = load_queue(queue_file)
        pending = [item for item in queue if not item.get("verdict")]

        if not pending:
            print(f"  ⏭️  {platform}: 无待鉴别条目")
            continue

        print(f"  📋 {platform}: 待鉴别 {len(pending)} 条，本次处理最多 {MAX_PER_RUN - total_processed} 条")
        batch_count = 0

        for i, item in enumerate(queue):
            if total_processed >= MAX_PER_RUN:
                break
            if item.get("verdict"):
                continue

            prompt = build_prompt(item)
            if not prompt:
                continue

            print(f"    [{i+1}/{len(pending)}] 鉴别中: {item.get('title', item.get('text_preview', ''))[:40]}...")

            # 规则引擎 v4 鉴别（替代 knot-cli）
            verdict = rule_engine_verdict(item)
            # 深度审查二次修正
            verdict = deep_review_verdict(item, verdict)

            if verdict:
                item["verdict"] = verdict.get("verdict", "")
                item["risk_level"] = verdict.get("risk_level", "🟡存疑")
                item["hit_patterns"] = verdict.get("hit_patterns", [])
                item["reason"] = verdict.get("reason", "")
                item["recommendation"] = verdict.get("recommendation", "")
                item["verdict_at"] = datetime.now().isoformat()

                # 统计
                case_id = get_case_id(platform, stats)
                stats[f"{platform}_total"] += 1
                if "高危" in item["risk_level"]:
                    stats["high_risk"] += 1
                    total_high_risk += 1
                elif "存疑" in item["risk_level"]:
                    stats["suspicious"] += 1
                else:
                    stats["safe"] += 1

                # 追加到案例库（仅高危和存疑）
                if "可信" not in item["risk_level"]:
                    append_to_case_library(item, verdict, case_id)
                    print(f"       → {item['risk_level']} 已归档为 {case_id}")
                else:
                    print(f"       → {item['risk_level']} (可信内容，不归档)")

                total_processed += 1
                batch_count += 1
            else:
                print(f"       → ⚠️ 鉴别失败，跳过")

            time.sleep(1)

        save_queue(queue_file, queue)
        print(f"  ✅ {platform} 本次处理 {batch_count} 条\n")

    stats["last_run"] = datetime.now().isoformat()
    save_stats(stats)

    print(f"{'='*50}")
    print(f"✅ 本次鉴别完成！共处理 {total_processed} 条")
    print(f"   🔴 高危: {total_high_risk} 条（已归档到案例库）")
    print(f"   累计案例库统计: {stats}")
    print(f"{'='*50}\n")

    return total_processed, total_high_risk


if __name__ == "__main__":
    run_verdict()
