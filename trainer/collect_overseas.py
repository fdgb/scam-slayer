#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
海外平台营销号鉴别训练数据采集器
采集 YouTube、Instagram、Facebook 等平台的营销号/诈骗内容
包含语音翻译为中文的功能
"""

import json
import os
import re
import time
from datetime import datetime

BASE_DIR = os.path.dirname(__file__)
COLLECTED_DIR = os.path.join(BASE_DIR, "..", "references", "collected_data")
os.makedirs(COLLECTED_DIR, exist_ok=True)

# ====== 配置区 ======
# 海外平台关键词采集（营销号/诈骗特征）
SEARCH_QUERIES = [
    # 英文 - 财富诈骗类
    "make money online scam YouTube 2024",
    "crypto investment scam YouTube channels",
    "get rich quick scheme YouTube scam",
    "work from home scam fake success stories",
    "AI money making scam YouTube",

    # 英文 - 健康养生类
    "miracle cure scam YouTube health",
    "detox scam fake health advice YouTube",
    "supplement scam health misinformation",
    "cancer cure scam fake doctor YouTube",

    # 英文 - AI诈骗类
    "deepfake scam YouTube AI generated",
    "celebrity AI clone scam investment",
    "AI voice clone scam phone call",

    # 英文 - 搬运盗视频类
    "stolen YouTube videos reupload watermark removed",
    "channel stealing content legal issues",
    "scraped content YouTube re-uploader",

    # 多语言 - 日韩
    "赚钱 方法 投資 詐欺 YouTube 日本語",
    "불법 투자 사기 유튜브 한국어",
    "副収入 投資話 詐欺 YouTube",

    # 多语言 - 欧洲
    "arnaque investissement YouTube français",
    "betrug geldanhaufen YouTube deutsch",
    "truffa soldi YouTube italiano",

    # 俄语
    "мошенничество инвестиции YouTube русский",
    "финансовая пирамида YouTube обман",

    # 西班牙语
    "estafa inversión YouTube español",
    "ganar dinero fácil estafa YouTube",
]

OUTPUT_FILE = os.path.join(COLLECTED_DIR, "overseas_queue.json")

# 支持的语言列表（用于翻译）
LANGUAGES = {
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "es": "Spanish",
    "zh": "Chinese",
    "pt": "Portuguese",
    "ar": "Arabic",
    "hi": "Hindi",
}
# ====== 配置区结束 ======


def detect_language(text: str) -> str:
    """简单语言检测"""
    if re.search(r'[\u4e00-\u9fff]', text):
        return "zh"
    elif re.search(r'[\u3040-\u30ff]', text):
        return "ja"
    elif re.search(r'[\uac00-\ud7af]', text):
        return "ko"
    elif re.search(r'[\u0400-\u04ff]', text):
        return "ru"
    elif re.search(r'[\u0600-\u06ff]', text):
        return "ar"
    elif re.search(r'[a-zA-Z]', text):
        return "en"
    return "unknown"


def extract_video_id_from_url(url: str) -> str:
    """从各种URL格式中提取视频ID"""
    # YouTube
    yt_match = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})', url)
    if yt_match:
        return f"youtube:{yt_match.group(1)}"

    # Instagram
    ig_match = re.search(r'instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)', url)
    if ig_match:
        return f"instagram:{ig_match.group(1)}"

    return url


def parse_case_from_search_result(result: dict, query: str, platform: str) -> dict:
    """解析搜索结果为统一格式"""
    return {
        "id": f"overseas_{platform}_{int(time.time())}_{len(str(result))}",
        "platform": platform,
        "query": query,
        "title": result.get("title", ""),
        "description": result.get("description", "")[:300],
        "channel": result.get("channel", ""),
        "url": result.get("url", ""),
        "video_id": extract_video_id_from_url(result.get("url", "")),
        "views": result.get("views", 0),
        "language": detect_language(result.get("title", "") + result.get("description", "")),
        "original_text": {
            "title": result.get("title", ""),
            "description": result.get("description", "")[:500],
        },
        "translated_text": {
            "title": "",  # 待翻译填充
            "description": "",
        },
        "scam_features": result.get("scam_features", []),
        "risk_indicators": result.get("risk_indicators", []),
        "collected_at": datetime.now().isoformat(),
        "archived": False,
    }


def get_mock_overseas_data(query: str) -> list:
    """
    模拟海外平台数据采集结果
    在实际环境中，这里会调用各平台的公开API或网页爬虫
    返回格式化的案例列表
    """
    # 模拟数据（实际使用时应替换为真实API调用）
    mock_data = [
        {
            "title": f"【模拟数据】{query.split()[0]} 相关视频标题（实际会调用API获取）",
            "description": "这是描述文本，实际会从YouTube/Instagram等平台获取真实内容...",
            "channel": "示例频道",
            "url": "https://www.youtube.com/watch?v=EXAMPLE",
            "views": 0,
            "scam_features": [],
            "risk_indicators": [],
        }
    ]
    return mock_data


def translate_to_chinese(text: str, source_lang: str) -> str:
    """
    将外语文本翻译为中文
    在实际环境中，这里可以调用翻译API
    """
    if not text:
        return ""

    # 如果已是中文，直接返回
    if source_lang == "zh":
        return text

    # 模拟翻译（实际使用时应调用翻译API如Google Translate、DeepL等）
    # 这里返回原文 + 语言标记，实际使用时请接入真实翻译服务
    return f"[{LANGUAGES.get(source_lang, source_lang)}] {text}"


def load_queue():
    """加载已有队列"""
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_queue(queue):
    """保存队列"""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def is_duplicate(queue, item):
    """检查是否重复"""
    title = item.get("title", "")[:50]
    return any(i.get("title", "")[:50] == title for i in queue)


def collect_overseas():
    """主采集函数"""
    print("\n🌍 海外平台营销号数据采集开始")
    print(f"   语言支持: {', '.join(LANGUAGES.values())}")
    print(f"   采集平台: YouTube, Instagram, Facebook 等\n")

    queue = load_queue()
    new_count = 0

    # 每次随机选择 3 个关键词
    import random
    selected = random.sample(SEARCH_QUERIES, min(3, len(SEARCH_QUERIES)))

    for query in selected:
        print(f"  🔍 搜索: {query[:50]}...")

        # 根据查询判断目标平台
        platform = "youtube"  # 默认YouTube
        if "instagram" in query.lower():
            platform = "instagram"
        elif "facebook" in query.lower():
            platform = "facebook"

        # 获取数据（实际环境替换为真实API调用）
        results = get_mock_overseas_data(query)

        for result in results:
            # 模拟多个平台的数据
            result["platform"] = platform
            result["url"] = f"https://www.{platform}.com/watch?v=sample_{platform}_{new_count}"

            case = parse_case_from_search_result(result, query, platform)

            # 语言检测和翻译
            if case["language"] != "zh" and case["language"] != "unknown":
                # 翻译标题
                if case["original_text"]["title"]:
                    case["translated_text"]["title"] = translate_to_chinese(
                        case["original_text"]["title"], case["language"]
                    )
                # 翻译描述
                if case["original_text"]["description"]:
                    case["translated_text"]["description"] = translate_to_chinese(
                        case["original_text"]["description"], case["language"]
                    )

            if not is_duplicate(queue, case):
                queue.append(case)
                new_count += 1

        print(f"    → 新增 {len(results)} 条")
        time.sleep(1)

    save_queue(queue)
    print(f"\n✅ 海外平台采集完成！本次新增 {new_count} 条")
    print(f"   总计 {len(queue)} 条数据\n")
    return new_count


def generate_translation_summary():
    """生成翻译摘要报告"""
    queue = load_queue()

    # 统计各语言数量
    lang_stats = {}
    for item in queue:
        lang = item.get("language", "unknown")
        lang_stats[lang] = lang_stats.get(lang, 0) + 1

    print("\n🌐 语言分布统计：")
    for lang, count in sorted(lang_stats.items(), key=lambda x: -x[1]):
        lang_name = LANGUAGES.get(lang, lang)
        print(f"   {lang_name}: {count} 条")

    # 已翻译数量
    translated = sum(1 for item in queue if item.get("translated_text", {}).get("title"))
    print(f"\n   已翻译: {translated}/{len(queue)} 条")

    return lang_stats


if __name__ == "__main__":
    collect_overseas()
    generate_translation_summary()