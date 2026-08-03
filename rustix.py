import asyncio
import json
import os
import re
import socket
import sys
from urllib.parse import urlparse

import requests
from playwright.async_api import async_playwright

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
COOKIES_JSON = os.getenv("COOKIES_JSON", "")
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")
CONSOLE_URL = os.getenv("CONSOLE_URL", "https://my.rustix.me/server/226fd977/console")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

NETWORK_MARKERS = (
    "ERR_SSL_PROTOCOL_ERROR", "ERR_PROXY_CONNECTION_FAILED",
    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_TIMED_OUT",
    "ERR_NAME_NOT_RESOLVED", "chrome-error://chromewebdata",
)
BUTTONS = (
    ("restart", ("Рестарт", "Restart", "Reboot", "重启")),
    ("start", ("Старт", "Start", "启动", "开机")),
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


def proxy_port_ok(url):
    parsed = urlparse(url)
    try:
        with socket.create_connection((parsed.hostname or "127.0.0.1", parsed.port or 10808), timeout=3):
            return True
    except OSError:
        return False


def normalize_cookies(value):
    raw = json.loads(value)
    if isinstance(raw, dict):
        raw = raw.get("cookies", [])
    if not isinstance(raw, list):
        raise ValueError("Cookie JSON 顶层必须是数组，或包含 cookies 数组")
    result = []
    for c in raw:
        if not c.get("name") or c.get("value") is None:
            continue
        item = {
            "name": c["name"],
            "value": str(c["value"]),
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
        result.append(item)
    if not result:
        raise ValueError("没有可用 Cookie")
    return result


async def save_diagnostics(page, prefix):
    await page.screenshot(path=f"{prefix}.png", full_page=True)
    try:
        with open(f"{prefix}.html", "w", encoding="utf-8") as f:
            f.write(await page.content())
        with open(f"{prefix}_text.txt", "w", encoding="utf-8") as f:
            f.write(await page.locator("body").inner_text(timeout=5000))
        frame_lines = [f"{i}: name={fr.name!r} url={fr.url}" for i, fr in enumerate(page.frames)]
        with open(f"{prefix}_frames.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(frame_lines))
    except Exception as exc:
        print(f"保存部分诊断文件失败: {exc}")


async def inject_stealth(context):
    """注入高级伪装脚本，避开 Mitelis/Cloudflare 防火墙指纹检测"""
    stealth_js = """
    // 1. 抹除 navigator.webdriver 特征
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. 伪造 window.chrome 运行库
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 3. 伪造 Permissions API
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
    );

    // 4. 伪造 Plugins 与 MimeTypes
    Object.defineProperty(navigator, 'plugins', {
        get: () => [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbheakdddfldooefmcfdefgakmcbq', description: '' },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
        ],
    });

    // 5. 伪造 WebGL 硬件指纹 (NVIDIA 显卡)
    try {
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Google Inc. (NVIDIA)';
            if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            return getParameter.apply(this, [parameter]);
        };
    } catch (e) {}
    """
    await context.add_init_script(stealth_js)


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

    try:
        result = await frame.evaluate("""
        (groups) => {
          const norm = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
          const collect = root => {
            const out = [];
            const walk = node => {
              if (!node) return;
              if (node.nodeType === 1) {
                out.push(node);
                if (node.shadowRoot) walk(node.shadowRoot);
              }
              for (const child of (node.children || [])) walk(child);
            };
            walk(root);
            return out;
          };
          const all = collect(document.documentElement);
          for (const group of groups) {
            for (const label of group.labels) {
              const wanted = norm(label);
              const matches = all.filter(el => norm(el.innerText || el.textContent) === wanted);
              matches.sort((a,b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length);
              for (const el of matches) {
                const target = el.closest('button,[role="button"],a,[tabindex]') || el;
                const st = getComputedStyle(target);
                const rect = target.getBoundingClientRect();
                if (st.display === 'none' || st.visibility === 'hidden' || rect.width < 2 || rect.height < 2) continue;
                if (target.disabled || target.getAttribute('aria-disabled') === 'true') continue;
                target.scrollIntoView({block:'center'});
                target.click();
                return {action: group.action, label};
              }
            }
          }
          return null;
        }
        """, [{"action": a, "labels": list(labels)} for a, labels in BUTTONS])
        if result:
            return result["action"], result["label"], frame.url
    except Exception:
        pass
    return None


async def find_and_click(page):
    for attempt in range(20):
        for frame in page.frames:
            result = await click_in_frame(frame)
            if result:
                return result
        print(f"等待控制按钮渲染: {attempt + 1}/20")
        await page.wait_for_timeout(3000)
    return None


async def run():
    if not COOKIES_JSON:
        raise RuntimeError("未配置 COOKIES_JSON")
    if not proxy_port_ok(PROXY_URL):
        raise RuntimeError(f"代理端口未监听: {PROXY_URL}")
    cookies = normalize_cookies(COOKIES_JSON)

    print("==========================================")
    print("开始访问 Rustix 控制台 (Mitelis 防火墙伪装模式)")
    print(f"代理: {PROXY_URL}")
    print("==========================================")

    async with async_playwright() as p:
        # 💡 关键点 1: headless=False (配合 Xvfb 实现真实界面运行，规避无头检测)
        # 💡 关键点 2: ignore_default_args 屏蔽自动化标识
        browser = await p.chromium.launch(
            headless=False,
            ignore_default_args=["--enable-automation"],
            proxy={"server": PROXY_URL},
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-quic",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768",
            ],
        )

        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=USER_AGENT,
            locale="ru-RU",
            extra_http_headers={
                "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            }
        )

        # 💡 关键点 3: 注入防护层伪装逻辑
        await inject_stealth(context)
        await context.add_cookies(cookies)
        page = await context.new_page()

        response = await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=60000)
        
        # 模拟真人鼠标微动，协助通过隐式 JavaScript 验证
        await page.mouse.move(100, 100)
        await page.wait_for_timeout(8000)

        body = await page.locator("body").inner_text(timeout=10000)
        combined = f"{page.url}\n{body}"

        for marker in NETWORK_MARKERS:
            if marker.lower() in combined.lower():
                await save_diagnostics(page, "network_error")
                raise RuntimeError(f"页面网络错误: {marker}")

        if response and response.status >= 400:
            await save_diagnostics(page, "http_error")
            raise RuntimeError(f"Rustix 返回 HTTP {response.status}")

        if "access denied" in body.lower():
            # 尝试等待 10 秒看 Mitelis 的 JavaScript 挑战是否自动通过
            print("⚠️ 检测到 Access denied，等待 Mitelis 自动验证跳转...")
            await page.wait_for_timeout(10000)
            body = await page.locator("body").inner_text(timeout=5000)
            if "access denied" in body.lower():
                await save_diagnostics(page, "access_denied")
                raise RuntimeError("Rustix/Mitelis 返回 Access denied (伪装未能绕过防护)")

        if "login" in page.url.lower() or "auth" in page.url.lower():
            await save_diagnostics(page, "login_failed")
            raise RuntimeError("Cookie 已失效，已跳转登录页")

        print("✅ 防火墙通过，进入面板，开始匹配按钮...")
        result = await find_and_click(page)
        if not result:
            await save_diagnostics(page, "button_not_found")
            raise RuntimeError("控制台已打开，但 60 秒内在主页面、iframe 和 Shadow DOM 中均未找到可用的 Рестарт/Старт 按钮")

        action, label, frame_url = result
        print(f"成功点击: {label}；frame={frame_url}")
        await page.wait_for_timeout(8000)
        await page.screenshot(path="after_click.png", full_page=True)
        notify(f"服务器控制指令已发出：{label}（{action}）")
        await browser.close()


async def main():
    try:
        await run()
    except Exception as exc:
        print(f"脚本运行出错: {exc}")
        notify(f"脚本运行失败\n\n{exc}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
