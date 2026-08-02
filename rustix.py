import asyncio
import requests
import os
import json
import sys
import re
from playwright.async_api import async_playwright

# --- 从环境变量读取敏感信息 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")

BASE_URL = "https://my.rustix.me/"
EXACT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"

def send_tg_message(text):
    """发送带 Markdown 格式的 Telegram 消息"""
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
        name = c.get("name", "")
        
        # 💡 核心修复：剔除绑定了本机 IP 的 Mitelis WAF 校验 Cookie
        if name.startswith("mit_ck"):
            print(f"🧹 自动过滤与旧 IP 绑定的 WAF Cookie: {name}")
            continue

        cookie = {
            "name": name,
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
        raise Exception("未配置 COOKIES_JSON 环境变量，请检查 GitHub Secrets 配置！")

    try:
        raw_cookies = json.loads(COOKIES_JSON)
        formatted_cookies = format_cookies_for_playwright(raw_cookies)
    except Exception as e:
        raise Exception(f"COOKIES_JSON 解析失败: {str(e)}")

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

        print("🔑 正在注入账号 Session Cookie...")
        await context.add_cookies(formatted_cookies)

        page = await context.new_page()

        print(f"🌐 1/3 访问面板主页: {BASE_URL}")
        try:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"⚠️ 页面加载较慢: {e}")

        # ⏳ 给 Mitelis 防火墙 5-8 秒时间在 Actions 的新 IP 上完成 JS 挑战和自动重定向
        print("⏳ 等待 Mitelis WAF 完成 Actions IP 的 JS 校验与页面跳转...")
        for _ in range(6):
            content = await page.content()
            if "Access denied" not in content and "Just a moment" not in content:
                break
            await asyncio.sleep(2)

        # 检查是否依然被 Access Denied
        current_content = await page.content()
        if "Access denied" in current_content:
            await page.screenshot(path="access_denied_block.png")
            raise Exception("Mitelis 防火墙拒绝了 Actions IP 的访问，截图保存为 `access_denied_block.png`。")

        # 检查凭证是否失效
        if "login" in page.url or "auth" in page.url:
            await page.screenshot(path="login_failed.png")
            raise Exception("Cookie 已失效，面板将其重定向回了登录页，请在浏览器重新导出最新 Cookie！")

        print("✅ 防火墙与账号 Session 校验成功，已成功进入后台！")

        print("⏳ 等待面板 API 异步渲染服务器数据...")
        try:
            await page.wait_for_selector('a[href*="/server/"], button', timeout=20000)
        except Exception:
            print("⚠️ 页面渲染等待超时，尝试直接分析当前页面 DOM...")

        # 2. 如果处于列表页，点击卡片进入控制台
        if "/server/" not in page.url:
            print("🌐 2/3 处于服务器列表页，准备点击进入控制台...")
            server_card = page.locator('a[href*="/server/"]').first
            if await server_card.count() == 0:
                server_card = page.locator('text=jack').first

            if await server_card.count() > 0:
                print("✅ 匹配到服务器卡片，正在点击进入...")
                await server_card.click()
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(5)
            else:
                print("ℹ️ 未能找到服务器卡片，尝试直接在当前页面寻找控制按键...")
        else:
            print("✅ 当前已直接位于服务器控制台页面。")

        # 3. 定位并点击【Старт】开机按钮
        print("🔍 3/3 正在定位【Старт / 开始】按钮...")

        start_btn = page.locator('button').filter(has_text=re.compile(r'Старт|Start|开始', re.IGNORECASE)).first

        if await start_btn.count() == 0:
            start_btn = page.locator('button.bg-green-600, button.btn-success').first

        btn_count = await start_btn.count()
        if btn_count == 0:
            await page.screenshot(path="button_not_found.png")
            raise Exception("在控制台页面未能找到【Старт / 开始】按钮，截图已保存为 `button_not_found.png`。")

        # 判断当前状态
        page_text = (await page.locator('body').inner_text()).lower()
        if "включён" in page_text or "running" in page_text or "online" in page_text:
            print("🎉 服务器当前已经是【Включён / 运行中】状态！")
            send_tg_message("🖥️ 服务器状态: *Включён (运行中)*\n💡 检查完成，服务器当前正在正常运行中，无需重复开机。")
        else:
            print("🚀 正在点击【Старт】开机按钮...")
            await start_btn.click(timeout=10000)
            await asyncio.sleep(8)
            
            await page.screenshot(path="after_click.png")
            print("🎉 开机指令已成功提交！")
            send_tg_message("🖥️ 服务器状态: *开机指令已成功发出 🚀*\n💡 正在后台初始化开机，请稍后在面板查看。")

        await browser.close()

async def main():
    try:
        await run_automation()
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ 脚本运行出错: {error_msg}")
        send_tg_message(f"⚠️ *脚本运行出现错误！*\n\n错误详情:\n`{error_msg}`")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
