import asyncio
import json
import os
import re
import socket
import sys
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright

TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
COOKIES_JSON = os.environ.get("COOKIES_JSON")
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:10809")
CONSOLE_URL = os.environ.get("CONSOLE_URL", "https://my.rustix.me/server/226fd977/console")

# Chromium internal error pages. Seeing one of these means the web panel was never loaded.
NETWORK_ERROR_MARKERS = (
    "ERR_SSL_PROTOCOL_ERROR",
    "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_CONNECTION_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED",
    "This site can’t provide a secure connection",
    "This site can't provide a secure connection",
    "chrome-error://chromewebdata",
)


def send_tg_message(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过通知。")
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动启动/保活通知\n\n{text}"},
            timeout=15,
        )
        if response.status_code == 200:
            print("[TG] Telegram 通知发送成功。")
        else:
            print(f"[TG] 通知失败: HTTP {response.status_code}: {response.text[:300]}")
    except Exception as exc:
        print(f"[TG] 通知异常: {exc}")


def check_proxy_port(proxy_url):
    parsed = urlparse(proxy_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 10809
    try:
        with socket.create_connection((host, port), timeout=3):
            return True
    except OSError:
        return False


def format_cookies_for_playwright(raw_cookies):
    cleaned = []
    for item in raw_cookies:
        name = item.get("name")
        value = item.get("value")
        domain = item.get("domain")
        if not name or value is None or not domain:
            continue
        cookie = {
            "name": name,
            "value": str(value),
            "domain": domain,
            "path": item.get("path", "/"),
            "secure": bool(item.get("secure", True)),
            "httpOnly": bool(item.get("httpOnly", False)),
        }
        same_site = str(item.get("sameSite", "")).lower()
        if same_site in ("no_restriction", "none"):
            cookie["sameSite"] = "None"
        elif same_site == "lax":
            cookie["sameSite"] = "Lax"
        elif same_site == "strict":
            cookie["sameSite"] = "Strict"
        expiration = item.get("expirationDate", item.get("expires"))
        if expiration:
            try:
                cookie["expires"] = int(float(expiration))
            except (TypeError, ValueError):
                pass
        cleaned.append(cookie)
    return cleaned


async def page_error(page):
    """Return a concrete Chromium/network error, or None when a real page loaded."""
    try:
        title = await page.title()
    except Exception:
        title = ""
    try:
        body = await page.locator("body").inner_text(timeout=3000)
    except Exception:
        body = ""
    combined = f"{page.url}\n{title}\n{body}"
    for marker in NETWORK_ERROR_MARKERS:
        if marker.lower() in combined.lower():
            return marker
    return None


async def save_diagnostics(page, prefix):
    await page.screenshot(path=f"{prefix}.png", full_page=True)
    try:
        html = await page.content()
        with open(f"{prefix}.html", "w", encoding="utf-8") as file:
            file.write(html)
    except Exception:
        pass


async def run_automation():
    if not COOKIES_JSON:
        raise RuntimeError("未配置 COOKIES_JSON。请在 GitHub Actions Secrets 中添加最新 Cookie JSON。")
    try:
        parsed_cookies = json.loads(COOKIES_JSON)
        if isinstance(parsed_cookies, dict) and "cookies" in parsed_cookies:
            parsed_cookies = parsed_cookies["cookies"]
        if not isinstance(parsed_cookies, list):
            raise ValueError("Cookie JSON 顶层必须是数组，或包含 cookies 数组")
        cookies = format_cookies_for_playwright(parsed_cookies)
    except Exception as exc:
        raise RuntimeError(f"COOKIES_JSON 解析失败: {exc}") from exc
    if not cookies:
        raise RuntimeError("COOKIES_JSON 中没有可用 Cookie。")
    if not check_proxy_port(PROXY_URL):
        raise RuntimeError(f"本地代理未启动或端口不通: {PROXY_URL}")

    print("\n==========================================")
    print("开始访问 Rustix 服务器控制台")
    print("==========================================")
    print(f"代理: {PROXY_URL}")

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True,
            proxy={"server": PROXY_URL},
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-quic",
                "--window-size=1366,768",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="ru-RU",
        )
        await context.add_cookies(cookies)
        page = await context.new_page()

        last_error = None
        for attempt in range(1, 4):
            print(f"访问控制台，尝试 {attempt}/3: {CONSOLE_URL}")
            try:
                response = await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(5000)
                chromium_error = await page_error(page)
                if chromium_error:
                    last_error = chromium_error
                    print(f"网络/TLS 加载失败: {chromium_error}")
                elif response and response.status >= 400:
                    last_error = f"HTTP {response.status}"
                    print(f"页面返回错误: {last_error}")
                else:
                    last_error = None
                    break
            except Exception as exc:
                last_error = str(exc)
                print(f"页面访问异常: {last_error}")
            if attempt < 3:
                await page.wait_for_timeout(5000)

        if last_error:
            await save_diagnostics(page, "network_error")
            raise RuntimeError(
                f"代理能够监听端口，但无法正确建立到 my.rustix.me 的 HTTPS 连接: {last_error}。"
                "这不是按钮定位问题，请更换可用的 NODE_LINK 后重试。"
            )

        current_url = page.url.lower()
        body_text = await page.locator("body").inner_text(timeout=10000)
        if "access denied" in body_text.lower():
            await save_diagnostics(page, "access_denied")
            raise RuntimeError("页面返回 Access denied。请更换节点 IP，或更新 Cookie。")
        if "login" in current_url or "auth" in current_url:
            await save_diagnostics(page, "login_failed")
            raise RuntimeError("Cookie 已失效，页面被重定向到登录页。请重新导出 COOKIES_JSON。")

        print("页面网络、TLS 与登录状态检查通过。")
        await page.wait_for_timeout(5000)

        # Prefer semantic Playwright locators. Regex covers Russian and English labels.
        start_button = page.get_by_role("button", name=re.compile(r"^(Старт|Start)$", re.I))
        restart_button = page.get_by_role("button", name=re.compile(r"^(Рестарт|Restart)$", re.I))
        action = None
        if await start_button.count() > 0:
            await start_button.first.click()
            action = "Старт / Start"
        elif await restart_button.count() > 0:
            await restart_button.first.click()
            action = "Рестарт / Restart"
        else:
            # Fallback for controls that are links or role=button.
            locator = page.locator(
                'button, a, [role="button"]'
            ).filter(has_text=re.compile(r"^(Старт|Start|Рестарт|Restart)$", re.I))
            if await locator.count() > 0:
                text = (await locator.first.inner_text()).strip()
                await locator.first.click(force=True)
                action = text

        if not action:
            await save_diagnostics(page, "button_not_found")
            raise RuntimeError(
                "控制台已成功打开，但没有找到 Start/Restart 按钮。"
                "已保存 button_not_found.png 和 button_not_found.html。"
            )

        print(f"已点击控制按钮: {action}")
        await page.wait_for_timeout(6000)
        await page.screenshot(path="after_click.png", full_page=True)
        send_tg_message(f"服务器控制指令已发出: {action}")
        await browser.close()


async def main():
    try:
        await run_automation()
    except Exception as exc:
        message = str(exc)
        print(f"\n脚本运行出错: {message}")
        send_tg_message(f"脚本运行失败\n\n{message}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
