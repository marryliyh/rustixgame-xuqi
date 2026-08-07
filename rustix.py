import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全依赖 (包含 PySocks)
for pkg in ["requests", "playwright", "PySocks"]:
    try:
        if pkg == "PySocks":
            __import__("socks")
        else:
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
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs("screenshots", exist_ok=True)


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


async def handle_cors_route(route):
    """拦截 OPTIONS 跨域预检请求并强制返回 200，解决浏览器 fetch 被 CORS 阻断的问题"""
    if route.request.method == "OPTIONS":
        await route.fulfill(
            status=200,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS, PUT, DELETE",
                "Access-Control-Allow-Headers": "*",
            },
        )
    else:
        await route.continue_()


async def browser_fetch(page, url, method="GET", body=None):
    """在已连通的浏览器上下文内部执行原生 fetch"""
    js_script = """
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
            return { status: 0, text: err.name + ': ' + err.message };
        }
    }
    """
    res = await page.evaluate(js_script, {"url": url, "method": method, "apiKey": API_KEY, "body": body})
    status = res.get("status", 0)
    text = res.get("text", "")
    data = None
    if text:
        try:
            data = json.loads(text)
        except Exception:
            pass
    return status, data, text


def requests_fallback(endpoint, method="GET", body=None):
    """标准 Python requests + PySocks 兜底请求 (基于 OpenSSL，完全避开 BoringSSL 的 SOCKS5 崩溃 bug)"""
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    } if PROXY_URL else None

    try:
        if method == "GET":
            res = requests.get(url, headers=headers, proxies=proxies, timeout=20)
        else:
            res = requests.post(url, headers=headers, json=body, proxies=proxies, timeout=20)
        
        data = None
        try:
            data = res.json()
        except Exception:
            pass
        return res.status_code, data, res.text
    except Exception as e:
        return 0, None, str(e)


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Chromium 预检接管 + PySocks 物理重试双保险模式)...")

    proxy_pw = PROXY_URL.replace("socks5h://", "socks5://") if PROXY_URL else None
    proxy_config = {"server": proxy_pw} if proxy_pw else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        # 注册路由拦截，自动回应并放行预检 OPTIONS
        await page.route("**/api/**", handle_cors_route)

        # 1. 打开控制台主页建立网络连接通道
        print("🌐 步骤 1: 访问 Rustix 控制台建立通道...")
        try:
            res = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
            print(f"  └─ 主页响应 HTTP 状态码: {res.status if res else '200'}")
        except Exception as e:
            print(f"  └─ 页面建立提示: {e}")

        await page.wait_for_timeout(5000)
        await page.screenshot(path="screenshots/rustix_ready.png")

        # 2. 查询服务器初始状态
        print("🔍 步骤 2: 请求 API 探测服务器当前状态...")
        status_url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources"
        code, json_data, raw_text = await browser_fetch(page, status_url, "GET")

        # 若浏览器上下文 fetch 失败或被拦截，自动无缝切入 PySocks 管道重试
        if code == 0 or not json_data:
            print(f"  └─ 浏览器上下文提示 [{raw_text[:60]}]，自动启用 PySocks 原生通道重试...")
            code, json_data, raw_text = requests_fallback(f"/api/client/servers/{SERVER_ID}/resources", "GET")

        init_status = "unknown"
        if json_data and isinstance(json_data, dict):
            init_status = json_data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 查询成功，当前服务器状态: [{init_status}]")
        else:
            print(f"  └─ 接口响应 HTTP {code}，片段: {raw_text[:120]}")

        # 若正在运行，直接通知并成功退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在正常运行中，无需开启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            await browser.close()
            sys.exit(0)

        # 3. 发送开机指令
        print("⚡ 步骤 3: 下发 [start] 电源指令...")
        power_url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/power"
        p_code, p_json, p_raw = await browser_fetch(page, power_url, "POST", {"signal": "start"})

        if p_code == 0:
            print("  └─ 切换为 PySocks 通道下发电源指令...")
            p_code, p_json, p_raw = requests_fallback(
                f"/api/client/servers/{SERVER_ID}/power", "POST", {"signal": "start"}
            )

        print(f"  └─ [start] 指令响应状态码: HTTP {p_code}")
        power_success = p_code in [200, 204]

        # 4. 轮询确认开机状态
        print("⏳ 步骤 4: 轮询确认服务器状态更新...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            c_code, c_json, _ = await browser_fetch(page, status_url, "GET")
            if c_code == 0 or not c_json:
                c_code, c_json, _ = requests_fallback(f"/api/client/servers/{SERVER_ID}/resources", "GET")

            if c_json and isinstance(c_json, dict):
                curr = c_json.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break
            else:
                print(f"  └─ 轮询第 {i}/5 次 HTTP {c_code}")

        await browser.close()

        # 5. 结果判定与 Telegram 通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif power_success:
            notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应: HTTP {p_code}\n- 开机信号已成功送达后台。")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
