import asyncio
import requests
import os
import json
import sys
import re
import socket
from urllib.parse import urlparse, unquote
from playwright.async_api import async_playwright

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")

# 代理地址定义
HTTP_PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:10809")
SOCKS5_PROXY_URL = "socks5://127.0.0.1:10808"

CONSOLE_URL = "https://my.rustix.me/server/226fd977/console"
API_POWER_URL = "https://my.rustix.me/api/client/servers/226fd977/power"
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

def check_port(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False

def format_cookies_for_requests(raw_cookies):
    cookie_dict = {}
    xsrf_token = None
    for c in raw_cookies:
        name = c.get("name")
        val = c.get("value")
        if name and val:
            cookie_dict[name] = val
            if name == "XSRF-TOKEN":
                xsrf_token = unquote(val)
    return cookie_dict, xsrf_token

def try_api_power_trigger(raw_cookies):
    """【方案一】：直接通过 Pterodactyl API 发送开机/重启请求，绕过浏览器与前端渲染"""
    print("\n⚡ [方案一] 尝试通过面板 API 直连发送保活指令...")
    cookie_dict, xsrf_token = format_cookies_for_requests(raw_cookies)
    
    proxies = {
        "http": HTTP_PROXY_URL,
        "https": HTTP_PROXY_URL
    }
    
    headers = {
        "User-Agent": EXACT_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": CONSOLE_URL,
        "Origin": "https://my.rustix.me"
    }
    if xsrf_token:
        headers["X-XSRF-TOKEN"] = xsrf_token

    # 先试 restart，若失败试 start
    for action in ["restart", "start"]:
        try:
            res = requests.post(API_POWER_URL, json={"signal": action}, headers=headers, cookies=cookie_dict, proxies=proxies, timeout=15)
            if res.status_code in [200, 204]:
                print(f"🎉 API 响应成功 (Status {res.status_code})！已成功发出【{action}】指令！")
                send_tg_message(f"🖥️ 面板 API 直连成功 🚀\n💡 已通过后端 API 发送服务器 [{action}] 指令，秒级生效！")
                return True
            else:
                print(f"⚠️ API 返回状态码 {res.status_code}: {res.text[:100]}")
        except Exception as e:
            print(f"⚠️ API 请求异常: {e}")
            break

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

async def run_playwright_automation(raw_cookies):
    """【方案二】：Playwright 模拟浏览器（带 SOCKS5 代理及 SSL 错误检测）"""
    print("\n🌐 [方案二] 启动 Playwright 自动化浏览器...")
    formatted_cookies = format_cookies_for_playwright(raw_cookies)

    async with async_playwright() as p:
        # 优先选择 SOCKS5 代理避免 HTTP SSL 握手断开
        selected_proxy = None
        if check_port("127.0.0.1", 10808):
            selected_proxy = {"server": SOCKS5_PROXY_URL}
            print(f"🌐 检测到 SOCKS5 代理可用，使用: {SOCKS5_PROXY_URL}")
        elif check_port("127.0.0.1", 10809):
            selected_proxy = {"server": HTTP_PROXY_URL}
            print(f"🌐 使用 HTTP 代理: {HTTP_PROXY_URL}")
        else:
            raise Exception("代理端口 (10808 / 10809) 均未连通！")

        launch_options = {
            "headless": False,
            "proxy": selected_proxy,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--ignore-certificate-errors",
                "--disable-blink-features=AutomationControlled"
            ]
        }

        browser = await p.chromium.launch(**launch_options)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=EXACT_USER_AGENT,
            locale="ru-RU"
        )

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        await context.add_cookies(formatted_cookies)
        page = await context.new_page()

        print(f"🌐 打开控制台: {CONSOLE_URL} ...")
        await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=40000)
        await asyncio.sleep(6)

        content = await page.content()
        
        # 🚨 严格检测 SSL 及代理网络报错
        if "ERR_SSL_PROTOCOL_ERROR" in content or "This site can" in content or "ERR_CONNECTION_CLOSED" in content:
            await page.screenshot(path="ssl_error.png")
            raise Exception("代理 TLS 握手失败，浏览器拦截为 ERR_SSL_PROTOCOL_ERROR，请更换代理节点！")

        if "login" in page.url or "auth" in page.url:
            await page.screenshot(path="login_failed.png")
            raise Exception("Cookie 已失效，已跳至登录页，请更新 COOKIES_JSON！")

        print("✅ 网页成功加载！真正进入了 Rustix 控制台。")

        # 注入原生 JS 进行全节点暴力点击
        click_result = await page.evaluate("""
            () => {
                const candidates = Array.from(document.querySelectorAll('*'));
                for (const el of candidates) {
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (/^(Старт|Start|Рестарт|Restart)$/i.test(txt)) {
                        el.click();
                        return { success: true, text: txt };
                    }
                }
                return { success: false };
            }
        """)

        if click_result and click_result.get("success"):
            txt = click_result.get("text")
            print(f"🎉 成功匹配并触发控制按钮:【{txt}】！")
            await asyncio.sleep(5)
            await page.screenshot(path="after_click.png")
            send_tg_message(f"🖥️ 控制台按钮成功触发: {txt} 🚀")
        else:
            await page.screenshot(path="button_not_found.png")
            raise Exception("控制台渲染完成但未找到 Start/Restart 控件。")

        await browser.close()

async def main():
    if not COOKIES_JSON:
        print("❌ 未配置 COOKIES_JSON！")
        sys.exit(1)

    try:
        raw_cookies = json.loads(COOKIES_JSON)
    except Exception as e:
        print(f"❌ COOKIES_JSON 解析失败: {e}")
        sys.exit(1)

    # 1. 优先采用轻量快速的 API 直连方式
    if try_api_power_trigger(raw_cookies):
        print("✅ API 执行成功，程序退出。")
        sys.exit(0)

    # 2. 若 API 失败，自动降级回 Playwright 模拟
    print("⚠️ API 直连未成功，自动降级使用 Playwright 浏览器模式...")
    try:
        await run_playwright_automation(raw_cookies)
    except Exception as e:
        error_msg = str(e)
        print(f"\n❌ 运行出错: {error_msg}")
        send_tg_message(f"⚠️ 脚本运行出现错误！\n\n错误详情:\n{error_msg}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
