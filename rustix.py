import asyncio
import os
import sys

from playwright.async_api import async_playwright
import requests

# 环境变量获取
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")

SERVER_ID = "226fd977"
CONSOLE_URL = f"https://my.rustix.me/server/{SERVER_ID}/console"
API_POWER_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/power"


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
    print("🌐 启动 Google Chrome 浏览器并加载防护屏障...")

    async with async_playwright() as p:
        # 核心修改点：加入了极强的反检测和防断连参数
        launch_kwargs = {
            "channel": "chrome",
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--ignore-certificate-errors",  # 防止代理自签证书导致 TLS 握手断开
                "--ignore-certificate-errors-spki-list",
                "--disable-infobars",
                "--window-size=1366,768"
            ],
        }
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        browser = await p.chromium.launch(**launch_kwargs)
        
        # 强行指定 User-Agent 伪装
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            ignore_https_errors=True
        )
        page = await context.new_page()

        print(f"⏳ 正在打开服务器控制台页面: {CONSOLE_URL}")
        
        # 核心修改点：加入完整的 Try-Except 重试机制，防止断连直接崩溃脚本
        max_retries = 3
        page_loaded = False
        
        for attempt in range(max_retries):
            try:
                print(f"🔄 尝试加载页面 (第 {attempt + 1}/{max_retries} 次)...")
                # wait_until="domcontentloaded" 防止被 WAF 持续加载卡死
                await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
                page_loaded = True
                print("✅ 页面基础加载成功！")
                break
            except Exception as e:
                print(f"⚠️ 第 {attempt + 1} 次加载异常: {e}")
                if attempt < max_retries - 1:
                    print("⏳ 等待 5 秒后进行下一次重试...")
                    await page.wait_for_timeout(5000)
                else:
                    await page.screenshot(path="error_fatal_load.png", full_page=True)
                    print("❌ 重试次数用尽，页面加载彻底失败。")
                    raise RuntimeError("网络连接断开次数过多，无法加载页面。")

        if not page_loaded:
            sys.exit(1)

        # 留出 8 秒时间给 Mitelis 盾进行 JS 验证与页面渲染
        print("⏳ 等待 8 秒让 Mitelis 完成 JS 验证与翼龙面板渲染...")
        await page.wait_for_timeout(8000)

        # 检查是否被 WAF 硬封禁
        body_text = await page.locator("body").inner_text()
        if "access denied" in body_text.lower():
            await page.screenshot(path="00_access_denied.png", full_page=True)
            raise RuntimeError("Mitelis 防火墙拒绝访问，可能当前节点 IP 仍处于冷却期！")

        print("🔍 尝试通过 Chrome 界面按钮点击 [Start / Restart]...")
        clicked = False

        # 寻找控制台 UI 上的开机/重启按钮
        selectors = [
            "button:has-text('Start')", "button:has-text('Restart')",
            "button:has-text('Старт')", "button:has-text('Рестарт')",
            "button:has-text('启动')", "button:has-text('重启')"
        ]

        for selector in selectors:
            btn = page.locator(selector).first
            if await btn.is_visible():
                btn_text = await btn.inner_text()
                print(f"🎉 成功找到 UI 按钮 [{btn_text.strip()}]，准备点击...")
                await btn.click(force=True)
                clicked = True
                break

        # 如果界面按钮未找到，在同源 Chrome 页面上下文内部发起 API 触发
        if not clicked:
            print("ℹ️ UI 按钮暂未捕捉到，在同源 Chrome 内部发送电源指令...")
            api_result = await page.evaluate(
                """async ({ url, key }) => {
                    try {
                        const headers = { 'Content-Type': 'application/json', 'Accept': 'application/json' };
                        if (key) headers['Authorization'] = 'Bearer ' + key;
                        const res = await fetch(url, {
                            method: 'POST',
                            headers: headers,
                            body: JSON.stringify({ signal: 'start' })
                        });
                        return res.status;
                    } catch (e) {
                        return 'err:' + e.message;
                    }
                }""",
                {"url": API_POWER_URL, "key": API_KEY},
            )
            print(f"⚡ 页面内同源 API 请求响应码: {api_result}")
            if api_result == 204 or api_result == 200:
                clicked = True

        print("⏳ 等待 10 秒以捕获服务器最新运行状态...")
        await page.wait_for_timeout(10000)

        await page.screenshot(path="02_after_click_status.png", full_page=True)
        print("📸 状态截图已保存: 02_after_click_status.png")

        if clicked:
            notify("🚀 Rustix 保活成功！开机/重启指令已成功发送，实时截图已存入 Actions Artifacts。")
            await browser.close()
            sys.exit(0)
        else:
            notify("❌ 没能成功点击或触发开机指令，请查看 Artifacts 截图。")
            await browser.close()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
