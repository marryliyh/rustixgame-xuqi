import asyncio
import requests
import os
import json
import sys
import re
from playwright.async_api import async_playwright

# --- 从环境变量读取配置 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")
PROXY_URL = os.environ.get("PROXY_URL")

# 直接访问你的特定服务器控制台页面
CONSOLE_URL = "https://my.rustix.me/server/226fd977/console"
EXACT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0"

def send_tg_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("💡 [TG] TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    formatted_text = f"*✅ rustix.me 服务器自动启动/保活通知*\n\n{text}"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ [TG] 发送 Telegram 消息失败: {e}")

def format_cookies_for_playwright(raw_cookies):
    cleaned_cookies = []
    for c in raw_cookies:
        name = c.get("name", "")
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
    print("🚀 开始访问 Rustix 服务器控制台页面")
    print("==========================================")

    async with async_playwright() as p:
        launch_options = {
            "headless": False,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768"
            ]
        }

        if PROXY_URL:
            print(f"🌐 正在通过代理访问: {PROXY_URL}")
            launch_options["proxy"] = {"server": PROXY_URL}

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

        # 带有重试机制的页面加载 (应对代理建立延迟)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"🌐 直接访问控制台: {CONSOLE_URL} (尝试 {attempt + 1}/{max_retries})...")
                await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=45000)
                break
            except Exception as e:
                print(f"⚠️ 页面访问延迟 ({e})，等待 5 秒后重试...")
                if attempt == max_retries - 1:
                    raise Exception(f"通过代理连接控制台失败: {e}")
                await asyncio.sleep(5)

        # ⏳ 安全循环等待 WAF 响应与页面渲染
        print("⏳ 等待页面加载与 WAF 自动响应...")
        for _ in range(10):
            try:
                content = await page.content()
                if "Access denied" not in content and "Just a moment" not in content:
                    break
            except Exception:
                pass
            await asyncio.sleep(2)

        # 检查是否被 WAF 拦截或退回登录页
        try:
            current_content = await page.content()
        except Exception:
            current_content = ""

        if "Access denied" in current_content:
            await page.screenshot(path="access_denied_block.png")
            raise Exception("代理 IP 被 Mitelis 防火墙拦截 (Access denied)。请尝试更换 Secret NODE_LINK 中的节点！")

        if "login" in page.url or "auth" in page.url:
            await page.screenshot(path="login_failed.png")
            raise Exception("Cookie 已失效，面板将其重定向回了登录页，请重新导出最新 Cookie！")

        print("✅ 防火墙与 Session 校验成功，已进入控制台！")

        # 等待控制台按钮加载出来
        print("⏳ 等待控制台操作按钮渲染...")
        try:
            await page.wait_for_selector('button', timeout=20000)
        except Exception:
            print("⚠️ 按钮渲染等待超时，尝试直接定位 DOM...")

        # 寻找 Старт (启动) 与 Рестарт (重启) 按钮
        start_btn = page.locator('button').filter(has_text=re.compile(r'Старт|Start|开', re.IGNORECASE)).first
        restart_btn = page.locator('button').filter(has_text=re.compile(r'Рестарт|Restart|重', re.IGNORECASE)).first

        # 获取页面纯文本进行状态判定
        body_text = (await page.locator('body').inner_text()).lower()
        is_running = "включён" in body_text or "running" in body_text or "online" in body_text

        if is_running:
            print("🎉 服务器当前状态为:【Включён / 运行中】")
            if await restart_btn.count() > 0:
                print("🚀 正在点击【Рестарт / 重启】按钮以维持服务状态...")
                await restart_btn.click(timeout=10000)
                await asyncio.sleep(8)
                await page.screenshot(path="after_click.png")
                print("🎉 重启指令提交完成！")
                send_tg_message("🖥️ 服务器状态: *Рестарт (重启指令已发出 🚀)*\n💡 服务器此前处于运行状态，已成功执行重启以保活。")
            else:
                await page.screenshot(path="after_click.png")
                send_tg_message("🖥️ 服务器状态: *Включён (正常运行中)*\n💡 服务器正常运行中，无需额外操作。")
        else:
            print("⚡ 服务器当前处于停止状态，正在提交启动指令...")
            if await start_btn.count() > 0:
                print("🚀 正在点击【Старт / 开始】开机按钮...")
                await start_btn.click(timeout=10000)
                await asyncio.sleep(8)
                await page.screenshot(path="after_click.png")
                print("🎉 开机指令提交完成！")
                send_tg_message("🖥️ 服务器状态: *Старт (开机指令已发出 🚀)*\n💡 已成功点击开机按钮，请稍后在面板查看。")
            elif await restart_btn.count() > 0:
                print("🚀 未找到 Старт 按钮，尝试点击【Рестарт / 重启】按钮...")
                await restart_btn.click(timeout=10000)
                await asyncio.sleep(8)
                await page.screenshot(path="after_click.png")
                send_tg_message("🖥️ 服务器状态: *Рестарт (重启指令已发出 🚀)*")
            else:
                await page.screenshot(path="button_not_found.png")
                raise Exception("未能在控制台页面匹配到【Старт】或【Рестарт】按钮，截图已保存为 `button_not_found.png`。")

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
