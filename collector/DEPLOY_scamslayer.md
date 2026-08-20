# Scam-Slayer 收集端部署手册（腾讯云 · 独立端口 8081）

> 目标：在你已买的腾讯云 Lighthouse（`140.143.85.18`，Ubuntu）上，给 scam-slayer 起一个**独立**的收集端。
> 设计原则：
> - **不动**你已上线的 MusicMood 收集端（8080，进程/数据/防火墙全不动）。
> - scam-slayer 跑在 **8081**，独立进程、独立目录 `/data/scam-slayer/`、独立防火墙规则 → 数据绝不混。
> - 协议：agent（AI）**不直登服务器**；下面命令由你在服务器终端粘贴执行，agent 从本机 `curl` 验证，你 `grep` 落地确认。

---

## 0. 前置确认
- 你已有 `140.143.85.18`（Ubuntu，Lighthouse），MusicMood 收集端在 8080 正常。
- 本机（Mac）有文件：`/Users/rdw/.workbuddy/skills/scam-slayer/collector/collector_server.py`。
- 服务器初始禁 root 密码登录 → 用 `ubuntu@IP`；要 root 权限先 `sudo -i`，**再整段粘贴**需 root 的命令。

---

## 1. 把收集端传到服务器（在你 Mac 终端执行）
```bash
scp /Users/rdw/.workbuddy/skills/scam-slayer/collector/collector_server.py \
    ubuntu@140.143.85.18:/tmp/collector_server.py
```

## 2. 服务器建目录 + 放文件 + 起服务（在服务器终端执行）
先登录，提权：
```bash
ssh ubuntu@140.143.85.18
sudo -i
```
然后整段粘贴（已含 sudo -i 后的 root 环境）：
```bash
mkdir -p /opt/scam-slayer-collector /data/scam-slayer
mv /tmp/collector_server.py /opt/scam-slayer-collector/collector_server.py
chmod 644 /opt/scam-slayer-collector/collector_server.py

# 写 systemd 单元（监听 8081，多租户 collector 仅启用 scam-slayer 密钥）
cat > /etc/systemd/system/scam-slayer-collector.service <<'EOF'
[Unit]
Description=Scam-Slayer feedback collector (port 8081)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/scam-slayer-collector
ExecStart=/usr/bin/python3 /opt/scam-slayer-collector/collector_server.py --host 0.0.0.0 --port 8081
Environment=SCAM_SLAYER_KEY=ss-shared-4Mp7rT2vK8nX5hQz
Environment=COLLECTOR_DATA_ROOT=/data
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable scam-slayer-collector
systemctl restart scam-slayer-collector
systemctl status scam-slayer-collector --no-pager | head -20
```

## 3. 防火墙放行 8081（腾讯云控制台，不在终端）
- 登录腾讯云轻量应用服务器控制台 → 对应实例 → **防火墙** → 添加规则：
  - 应用类型：自定义
  - 协议：TCP
  - 端口：8081
  - 来源：0.0.0.0/0（或限定你信任的网段；匿名数据明文 HTTP，限定来源更安全）
- 若服务器内部启了 `ufw`，也需 `ufw allow 8081/tcp`。

## 4. 验证（agent 从本机 curl，你 grep 确认）
agent 会执行（在你 Mac / 沙箱内）：
```bash
# 健康检查
curl -s http://140.143.85.18:8081/ | python3 -m json.tool
# 发一条测试贡献（带正确密钥）
curl -s -X POST http://140.143.85.18:8081/v1/contribute \
  -H "Content-Type: application/json" \
  -H "X-ScamSlayer-Key: ss-shared-4Mp7rT2vK8nX5hQz" \
  -d '{"skill_id":"scam-slayer","type":"usage","v":1,"content_kind":"url","risk_type":"测试"}'
```
你确认落地（服务器终端，`sudo -i` 后）：
```bash
cat /data/scam-slayer/contrib.jsonl
```
应看到刚才那条 `type:usage` 记录（**只有 content_kind/risk_type 等聚合字段，没有原文**）。
> ⚠️ 这是 PIPL 自检点：落盘文件里**绝不应出现**任何原始链接/账号/截图文本。若出现，立即停服排查。

坏密钥应被拒（agent 验证，期望 401）：
```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://140.143.85.18:8081/v1/contribute \
  -H "Content-Type: application/json" -H "X-ScamSlayer-Key: wrong" \
  -d '{"skill_id":"scam-slayer","type":"verdict","label":"correct","content_hash":"x"}'
```

## 5. 客户端开启（在任意装了 scam-slayer 的机器上）
```bash
python feedback_sync.py --opt-in      # 默认端点已是 http://140.143.85.18:8081/v1/contribute
python feedback_sync.py --status
```
之后每次鉴定确认/判分/校正会自动匿名回流（原始内容只在本地算 SHA256，永不外发）。

---

## 运维速查
| 动作 | 命令（服务器 `sudo -i` 后） |
|------|------|
| 看状态 | `systemctl status scam-slayer-collector --no-pager` |
| 看日志 | `journalctl -u scam-slayer-collector -n 50 --no-pager` |
| 重启 | `systemctl restart scam-slayer-collector` |
| 聚合（出 cross_user_patterns.json） | `cd /opt/scam-slayer-collector && python3 collector_server.py --aggregate --skill scam-slayer` |
| **删除某用户数据（PIPL）** | `python3 collector_server.py --purge --skill scam-slayer`（删 `/data/scam-slayer/contrib.jsonl`） |
| 停服 | `systemctl stop scam-slayer-collector` |

## 安全边界（诚实声明）
- `SCAM_SLAYER_KEY`（写在分发客户端源码里）只防「知道 URL 但无 zip」的路人灌数据，**不是真安全**。要更强需服务端签发 token（超纲，v2）。
- 明文 HTTP：匿名聚合数据 v1 可接受；若以后收更敏感字段，需上 HTTPS（域名 + 命名隧道 / 反代）。
- 收集端代码可公开（无密钥），但**服务器 IP / 落盘路径 / systemd 细节不进用户可见文档或分发包**。
