import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全必要依赖 (包含 PySocks)
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


def get_socks5h_proxy():
    """将 socks5:// 转换为 socks5h://，强制使用代理端远程 DNS 解析"""
    if not PROXY_URL:
        return None
    p = PROXY_URL
    if p.startswith("socks5://"):
        p = p.replace("socks5://", "socks5h://")
    return {"http": p, "https": p}


def requests_fallback(endpoint, method="GET", body=None):
    """基于 OpenSSL + socks5h 远程 DNS 解析的物理重试请求"""
    url = f"{BASE_URL}{endpoint}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    proxies = get_socks5h_proxy()

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


async def get_status_via_page(page):
    """利用 Chromium 浏览器原生页面导航读取 API 节点 (绕过 CSP 和 JS CORS 限制)"""
    url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources"
    try:
        res = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        status_code = res.status if res else 0
        text = await page.inner_text("body")
        data = None
        try:
            data = json.loads(text)
        except Exception:
            pass
        return status_code, data, text
    except Exception as e:
        return 0, None, str(e)


async def send_power_via_xhr(page):
    """利用注入的页面 XHR 发送开机指令"""
    url = f"{BASE_URL}/api/client/servers/{SERVER_ID}/power"
    js_xhr = """
    async ({ url, apiKey }) => {
        return new Promise((resolve) => {
            const xhr = new XMLHttpRequest();
            xhr.open("POST", url, true);
            xhr.setRequestHeader("Authorization", "Bearer " + apiKey);
            xhr.setRequestHeader("Content-Type", "application/json");
            xhr.setRequestHeader("Accept", "application/json");
            xhr.onload = () => resolve({ status: xhr.status, text: xhr.responseText });
            xhr.onerror = () => resolve({ status: 0, text: "XHR Error" });
            xhr.send(JSON.stringify({ signal: "start" }));
        });
    }
    """
    try:
        res = await page.evaluate(js_xhr, {"url": url, "apiKey": API_KEY})
        return res.get("status", 0), res.get("text", "")
    except Exception as e:
        return 0, str(e)


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Chromium 页面直连 + socks5h 远程 DNS 双路径模式)...")

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

        # 挂载全局 Authorization 认证头
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "application/json",
            },
        )
        page = await context.new_page()

        # 1. 访问主页建立网络连接通道
        print("🌐 步骤 1: 访问 Rustix 控制台主页建立连接...")
        try:
            res = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
            print(f"  └─ 主页响应状态码: HTTP {res.status if res else '200'}")
        except Exception as e:
            print(f"  └─ 主页访问提示: {e}")

        await asyncio.sleep(5)

        # 2. 查询服务器初始状态
        print("🔍 步骤 2: 请求 API 探测服务器当前状态...")
        code, json_data, raw_text = await get_status_via_page(page)

        if code != 200 or not json_data:
            print(f"  └─ 浏览器页面读取非 200 (HTTP {code})，自动切换至 socks5h 远程 DNS 通道...")
            code, json_data, raw_text = requests_fallback(f"/api/client/servers/{SERVER_ID}/resources", "GET")

        init_status = "unknown"
        if json_data and isinstance(json_data, dict):
            init_status = json_data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 查询成功，当前服务器状态: [{init_status}]")
        else:
            print(f"  └─ 接口响应 HTTP {code}，内容: {raw_text[:120]}")

        # 若正在运行，直接通知并成功退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在正常运行中，无需重启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            await browser.close()
            sys.exit(0)

        # 3. 发送开机指令
        print("⚡ 步骤 3: 下发 [start] 电源指令...")
        p_code, p_raw = await send_power_via_xhr(page)

        if p_code not in [200, 204]:
            print(f"  └─ 页面 XHR 响应 HTTP {p_code}，切换为 socks5h 远程 DNS 通道重试...")
            p_code, _, p_raw = requests_fallback(
                f"/api/client/servers/{SERVER_ID}/power", "POST", {"signal": "start"}
            )

        print(f"  └─ [start] 指令响应状态码: HTTP {p_code}")
        power_success = p_code in [200, 204]

        # 4. 轮询确认开机状态
        print("⏳ 步骤 4: 轮询确认服务器状态更新...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            c_code, c_json, _ = await get_status_via_page(page)
            if c_code != 200 or not c_json:
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
