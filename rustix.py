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

# 有 Cookie 后直接访问控制台主页，不要去 /auth/login
BASE_URL = "https://my.rustix.me/"

# ⚠️ 必须与你导出 Cookie 时的浏览器 User-Agent 完全一致，避免触发 Mitelis 防火墙校验
EXACT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"

def send_tg_message(text):
    """发送带 Markdown 格式的 Telegram 消息"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("💡 [TG] TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    formatted_text = f"*✅ rustix.me 服务器自动启动/重启通知*\n\n{text}"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ [TG] 发送 Telegram 消息失败: {e}")

def format_cookies_for_playwright(raw_cookies):
    """清洗 Cookie-Editor 导出的 Cookie 格式以兼容 Playwright"""
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
        # 处理 SameSite 映射
        same_site = str(c.get("sameSite", "")).lower()
        if same_site in ["no_restriction", "none"]:
            cookie["sameSite"] = "None"
        elif same_site == "lax":
            cookie["sameSite"] = "Lax"
        elif same_site == "strict":
            cookie["sameSite"] = "Strict"
            
        # 转换过期时间
        if "expirationDate" in c:
            cookie["expires"] = int(c["expirationDate"])
            
        cleaned_cookies.append(cookie)
    return cleaned_cookies

async def run_automation():
    if not COOKIES_JSON:
        print("❌ 错误: 未配置 COOKIES_JSON 环境变量，请检查 GitHub Secrets。")
        sys.exit(1)

    try:
        raw_cookies = json.loads(COOKIES_JSON)
        formatted_cookies = format_cookies_for_playwright(raw_cookies)
    except Exception as e:
        print(f"❌ Cookie JSON 解析失败: {e}")
        sys.exit(1)

    print("\n==========================================")
    print("🚀 开始通过免登录 Cookie 凭证处理任务")
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

        # 擦除 webdriver 自动化标记
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        # 1. 注入 Cookie
        print("🔑 正在向浏览器 Context 注入 Cookie 凭证...")
        await context.add_cookies(formatted_cookies)

        page = await context.new_page()

        # 2. 直接访问主页面板
        print(f"🌐 1/3 正在使用 Session 访问主页: {BASE_URL}")
        try:
            await page.goto(BASE_URL, wait_until="networkidle", timeout=60000)
        except Exception:
            await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

        await asyncio.sleep(5)

        # 检查是否成功处于登录状态（判断 URL 或页面内容）
        current_url = page.url
        if "login" in current_url or "auth" in current_url:
            print("❌ Cookie 已失效或指纹未匹配，被重定向回了登录页面！")
            await page.screenshot(path="cloudflare_block.png")
            raise Exception("Cookie 登录凭证失效，请重新导出最新的 Cookie。")

        print("✅ 成功跳过登录界面进入后台！")

        # 3. 查找并管理服务器
        print("🌐 2/3 正在匹配管理服务器按钮...")
        manage_btn = await page.query_selector('text=Управлять сервером, text=管理服务器, a[href*="/server/"]')
        if manage_btn:
            await manage_btn.click()
            print("✅ 已点击【Управлять сервером / 管理服务器】按钮")
            await asyncio.sleep(5)
        else:
            print("ℹ️ 未能匹配到 [Управлять сервером] 按钮，尝试直接检测控制台组件...")

        # 4. 检测控制台及运行状态
        print("🔍 3/3 正在检查服务器控制台状态...")
        try:
            await page.wait_for_selector('text=Консоль, text=Console, text=Старт, text=Рестарт', timeout=30000)
        except Exception:
            print("⚠️ 未识别到控制台核心按钮，保存当前页面截图 error_page_load.png ...")
            await page.screenshot(path="error_page_load.png")

        await asyncio.sleep(2)
        page_text = (await page.locator('body').inner_text()).lower()

        # 根据俄语面板状态判断
        if "включён" in page_text or "online" in page_text or "running" in page_text:
            print("🎉 服务器当前状态：【Включён / 运行中】")
            send_tg_message("🖥️ 服务器状态: *Включён (运行中)*\n💡 操作结果: 免登录续据/状态正常，无需重复启动。")
        else:
            print("⚠️ 服务器当前状态：【Отключён / 已关机】，准备发送启动指令...")
            
            start_btn = await page.query_selector('button:has-text("Старт"), button:has-text("Start"), button:has-text("开始")')
            restart_btn = await page.query_selector('button:has-text("Рестарт"), button:has-text("Restart"), button:has-text("重启")')

            target_btn = start_btn or restart_btn
            if target_btn:
                await target_btn.click()
                print("✅ 已成功点击【Старт 开始 / Рестарт 重启】按钮！")
            else:
                try:
                    await page.click('//button[contains(., "Старт") or contains(., "Рестарт")]')
                    print("✅ 保底选择器：已点击控制按钮！")
                except Exception as e:
                    print(f"⚠️ 保底点击未成功: {e}")

            # 处理二次确认弹窗
            await asyncio.sleep(2)
            confirm_btn = await page.query_selector("//button[contains(text(), '确认') or contains(text(), 'Yes') or contains(text(), 'Да')]")
            if confirm_btn:
                await confirm_btn.click()
                print("✅ 已点击二次确认弹窗")

            print("⏳ 等待 15 秒确认服务器启动状态...")
            await asyncio.sleep(15)

            new_text = (await page.locator('body').inner_text()).lower()
            if "включён" in new_text or "online" in new_text or "running" in new_text or "запуск" in new_text:
                print("🎉 服务器成功启动！状态已更新为 Включён")
                send_tg_message("🖥️ 服务器状态: *已成功从 Отключён 启动 ✅*\n🚀 当前状态: Включён (运行中)")
            else:
                print("💡 已成功触发启动点击，服务器正在后台初始化开机...")
                send_tg_message("🖥️ 服务器状态: *启动指令已发出 🚀*\n💡 请稍后在面板手动查看。")

        print("✅ 处理完毕。")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_automation())
