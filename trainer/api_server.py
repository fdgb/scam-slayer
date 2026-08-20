#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scam-slayer · B 端 HTTP API 网关 (P2 · 付费点 A「责任转移+批量权限」落地)
================================================================================
把 batch_verdict / monitor 包装成 HTTP 接口，供机构系统调用，配合 API Key 鉴权
实现「批量权限 + 责任转移」。

端点：
  GET  /api/health
  POST /api/batch        body: {"inputs":[{type,value,label}...], "org":"..."}
                          → 批量分级报告（JSON 追溯）
  POST /api/monitor/scan → 对机构盯防列表(monitor_data/watchlist.json)执行扫描
                          → 返回监控报告（合规可追溯）

鉴权：环境变量 API_TOKEN 非空时，要求请求头 `X-API-Key` 匹配（或 ?key=）。
      未设 API_TOKEN 则开放（仅建议本机/内网测试用）。

运行：
  python api_server.py                 # 默认 127.0.0.1:8787
  PORT=9000 API_TOKEN=xxx python api_server.py

⚠️ 生产部署须放在反向代理后并启用 API_TOKEN + HTTPS；不要直接 0.0.0.0 暴露公网。
   实际计费由 SkillPay / SkillHub 平台侧处理，本服务只提供能力调用与权限网关。
"""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from batch_verdict import run_batch
from monitor import scan as monitor_scan

PORT = int(os.environ.get("PORT", "8787"))
HOST = os.environ.get("HOST", "127.0.0.1")
API_TOKEN = os.environ.get("API_TOKEN", "").strip()


def _check_auth(headers, query):
    if not API_TOKEN:
        return True
    key = headers.get("X-API-Key") or query.get("key", [None])[0]
    return key == API_TOKEN


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/api/health":
            self._send(200, {"ok": True, "engine": "rule_engine_v4", "auth": bool(API_TOKEN)})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not _check_auth(self.headers, q):
            self._send(401, {"error": "unauthorized"})
            return

        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"error": "invalid json"})
            return

        if u.path == "/api/batch":
            items = data.get("inputs", [])
            org = data.get("org", "（机构名称待填）")
            if not items:
                self._send(400, {"error": "inputs 为空"})
                return
            results, stats = run_batch(items, org)
            self._send(200, {
                "engine": "rule_engine_v4",
                "org": org,
                "generated_at": __import__("datetime").datetime.now().isoformat(),
                "stats": stats,
                "results": results,
                "disclaimer": "结论由规则引擎离线生成，非人工核实；重大风险请以官方渠道为准。",
            })
        elif u.path == "/api/monitor/scan":
            report = monitor_scan("all")
            if report is None:
                self._send(200, {"status": "empty", "message": "盯防列表为空"})
            else:
                self._send(200, {
                    "status": "ok",
                    "generated_at": report.get("generated_at"),
                    "stats": {"total": report.get("total"), "high_risk": report.get("high_risk"),
                              "suspicious": report.get("suspicious"), "need_online": report.get("need_online")},
                    "results": report.get("results"),
                })
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass  # 静默


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"🛡️  Scam Slayer API 网关已启动：http://{HOST}:{PORT}")
    print(f"   鉴权: {'已启用(API_TOKEN 已设)' if API_TOKEN else '未启用(仅本机/内网测试)'}")
    print(f"   端点: GET /api/health · POST /api/batch · POST /api/monitor/scan")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 已停止")


if __name__ == "__main__":
    main()
