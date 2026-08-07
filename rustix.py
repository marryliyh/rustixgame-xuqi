import os
import sys
import time
import json
import asyncio
import subprocess

# 补全必要依赖
for pkg in ["requests", "playwright"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

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


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Xvfb 桌面真机渲染模式)...")

    proxy_server = PROXY_URL.replace("socks5h://", "socks5://") if PROXY_URL else None
    proxy_config = {"server": proxy_server} if proxy_server else None

    async with async_playwright() as p:
        # 核心修复点：使用 headless=False 配合 xvfb，以真正的图形界面浏览器运行，突破 Cloudflare TLS 指纹封锁
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
        )
        page = await context.new_page()

        # 抹除 WebDriver 特征变量
        await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        # 1. 载入控制台主页通过 Cloudflare 校验
        print("🌐 步骤 1: 访问 Rustix 控制台主页突破防火墙...")
        try:
            res = await page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
            print(f"  └─ 主页响应 HTTP 状态码: {res.status if res else '200'}")
        except Exception as e:
            print(f"  └─ 页面加载提示: {e}")

        # 留出时间供 Cloudflare 人机验证自动解算
        print("⏳ 等待 Cloudflare 前端盾解算...")
        await asyncio.sleep(8)
        await page.screenshot(path="screenshots/cf_passed.png")

        # 同源 Fetch 函数定义 (在已通畅的页面上下文内发起通信，绕过 CORS 和外部网络断连)
        get_status_js = """
        async ({ serverId, apiKey }) => {
            try {
                const res = await fetch('/api/client/servers/' + serverId + '/resources', {
                    method: 'GET',
                    headers: {
                        'Authorization': 'Bearer ' + apiKey,
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    }
                });
                const text = await res.text();
                try {
                    return { ok: true, status: res.status, json: JSON.parse(text) };
                } catch(e) {
                    return { ok: false, status: res.status, raw: text };
                }
            } catch (err) {
                return { ok: false, error: err.toString() };
            }
        }
        """

        # 2. 检查服务器是否已经启动
        print("🔍 步骤 2: 检查服务器当前运行状态...")
        res_info = await page.evaluate(get_status_js, {"serverId": SERVER_ID, "apiKey": API_KEY})

        init_status = "unknown"
        if res_info.get("ok") and "json" in res_info:
            init_status = res_info["json"].get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 当前服务器状态为: [{init_status}]")
        else:
            print(f"  └─ 读取状态失败，详细返回: {res_info}")

        # 如果已经是启动或正在启动状态，不用点击启动，直接通知并结束
        if init_status in ["running", "starting"]:
            print("🎉 服务器处于启动状态，无需执行开机操作。")
            notify(f"🚀 Rustix 服务器已在运行中！\n\n- 当前状态: [{init_status.upper()}]")
            await browser.close()
            sys.exit(0)

        # 3. 如果处于停止/离线状态，执行启动指令
        print(f"⚡ 步骤 3: 当前状态为 [{init_status}]，下发 [start] 启动指令...")
        send_start_js = """
        async ({ serverId, apiKey }) => {
            try {
                const res = await fetch('/api/client/servers/' + serverId + '/power', {
                    method: 'POST',
                    headers: {
                        'Authorization': 'Bearer ' + apiKey,
                        'Accept': 'application/json',
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ signal: 'start' })
                });
                return { ok: true, status: res.status };
            } catch (err) {
                return { ok: false, error: err.toString() };
            }
        }
        """

        power_res = await page.evaluate(send_start_js, {"serverId": SERVER_ID, "apiKey": API_KEY})
        power_status = power_res.get("status", 0)
        print(f"  └─ 启动指令响应状态码: HTTP {power_status}")

        # 4. 轮询确认状态更新
        print("⏳ 步骤 4: 轮询确认服务器状态变更...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            check_info = await page.evaluate(get_status_js, {"serverId": SERVER_ID, "apiKey": API_KEY})
            if check_info.get("ok") and "json" in check_info:
                curr = check_info["json"].get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break

        await browser.close()

        # 5. 结果判定与通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 启动成功！\n\n- 最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif power_status in [200, 204]:
            notify(f"🚀 Rustix 启动指令已成功送达！\n\n- 接口响应: HTTP {power_status}")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 启动失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
