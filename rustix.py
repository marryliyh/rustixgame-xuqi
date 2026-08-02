import asyncio
import requests
import os
import json
import sys
import re
import socket
from urllib.parse import urlparse
from playwright.async_api import async_playwright

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")
PROXY_URL = os.environ.get("PROXY_URL", "socks5://127.0.0.1:10808")

CONSOLE_URL = "https://my.rustix.me/server/226fd977/console"
EXACT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"

def send_tg_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("💡 [TG] TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {
        "chat_id": TG_CHAT_ID, 
        "text": f"✅ rustix.me 服务器自动启动/保活通知\n\n{text}"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"❌ [TG] 发送 Telegram 消息失败: Status {res.status_code}, Response: {res.text}")
        else:
            print("✅ [TG] Telegram 通知已成功发送！")
    except Exception as e:
        print(f"❌ [TG] 发送 Telegram 消息异常: {e}")

def check_proxy_port(proxy_url):
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 10809
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False

def format_cookies_for_playwright(raw_cookies):
    cleaned_cookies = []
    for c in raw_cookies:
        cookie = {
            "name": c.get("name", ""),
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
    print("🚀 开始访问 Rustix 服务器控制台页面")
    print("==========================================")

    async with async_playwright() as p:
        launch_options = {
            "headless": True,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768"
            ]
        }

        if PROXY_URL and check_proxy_port(PROXY_URL):
            print(f"🌐 节点代理正常运行，正在通过 HTTP 隧道代理访问: {PROXY_URL}")
            launch_options["proxy"] = {"server": PROXY_URL}
        else:
            raise Exception("本地 sing-box 代理未正常启动 (端口 10809 未连通)！")

        browser = await p.chromium.launch(**launch_options)
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

        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"🌐 访问控制台: {CONSOLE_URL} (尝试 {attempt + 1}/{max_retries})...")
                await page.goto(CONSOLE_URL, wait_until="commit", timeout=25000)
                break
            except Exception as e:
                print(f"⚠️ 页面响应延迟 ({e})，等待 5 秒重试...")
                if attempt == max_retries - 1:
                    raise Exception(f"通过代理连接控制台失败: {e}")
                await asyncio.sleep(5)

        print("⏳ 等待页面 DOM 加载与 Cloudflare/Mitelis 校验 (8秒)...")
        await asyncio.sleep(8)

        try:
            current_content = await page.content()
        except Exception:
            current_content = ""

        if "Access denied" in current_content:
            await page.screenshot(path="access_denied_block.png")
            raise Exception("提示 Access denied，请确认节点 IP 或更新最新 Cookie！")

        if "login" in page.url or "auth" in page.url:
            await page.screenshot(path="login_failed.png")
            raise Exception("Cookie 已失效，面板将其重定向回了登录页，请重新导出最新 Cookie！")

        print("✅ 防火墙与 Session 校验成功，已进入控制台！")

        print("⏳ 正在等待前端 WebSocket 与控制按钮完全加载渲染 (8秒)...")
        await asyncio.sleep(8)

        # Rustix 控制按钮为俄文：Старт（启动）、Рестарт（重启）、Стоп（停止）。
        # 页面显示“Включён”时 Старт 会禁用，因此优先点击可用的 Рестарт。
        print("⏳ 等待俄文控制按钮渲染（最长 40 秒）...")

        async def click_rustix_control():
            # 依次检查主页面和 iframe，并处理按钮文本被图标/span 分割的情况。
            for _ in range(20):
                for frame in page.frames:
                    buttons = frame.locator('button, [role="button"], a')
                    count = await buttons.count()
                    visible_texts = []
                    for i in range(min(count, 100)):
                        item = buttons.nth(i)
                        try:
                            text = " ".join((await item.inner_text()).split())
                            if text:
                                visible_texts.append(text)
                        except Exception:
                            continue

                    if visible_texts:
                        print("🔎 当前可点击控件文本:", " | ".join(visible_texts[:30]))

                    # 优先重启。截图中的实际俄文是 Рестарт。
                    for wanted, action_name in [
                        ("Рестарт", "Рестарт（重启）"),
                        ("Restart", "Restart（重启）"),
                        ("Старт", "Старт（启动）"),
                        ("Start", "Start（启动）"),
                    ]:
                        for i in range(min(count, 100)):
                            item = buttons.nth(i)
                            try:
                                text = " ".join((await item.inner_text()).split())
                                if text.casefold() != wanted.casefold():
                                    continue
                                if not await item.is_visible():
                                    continue
                                disabled = await item.is_disabled()
                                aria_disabled = (await item.get_attribute("aria-disabled") or "").lower() == "true"
                                if disabled or aria_disabled:
                                    print(f"⏭️ 跳过禁用按钮: {text}")
                                    continue
                                await item.scroll_into_view_if_needed()
                                await item.click(force=True, timeout=10000)
                                return action_name
                            except Exception as exc:
                                print(f"⚠️ 候选按钮 {wanted} 点击失败: {exc}")
                await page.wait_for_timeout(2000)
            return None

        action_name = await click_rustix_control()
        if action_name:
            print(f"🎉 已点击控制按钮: {action_name}")
            await page.wait_for_timeout(6000)
            await page.screenshot(path="after_click.png", full_page=True)
            send_tg_message(f"🖥️ 服务器控制指令已发出: {action_name} 🚀")
        else:
            await page.screenshot(path="button_not_found.png", full_page=True)
            with open("button_not_found.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            raise Exception("控制台已打开，但没有找到可用的俄文按钮 Рестарт/Старт。已保存诊断文件。")

        await browser.close()

async def main():
    try:
        await run_automation()
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ 脚本运行出错: {error_msg}")
        send_tg_message(f"⚠️ 脚本运行出现错误！\n\n错误详情:\n{error_msg}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
