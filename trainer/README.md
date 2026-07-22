# 🛡️ Marketing-Detector 自动训练系统

> 自动采集多平台营销号/谣言内容，持续训练鉴别能力，丰富案例库

## 📁 目录结构

```
trainer/
├── run_trainer.py          # 🎯 主控脚本（完整流程入口）
├── collect_bilibili.py     # 🎬 B站视频采集
├── collect_wechat_weibo.py # 📱 微信公众号 + 微博采集
├── auto_verdict.py         # 🤖 AI自动鉴别 + 归档
└── README.md               # 📖 本文档

references/
├── case-library.md         # 📚 案例库（自动追加）
├── collected_data/
│   ├── bilibili_queue.json  # B站采集队列
│   ├── wechat_queue.json    # 微信采集队列
│   ├── weibo_queue.json     # 微博采集队列
│   ├── stats.json           # 统计数据
│   └── training_report.md  # 最新训练报告
```

## 🚀 使用方法

```bash
# 完整流程（采集 + 鉴别 + 报告）
python run_trainer.py

# 仅采集数据
python run_trainer.py --collect

# 仅鉴别归档
python run_trainer.py --verdict

# 仅生成报告
python run_trainer.py --report
```

## ⏰ 定时任务

已设置 **每天早上 8:00** 自动执行完整流程，执行完毕后会主动通知。

## 🌐 采集平台覆盖

| 平台 | 采集方式 | 内容类型 |
|------|---------|---------|
| **B站** | 官方搜索API | 视频（标题/UP主/播放量/标签） |
| **微信公众号** | 搜狗微信搜索 | 文章（标题/公众号名/摘要） |
| **微博** | 移动版API | 帖子（内容/博主/传播数据） |

## 🎯 采集关键词（自动覆盖）

**财富诱惑型**：副业赚钱、月入过万、暴富秘籍、财富自由  
**养生伪科学型**：养生秘方、保健品推荐、治疗偏方、排毒方法  
**健康焦虑型**：食物相克、这样吃致癌、健康必看、医生不告诉你  
**政策谣言型**：国家补贴、养老金新规、退休政策  
**AI伪造型**：AI换脸、克隆声音、深度伪造  

## 📊 风险分级

| 等级 | 说明 | 处理方式 |
|------|------|---------|
| 🔴 **高危** | 明确诈骗/谣言 | **自动归档到案例库** |
| 🟡 **存疑** | 夸大/误导性内容 | **自动归档到案例库** |
| 🟢 **可信** | 正常内容 | 记录但不归档 |

## ⚙️ 配置说明

各采集脚本顶部均有 `配置区`，可调整：
- `KEYWORDS`：采集关键词列表
- `MAX_PER_KEYWORD`：每个关键词最大采集数量
- `SLEEP_BETWEEN`：请求间隔（秒）
- `MAX_PER_RUN`（auto_verdict.py）：每次最多鉴别条数

## 📦 依赖安装

```bash
pip install requests beautifulsoup4
```
