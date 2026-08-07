import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查与补全依赖
required_pkgs = ["requests", "playwright"]
for pkg in required_pkgs:
    try:
        if pkg == "playwright":
            from playwright.async_api import async_playwright
        else:
            import requests
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
from playwright.async_api import async_playwright

TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"


def notify(text):
    """发送纯文字通知到 Telegram"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
    except Exception as exc:
        print(f"[TG] 通知失败: {exc}")


async def get_server_resource_state(api_context):
    """安全获取服务器当前运行状态"""
    try:
        res = await api_context.get(f"/api/client/servers/{SERVER_ID}/resources")
        if res.status == 200:
            body_text = await res.text()
            if body_text and body_text.strip():
                data = json.loads(body_text)
                return data.get("attributes", {}).get("current_state", "unknown"), "OK"
        return "unknown", f"HTTP_{res.status}"
    except Exception as e:
        return "unknown", f"ERROR_{e}"


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (纯 API 精确控制模式)...")
    proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None

    async with async_playwright() as p:
        api = await p.request.new_context(
            base_url=BASE_URL,
            extra_http_headers={
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            proxy=proxy_config,
        )

        # 1. 探测初始状态
        print("🔍 步骤 1: 检查服务器当前状态...")
        init_status, msg = await get_server_resource_state(api)
        print(f"  └─ 探测状态: [{init_status}] (诊断: {msg})")

        if init_status in ["running", "starting"]:
            print("🎉 服务器正处于运行/启动状态，无需重复开机。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            sys.exit(0)

        # 2. 下发开机/保活指令
        print("⚡ 步骤 2: 发送 [start] 电源指令...")
        power_success = False
        try:
            p_res = await api.post(f"/api/client/servers/{SERVER_ID}/power", data=json.dumps({"signal": "start"}))
            print(f"  └─ [start] 指令 HTTP 状态码: {p_res.status}")
            if p_res.status in [200, 204]:
                power_success = True
        except Exception as e:
            print(f"  └─ 发送指令异常: {e}")

        # 3. 轮询确认状态变动
        print("⏳ 步骤 3: 等待服务器状态刷新...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            curr_status, _ = await get_server_resource_state(api)
            print(f"  └─ 轮询第 {i}/5 次: [{curr_status}]")
            if curr_status in ["running", "starting"]:
                final_status = curr_status
                break

        # 4. 结果判定与通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 状态变更为: [{final_status.upper()}]")
            sys.exit(0)
        elif power_success:
            # API 指令下发成功 (HTTP 200/204)，指令已送到服务端
            notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应: HTTP 200\n- 指令已成功下发至后台，服务器正在启动中。")
            sys.exit(0)
        else:
            notify(f"⚠️ Rustix 保活异常！\n\n- 初始状态: {init_status}\n- 最终状态: {final_status}")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
