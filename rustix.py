import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全必要依赖
for pkg in ["requests", "playwright", "curl_cffi"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
from curl_cffi import requests as cffi_requests
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


async def pass_cf_and_get_session():
    """使用 Playwright 启动 Chromium，自动识别并点击 Turnstile 验证框突破 Cloudflare，提取 clearance Cookie"""
    print("🌐 步骤 0: 启动 Playwright 穿透 Cloudflare 防火墙与 Turnstile 人机验证...")
    
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
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
        )
        page = await context.new_page()

        # 抹除 webdriver 特征标识
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        try:
            print("⏳ 正在载入 Rustix 控制台主页...")
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  └─ 初始加载提示: {e}")

        # 循环检测与点击 Cloudflare Turnstile 验证框
        passed = False
        for i in range(1, 25):
            await asyncio.sleep(1)
            title = await page.title()
            cookies = await context.cookies()
            has_clearance = any(c.get("name") == "cf_clearance" for c in cookies)

            if "Just a moment" not in title and "Cloudflare" not in title:
                print(f"  └─ ✅ 页面已成功通过 Cloudflare 盾！标题: [{title}]")
                passed = True
                break

            # 检索 Turnstile iframe 框架并自动执行模拟点击
            try:
                for frame in page.frames:
                    if "challenges.cloudflare.com" in frame.url:
                        cb = await frame.query_selector("input[type='checkbox'], .recaptcha-checkbox, #challenge-stage")
                        if cb:
                            print(f"  └─ 🎯 (第 {i}s) 检测到 Turnstile 验证复选框，正在模拟物理点击...")
                            await cb.click()
            except Exception:
                pass

        await page.screenshot(path="screenshots/cf_pass_result.png")
        cookies_list = await context.cookies()
        actual_ua = await page.evaluate("navigator.userAgent")
        await browser.close()

        cookie_dict = {c["name"]: c["value"] for c in cookies_list}
        print(f"  └─ 获取 Cookies 数量: {len(cookie_dict)} 项 (包含 clearance: {any(c == 'cf_clearance' for c in cookie_dict)})")
        return cookie_dict, actual_ua


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Turnstile 自动解算 + socks5h 远程 DNS 模式)...")

    # 1. 自动穿透 Cloudflare 并提取验证 Cookie
    cookies, ua = asyncio.run(pass_cf_and_get_session())

    # 强制将 SOCKS5 代理转换为 socks5h:// (由代理端执行远程 DNS 解析，解决 SSL_connect 握手中断问题)
    cffi_proxy = PROXY_URL
    if cffi_proxy.startswith("socks5://"):
        cffi_proxy = cffi_proxy.replace("socks5://", "socks5h://")

    proxies = {
        "http": cffi_proxy,
        "https": cffi_proxy,
    } if cffi_proxy else None

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": ua or USER_AGENT,
    }

    session = cffi_requests.Session()
    session.headers.update(headers)
    if cookies:
        session.cookies.update(cookies)

    # 2. 查询服务器初始状态
    print("🔍 步骤 1: 请求 API 探测服务器当前状态...")
    init_status = "unknown"
    try:
        res = session.get(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ API HTTP 状态码: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            init_status = data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 当前服务器状态: [{init_status}]")
        else:
            print(f"  └─ 响应片段: {res.text[:150]}")
    except Exception as e:
        print(f"  └─ API 请求异常: {e}")

    # 若正在运行则直接成功退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正在正常运行中，无需重复开启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 3. 发送开机指令
    print("⚡ 步骤 2: 下发 [start] 电源指令...")
    power_success = False
    p_code = 0
    try:
        p_res = session.post(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/power",
            json={"signal": "start"},
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        p_code = p_res.status_code
        print(f"  └─ 电源指令 HTTP 状态码: {p_code}")
        if p_code in [200, 204]:
            power_success = True
    except Exception as e:
        print(f"  └─ 电源指令发送异常: {e}")

    # 4. 轮询确认状态变更
    print("⏳ 步骤 3: 轮询确认服务器状态变更...")
    final_status = "unknown"
    for i in range(1, 6):
        time.sleep(4)
        try:
            check_res = session.get(
                f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
                proxies=proxies,
                impersonate="chrome120",
                timeout=20,
            )
            if check_res.status_code == 200:
                curr = check_res.json().get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break
            else:
                print(f"  └─ 轮询第 {i}/5 次响应 HTTP {check_res.status_code}")
        except Exception as e:
            print(f"  └─ 轮询网络异常: {e}")

    # 5. 结果判定与 Telegram 通知
    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
        sys.exit(0)
    elif power_success:
        notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应: HTTP {p_code}\n- 开机指令已成功送达服务端后台。")
        sys.exit(0)
    else:
        notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
        sys.exit(1)


if __name__ == "__main__":
    main()
