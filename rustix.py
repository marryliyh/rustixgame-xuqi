import asyncio
import requests
import os
import json
import sys
from playwright.async_api import async_playwright

# --- 从环境变量读取敏感信息 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")

BASE_URL = "https://my.rustix.me/"
EXACT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"

def send_tg_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("💡 [TG] TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    formatted_text = f"*✅ rustix.me 服务器自动启动通知*\n\n{text}"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ [TG] 发送 Telegram 消息失败: {e}")

def format_cookies_for_playwright(raw_cookies):
    cleaned_cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c.get("name"),
            "value": c.get("value"),
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
            "secure": c.get("secure", True),
            "httpOnly": c.get("httpOnly", False)
        }
        same_site = str(c.get("sameSite", "")).lower()
        if same_site in ["no_restriction", "none"]:
            cookie["sameSite"] = "None"
        elif same_site == "lax":
            cookie["sameSite"] = "Lax"
        elif same_site == "strict":
            cookie["sameSite"] = "Strict"
            
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
            
        cleaned_cookies.append(cookie)
    return cleaned_cookies

async def run_automation():
    if not COOKIES_JSON:
        print("❌ 错误: 未配置 COOKIES_JSON 环境变量。")
        sys.exit(1)

    try:
        raw_cookies = json.loads(COOKIES_JSON)
        formatted_cookies = format_cookies_for_playwright(raw_cookies)
    except Exception as e:
        print(f"❌ Cookie JSON 解析失败: {e}")
        sys.exit(1)

    print("\n==========================================")
    print("🚀 开始通过 Cookie 免登录启动服务器")
    print("==========================================")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=EXACT_USER_AGENT,
            locale="ru-RU"
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        print("🔑 正在注入 Cookie 凭证...")
        await context.add_cookies(formatted_cookies)

        page = await context.new_page()

        print(f"🌐 1/3 访问面板主页: {BASE_URL}")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"⚠️ 页面打开慢，继续尝试: {e}")

        await asyncio.sleep(4)

        if "login" in page.url or "auth" in page.url:
            print("❌ Cookie 已失效，重定向回了登录页！")
            await page.screenshot(path="cloudflare_block.png")
            raise Exception("Cookie 已失效，请在浏览器重新导出 Cookie！")

        print("✅ 登录凭证有效，已成功进入后台！")

        # 2. 从服务器列表进入控制台
        print("🌐 2/3 正在匹配并进入服务器控制台...")
        
        # 匹配 Pterodactyl 架构面板的服务器链接 (例如包含 /server/ 的卡片)
        server_link = await page.query_selector('a[href*="/server/"]')
        if not server_link:
            # 备用选择器：点击包含 jack 名字的服务器卡片
            server_link = await page.query_selector('text=jack') or await page.query_selector('div[class*="server"]')

        if server_link:
            print("✅ 找到服务器列表项，点击进入控制台...")
            await server_link.click()
            await asyncio.sleep(5)
        else:
            print("ℹ️ 未在首页匹配到服务器链接，可能已直接处于控制台页面，继续检测按钮...")

        # 3. 检查并点击【Старт】绿色开机按钮
        print("🔍 3/3 正在定位控制台【Старт / 开始】按钮...")
        
        start_btn = None
        try:
            # 优先精准寻找包含 Старт / Start 的按钮
            start_btn_locator = page.locator('button:has-text("Старт"), button:has-text("Start")').first
            await start_btn_locator.wait_for(state="visible", timeout=15000)
            start_btn = start_btn_locator
        except Exception:
            print("⚠️ 未直接找到绿色 [Старт] 按钮，尝试保底匹配方式...")

        # 再次获取页面内文本确认当前运行状态
        page_text = (await page.locator('body').inner_text()).lower()

        if "включён" in page_text or "online" in page_text or "running" in page_text:
            print("🎉 服务器当前已经是【Включён / 运行中】状态，无需重复启动。")
            send_tg_message("🖥️ 服务器状态: *Включён (运行中)*\n💡 操作结果: 检查完成，服务器正在正常运行。")
        else:
            if start_btn:
                await start_btn.click()
                print("🚀 已成功点击绿色【Старт】开机按钮！")
            else:
                # 最后的保底强制点击
                await page.click('//button[contains(., "Старт") or contains(., "Start")]')
                print("🚀 保底选择器：已点击开机按钮！")

            print("⏳ 等待 10 秒确认状态...")
            await asyncio.sleep(10)

            # 再次截图保存状态
            await page.screenshot(path="after_start.png")
            print("🎉 开机指令已成功提交！")
            send_tg_message("🖥️ 服务器状态: *开机指令已成功发出 🚀*\n💡 服务器正在启动中。")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_automation())
