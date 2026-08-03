import asyncio
import os
import sys

from playwright.async_api import async_playwright
import requests

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")

SERVER_ID = "226fd977"
CONSOLE_URL = f"https://my.rustix.me/server/{SERVER_ID}/console"
API_POWER_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/power"
API_STATUS_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/resources"


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
        print("[TG] 通知发送成功" if r.ok else f"[TG] 通知失败: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[TG] 通知异常: {exc}")


async def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🌐 启动 Google Chrome 浏览器并加载防护屏障...")

    async with async_playwright() as p:
        launch_kwargs = {
            "channel": "chrome",
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        }
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        # 1. 打开主页，让真实 Chrome 跑完 Mitelis JS 验证并获得 Cookie
        print("⏳ 正在通过 Mitelis JS 防火墙...")
        await page.goto("https://my.rustix.me", wait_until="networkidle", timeout=60000)
        await page.wait_for_timeout(5000)

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # 2. 使用 context.request 发起请求（继承浏览器 WAF Cookie，且不触发 OPTIONS 预检）
        print("🔍 正在获取服务器当前状态...")
        status_res_obj = await context.request.get(API_STATUS_URL, headers=headers)
        
        status_res = "unknown"
        if status_res_obj.status == 200:
            data = await status_res_obj.json()
            status_res = data.get("attributes", {}).get("current_state", "unknown")
        else:
            print(f"⚠️ 查询状态返回 HTTP {status_res_obj.status}")

        print(f"📊 服务器当前状态: [{status_res}]")

        target_signal = "restart" if status_res == "running" else "start"
        print(f"⚡ 通过 API 发送 [{target_signal}] 指令...")

        # 3. 发送电源指令
        power_res_obj = await context.request.post(
            API_POWER_URL,
            headers=headers,
            data={"signal": target_signal},
        )

        power_status = power_res_obj.status

        if power_status == 204:
            print(f"🎉 成功发送 [{target_signal}] 开机/重启指令！")
            
            # 4. 跳转控制台页面，等待 10 秒后拍摄实时截图
            print("🌐 正在跳转控制台页面并拍摄实时状态截图...")
            await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(10000)
            
            await page.screenshot(path="02_after_click_status.png", full_page=True)
            print("📸 状态截图已保存: 02_after_click_status.png")

            notify(f"🚀 API 指令 [{target_signal}] 发送成功！服务器状态截图已打包存入 Actions Artifacts。")
            await browser.close()
            sys.exit(0)
        else:
            print(f"❌ 发送指令失败，API 返回 HTTP {power_status}")
            body_text = await power_res_obj.text()
            print(f"📄 响应内容: {body_text[:300]}")
            await page.screenshot(path="error_api_failed.png", full_page=True)
            notify(f"❌ 保活失败：API 响应错误 HTTP {power_status}")
            await browser.close()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
