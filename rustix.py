import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全依赖
for pkg in ["requests", "playwright"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
from playwright.async_api import async_playwright

# 环境变量配置
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"


def notify(text):
    """发送 Telegram 消息通知"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
    except Exception as e:
        print(f"[TG] 通知发送失败: {e}")


async def wait_and_fetch_api(page, url, method="GET", body=None):
    """在已通过 Cloudflare 盾的 Chromium 浏览器环境中发起 fetch API 请求"""
    js_code = """
    async ({ url, method, apiKey, body }) => {
        try {
            const options = {
                method: method,
                headers: {
                    'Authorization': 'Bearer ' + apiKey,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }
            };
            if (body) {
                options.body = JSON.stringify(body);
            }
            const res = await fetch(url, options);
            const text = await res.text();
            return { status: res.status, text: text };
        } catch (err) {
            return { status: 0, text: err.toString() };
        }
    }
    """
    payload = {
        "url": url,
        "method": method,
        "apiKey": API_KEY,
        "body": body,
    }
    res_data = await page.evaluate(js_code, payload)
    
    status = res_data.get("status", 0)
    raw_text = res_data.get("text", "").strip()

    parsed_json = None
    if raw_text:
        try:
            parsed_json = json.loads(raw_text)
        except Exception:
            pass

    return status, parsed_json, raw_text


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Chromium 引擎解算 Cloudflare JS Challenge 模式)...")
    proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None

    async with async_playwright() as p:
        # 1. 启动 Chromium 原生浏览器引擎
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
        )

        page = await context.new_page()

        print("🌐 步骤 1: 加载主页，由 Chromium 执行 Cloudflare 前端 JavaScript 校验...")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            print(f"  └─ 页面加载提示: {e}")

        # 留出时间供 Cloudflare 的前端 JS 完成演算并写入 Clearance 校验
        print("⏳ 等待 Cloudflare 前端 JS 盾通过验证...")
        await page.wait_for_timeout(6000)

        # 2. 查询服务器初始状态
        print("🔍 步骤 2: 在浏览器上下文中调用 API 查询服务器状态...")
        res_url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources"
        status_code, json_data, raw_text = await wait_and_fetch_api(page, res_url, "GET")

        init_status = "unknown"
        if json_data and isinstance(json_data, dict):
            init_status = json_data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ API 响应成功，状态: [{init_status}]")
        else:
            print(f"  └─ HTTP {status_code}，内容片段: {raw_text[:120]}...")

        # 运行中则直接退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在正常运行中，无需重启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            await browser.close()
            sys.exit(0)

        # 3. 发送开机指令
        print("⚡ 步骤 3: 在浏览器上下文中发送 [start] 电源指令...")
        power_url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/power"
        p_code, p_json, p_raw = await wait_and_fetch_api(page, power_url, "POST", {"signal": "start"})
        print(f"  └─ [start] 指令 HTTP 状态码: {p_code}")

        power_success = p_code in [200, 204]

        # 4. 轮询更新确认
        print("⏳ 步骤 4: 轮询确认服务器最新状态...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            code, data, text = await wait_and_fetch_api(page, res_url, "GET")
            if data and isinstance(data, dict):
                curr = data.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break
            else:
                print(f"  └─ 轮询第 {i}/5 次 HTTP {code}: {text[:80]}")

        await browser.close()

        # 5. 结果判定与通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif power_success:
            notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应状态码: HTTP {p_code}\n- 开机信号已成功送达后台。")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
