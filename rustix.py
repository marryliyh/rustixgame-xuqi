import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全必要依赖
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

    print("🚀 启动 Rustix 保活流程 (Chromium 上下文原生 API 请求模式)...")

    proxy_server = PROXY_URL.replace("socks5h://", "socks5://") if PROXY_URL else None
    proxy_config = {"server": proxy_server} if proxy_server else None

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
        )
        page = await context.new_page()

        # 1. 打开主页解算 Cloudflare 防火墙盾
        print("🌐 步骤 1: 访问 Rustix 控制台解算 Cloudflare 防火墙...")
        try:
            res = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
            print(f"  └─ 主页响应 HTTP 状态码: {res.status if res else 'UNKNOWN'}")
        except Exception as e:
            print(f"  └─ 页面加载提示: {e}")

        # 等待 Cloudflare 验证完成并写入 Cookies
        print("⏳ 等待 Cloudflare 验证完成...")
        await page.wait_for_timeout(8000)
        await page.screenshot(path="screenshots/cf_passed.png")

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # 2. 查询服务器初始状态 (使用 context.request 走 Chromium 原生协议栈，无 CORS 限制，不受 curl SSL 报错影响)
        print("🔍 步骤 2: 请求 API 探测服务器当前状态...")
        init_status = "unknown"
        try:
            api_res = await context.request.get(
                f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
                headers=headers,
            )
            print(f"  └─ API HTTP 状态码: {api_res.status}")
            if api_res.status == 200:
                data = await api_res.json()
                init_status = data.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 当前服务器状态: [{init_status}]")
            else:
                body = await api_res.text()
                print(f"  └─ 响应片段: {body[:150]}")
        except Exception as e:
            print(f"  └─ API 请求异常: {e}")

        # 若已经在运行，直接成功退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在正常运行中，无需重启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            await browser.close()
            sys.exit(0)

        # 3. 发送开机指令
        print("⚡ 步骤 3: 下发 [start] 电源指令...")
        power_success = False
        p_code = 0
        try:
            p_res = await context.request.post(
                f"{BASE_URL}/api/client/servers/{SERVER_ID}/power",
                headers=headers,
                data=json.dumps({"signal": "start"}),
            )
            p_code = p_res.status
            print(f"  └─ 电源指令 HTTP 状态码: {p_code}")
            if p_code in [200, 204]:
                power_success = True
        except Exception as e:
            print(f"  └─ 发送指令异常: {e}")

        # 4. 轮询确认状态
        print("⏳ 步骤 4: 轮询确认服务器状态变更...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            try:
                check_res = await context.request.get(
                    f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
                    headers=headers,
                )
                if check_res.status == 200:
                    curr_data = await check_res.json()
                    curr = curr_data.get("attributes", {}).get("current_state", "unknown")
                    print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                    if curr in ["running", "starting"]:
                        final_status = curr
                        break
                else:
                    print(f"  └─ 轮询第 {i}/5 次 HTTP {check_res.status}")
            except Exception as e:
                print(f"  └─ 轮询网络异常: {e}")

        await browser.close()

        # 5. 结果判定与 Telegram 通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif power_success:
            notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应: HTTP {p_code}\n- 开机指令已成功送到服务端后台。")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
