import os
import sys
import time
import asyncio
import subprocess
import json

# 自动检查并安装必要依赖
for pkg in ["requests", "playwright"]:
    try:
        __import__(pkg)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

import requests
from playwright.async_api import async_playwright

# 环境变量
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

def notify(text):
    """专属 Telegram 通知 (严格按照你的配置要求)"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活\n\n{text}"},
            timeout=15,
        )
    except Exception as e:
        print(f"[TG] 通知失败: {e}")

async def wait_for_cloudflare(page):
    """核心修复：死等 Cloudflare 盾解开，只有解开了才能发起 API 请求"""
    print("⏳ 正在等待 Cloudflare 防火墙解算通过...")
    for _ in range(30):
        title = await page.title()
        # 只要标题不是 CF 的提示，就说明进入了真正的网站
        if "Just a moment" not in title and "Cloudflare" not in title and title.strip() != "":
            print(f"  └─ ✅ 成功突破 CF 防火墙！当前页面标题: [{title}]")
            return True
        
        # 尝试点一下可能存在的 Turnstile 验证框
        try:
            for frame in page.frames:
                if "challenges.cloudflare.com" in frame.url:
                    box = await frame.query_selector(".recaptcha-checkbox, #challenge-stage")
                    if box:
                        await box.click(force=True)
        except Exception:
            pass
        
        await asyncio.sleep(1)
    return False

async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活 (桌面级浏览器直连 + 强行穿透模式)...")

    proxy_server = PROXY_URL.replace("socks5h://", "socks5://") if PROXY_URL else None
    proxy_config = {"server": proxy_server} if proxy_server else None

    async with async_playwright() as p:
        # headless=False 配合 Xvfb 伪装真实桌面环境
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
            proxy=proxy_config,
        )

        # bypass_csp=True 彻底关闭页面内的跨域和安全请求拦截，解决 Failed to fetch
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            bypass_csp=True, 
        )
        page = await context.new_page()

        # 消除 webdriver 痕迹
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # 1. 访问网站建立连接
        print("🌐 步骤 1: 访问主页并获取授权...")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"  └─ 页面初始加载异常: {e}")

        # 强制等待 CF 盾解开，过不了盾绝不发请求
        passed = await wait_for_cloudflare(page)
        if not passed:
            print("⚠️ 警告: 似乎未能在规定时间内解开 CF，将强行尝试执行指令...")

        # 万能请求工具 (利用当前已通行的页面环境)
        async def run_fetch(endpoint, method="GET", body=None):
            js_code = """
            async ({ endpoint, method, apiKey, body }) => {
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
                    const res = await fetch(endpoint, options);
                    const text = await res.text();
                    try {
                        return { ok: res.ok, status: res.status, data: JSON.parse(text) };
                    } catch(e) {
                        return { ok: res.ok, status: res.status, raw: text };
                    }
                } catch (err) {
                    return { ok: false, status: 0, error: err.name + ': ' + err.message };
                }
            }
            """
            return await page.evaluate(js_code, {
                "endpoint": f"/api/client/servers/{SERVER_ID}/{endpoint}",
                "method": method,
                "apiKey": API_KEY,
                "body": body
            })

        # 2. 检查服务器是否启动
        print("🔍 步骤 2: 检查服务器当前运行状态...")
        res_info = await run_fetch("resources", "GET")
        
        init_status = "unknown"
        if res_info.get("ok") and "data" in res_info:
            init_status = res_info["data"].get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 当前服务器状态: [{init_status}]")
        else:
            print(f"  └─ 状态获取失败: {res_info}")

        # 如果已经是启动状态，直接结束
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在运行，无需重复启动。")
            notify(f"✅ 服务器运行正常\n当前状态: [{init_status.upper()}]")
            await browser.close()
            sys.exit(0)

        # 3. 停止状态，立刻执行启动指令
        print(f"⚡ 步骤 3: 状态为 [{init_status}]，立即发送开机指令...")
        power_res = await run_fetch("power", "POST", {"signal": "start"})
        print(f"  └─ 开机指令执行结果: HTTP {power_res.get('status', 0)}")

        # 4. 轮询确认状态
        print("⏳ 步骤 4: 轮询确认是否启动成功...")
        final_status = init_status
        for i in range(1, 6):
            await asyncio.sleep(4)
            check_info = await run_fetch("resources", "GET")
            if check_info.get("ok") and "data" in check_info:
                curr = check_info["data"].get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                final_status = curr
                if curr in ["running", "starting"]:
                    break

        await browser.close()

        # 5. 通知结果
        if final_status in ["running", "starting"]:
            notify(f"🚀 服务器已成功唤醒！\n最新状态: [{final_status.upper()}]")
            sys.exit(0)
        else:
            notify(f"❌ 服务器唤醒可能失败\n初始状态: [{init_status}]\n最终状态: [{final_status}]")
            sys.exit(1)

def main():
    asyncio.run(async_main())

if __name__ == "__main__":
    main()
