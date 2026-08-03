import asyncio
import base64
import json
import os
import re
import socket
import sys
from urllib.parse import urlparse, unquote
import requests

try:
    from curl_cffi import requests as cffi_requests
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

from playwright.async_api import async_playwright

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
COOKIES_JSON = os.getenv("COOKIES_JSON", "")
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")
CONSOLE_URL = os.getenv("CONSOLE_URL", "https://my.rustix.me/server/226fd977/console")
API_POWER_URL = "https://my.rustix.me/api/client/servers/226fd977/power"

BUTTONS = (
    ("start", ("Старт", "Start", "启动", "开机")),
    ("restart", ("Рестарт", "Restart", "Reboot", "重启")),
)


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动启动/保活通知\n\n{text}"},
            timeout=15,
        )
        print("[TG] 通知发送成功" if r.ok else f"[TG] 通知失败: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[TG] 通知异常: {exc}")


def parse_cookie_dict(value):
    raw = json.loads(value)
    if isinstance(raw, dict):
        raw = raw.get("cookies", [])
    if not isinstance(raw, list):
        raise ValueError("Cookie JSON 格式有误")
    
    cookie_dict = {}
    xsrf_token = None
    playwright_cookies = []

    for c in raw:
        name = c.get("name")
        val = c.get("value")
        if not name or val is None:
            continue
        
        cookie_dict[name] = str(val)
        if name == "XSRF-TOKEN":
            xsrf_token = unquote(str(val))

        item = {
            "name": name, "value": str(val),
            "path": c.get("path", "/"),
            "secure": bool(c.get("secure", True)),
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        if c.get("domain"):
            item["domain"] = c["domain"]
        else:
            item["url"] = "https://my.rustix.me"
        
        same = str(c.get("sameSite", "")).lower()
        if same in ("none", "no_restriction"):
            item["sameSite"] = "None"
        elif same == "lax":
            item["sameSite"] = "Lax"
        elif same == "strict":
            item["sameSite"] = "Strict"
        
        expires = c.get("expirationDate", c.get("expires"))
        if expires:
            try:
                item["expires"] = int(float(expires))
            except (TypeError, ValueError):
                pass
        playwright_cookies.append(item)

    return cookie_dict, xsrf_token, playwright_cookies


def extract_user_agent(cookie_dict):
    """自动从 mit_ck_p1 中反向解码真实的 User-Agent，避免触发 UA Mismatch 被封禁"""
    mit_p1 = cookie_dict.get("mit_ck_p1", "")
    if mit_p1:
        try:
            decoded = base64.b64decode(mit_p1).decode('utf-8', errors='ignore')
            parts = decoded.split('|')
            for part in parts:
                if part.startswith("Mozilla/5.0"):
                    print(f"🎯 成功从 mit_ck_p1 自动匹配解密出真实 UA: {part}")
                    return part
        except Exception as e:
            print(f"⚠️ 解析 Cookie 中的 UA 失败: {e}")
            
    default_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    print(f"ℹ️ 使用默认 UA: {default_ua}")
    return default_ua


def test_connectivity(use_proxy=True):
    mode = f"代理 ({PROXY_URL})" if use_proxy else "直连 (GitHub Actions Native)"
    print(f"🔍 正在测试针对 my.rustix.me 的 TLS 连通性 [{mode}]...")
    
    proxies = {"https": PROXY_URL, "http": PROXY_URL} if use_proxy else None
    try:
        if HAS_CFFI:
            r = cffi_requests.get("https://my.rustix.me", proxies=proxies, impersonate="chrome124", timeout=10)
        else:
            r = requests.get("https://my.rustix.me", proxies=proxies, timeout=10)
        print(f"✅ 连通性测试通过！Status: {r.status_code}")
        return True
    except Exception as e:
        print(f"❌ 连通失败 ({mode}): {e}")
        return False


def try_cffi_bypass(cookie_dict, xsrf_token, user_agent, use_proxy=True):
    """尝试 API 直连"""
    if not HAS_CFFI:
        return False

    mode = "代理模式" if use_proxy else "直连模式"
    print(f"\n⚡ 尝试 curl_cffi API 直连 ({mode})...")
    
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Origin": "https://my.rustix.me",
        "Referer": CONSOLE_URL,
        "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if xsrf_token:
        headers["X-XSRF-TOKEN"] = xsrf_token

    proxies = {"https": PROXY_URL, "http": PROXY_URL} if use_proxy else None

    try:
        session = cffi_requests.Session(impersonate="chrome124")
        
        for action in ["start", "restart"]:
            res = session.post(
                API_POWER_URL,
                json={"signal": action},
                headers=headers,
                cookies=cookie_dict,
                proxies=proxies,
                timeout=15,
                allow_redirects=False
            )
            if res.status_code == 204:
                print(f"🎉 API 响应真实 HTTP 204！已向服务器发送 [{action}] 指令！")
                notify(f"🚀 API 直连成功！已向服务器发送 [{action}] 开机指令！({mode})")
                return True
            else:
                print(f"ℹ️ API 响应 HTTP {res.status_code}，降级转入 Chrome 浏览器界面真实点击模式...")
                return False
    except Exception as exc:
        print(f"⚠️ API 尝试异常: {exc}")

    return False


async def click_in_frame(frame):
    for action, labels in BUTTONS:
        for label in labels:
            pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
            locators = [
                frame.get_by_role("button", name=pattern),
                frame.locator('button, [role="button"], a, [tabindex]').filter(has_text=pattern),
                frame.get_by_text(pattern, exact=True),
            ]
            for locator in locators:
                try:
                    count = min(await locator.count(), 20)
                    for i in range(count):
                        el = locator.nth(i)
                        if not await el.is_visible():
                            continue
                        if await el.get_attribute("disabled") is not None:
                            continue
                        if (await el.get_attribute("aria-disabled")) == "true":
                            continue
                        classes = (await el.get_attribute("class")) or ""
                        if "disabled" in classes.lower():
                            continue
                        await el.scroll_into_view_if_needed()
                        await el.click(force=True, timeout=5000)
                        return action, label, frame.url
                except Exception:
                    pass
    return None


async def run_playwright_official_chrome(playwright_cookies, user_agent, use_proxy=True):
    """启动官方 Chrome 进行图形界面操作并截图"""
    mode = "代理模式" if use_proxy else "直连模式"
    print(f"\n🌐 启动官方 Google Chrome 浏览器 ({mode})...")

    async with async_playwright() as p:
        launch_kwargs = {
            "channel": "chrome",
            "headless": False,
            "ignore_default_args": ["--enable-automation"],
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
            ],
        }
        if use_proxy:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(viewport={"width": 1366, "height": 768}, user_agent=user_agent, locale="ru-RU")

        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
        await context.add_cookies(playwright_cookies)
        page = await context.new_page()

        print(f"🌐 打开控制台页面: {CONSOLE_URL}")
        await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
        await page.mouse.move(50, 50)
        await page.wait_for_timeout(8000)

        body = await page.locator("body").inner_text(timeout=10000)
        if "access denied" in body.lower():
            await page.screenshot(path="00_access_denied.png", full_page=True)
            raise RuntimeError("Mitelis 防火墙拒绝访问，可能当前节点 IP 仍处于冷却期，请更换节点或重新导出 COOKIES_JSON！")

        await page.screenshot(path="01_before_click.png", full_page=True)
        print("📸 已生成点击前截图: 01_before_click.png")

        print("✅ 成功进入页面，寻找并点击 Старт / Рестарт 按钮...")
        for attempt in range(20):
            for frame in page.frames:
                res = await click_in_frame(frame)
                if res:
                    action, label, frame_url = res
                    print(f"🎉 成功点击按钮: {label} ({action})！")
                    print("⏳ 等待 10 秒以捕捉服务器最新的启动状态...")
                    await page.wait_for_timeout(10000)
                    
                    await page.screenshot(path="02_after_click_status.png", full_page=True)
                    print("📸 已生成启动后状态截图: 02_after_click_status.png")

                    notify(f"服务器控制指令已发：[{label}]。状态截图已存入 Actions Artifacts！")
                    await browser.close()
                    return
            await page.wait_for_timeout(3000)

        await page.screenshot(path="error_button_not_found.png", full_page=True)
        raise RuntimeError("在控制台未找到可用按钮")


async def main():
    if not COOKIES_JSON:
        raise RuntimeError("未配置 COOKIES_JSON")

    cookie_dict, xsrf_token, playwright_cookies = parse_cookie_dict(COOKIES_JSON)
    user_agent = extract_user_agent(cookie_dict)

    use_proxy = False
    if test_connectivity(use_proxy=True):
        use_proxy = True
    elif test_connectivity(use_proxy=False):
        print("⚠️ 代理节点 IP 被 Rustix 拒绝，自动切为【直连模式】！")
        use_proxy = False
    else:
        raise RuntimeError("代理通道与直连通道均无法连接 my.rustix.me")

    if try_cffi_bypass(cookie_dict, xsrf_token, user_agent, use_proxy=use_proxy):
        sys.exit(0)

    try:
        await run_playwright_official_chrome(playwright_cookies, user_agent, use_proxy=use_proxy)
    except Exception as exc:
        print(f"脚本运行出错: {exc}")
        notify(f"脚本运行失败\n\n{exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
