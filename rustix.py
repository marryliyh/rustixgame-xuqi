import os
import sys
import time
import json
import asyncio
import subprocess

# 1. 动态依赖检查与安装
required_pkgs = ["curl_cffi", "requests", "websockets", "playwright"]
for pkg in required_pkgs:
    try:
        if pkg == "curl_cffi":
            from curl_cffi import requests as cffi_requests
        elif pkg == "websockets":
            import websockets
        elif pkg == "playwright":
            from playwright.async_api import async_playwright
        else:
            import requests
    except ImportError:
        print(f"📦 自动安装 Python 依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

from curl_cffi import requests as cffi_requests
import requests
import websockets
from playwright.async_api import async_playwright

# 确保 Playwright Chromium 浏览器内核可用
print("🌐 检查并补全 Chromium 浏览器内核...")
try:
    subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
except Exception as e:
    print(f"⚠️ 补全内核提示: {e}")

# 2. 环境变量配置
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

if PROXY_URL.startswith("socks5://"):
    PROXY_URL_SOCKS5H = PROXY_URL.replace("socks5://", "socks5h://")
elif PROXY_URL and not PROXY_URL.startswith("socks5h://") and not PROXY_URL.startswith("http"):
    PROXY_URL_SOCKS5H = f"socks5h://{PROXY_URL}"
else:
    PROXY_URL_SOCKS5H = PROXY_URL

SERVER_ID = "226fd977"
BASE_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}"
CONSOLE_URL = f"https://my.rustix.me/server/{SERVER_ID}/console"

os.makedirs("screenshots", exist_ok=True)


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
    except Exception as exc:
        print(f"[TG] 文字通知异常: {exc}")


def send_tg_photo(photo_path, caption=""):
    """发送截图到 Telegram"""
    if not TG_TOKEN or not TG_CHAT_ID or not os.path.exists(photo_path):
        return
    try:
        with open(photo_path, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TG_TOKEN}/sendPhoto",
                data={"chat_id": TG_CHAT_ID, "caption": f"📸 {caption}"},
                files={"photo": f},
                timeout=30,
            )
        print(f"📸 截图已成功发送至 Telegram: {photo_path}")
    except Exception as e:
        print(f"[TG] 发送截图异常: {e}")


def get_server_status(headers, proxies):
    try:
        r = cffi_requests.get(
            f"{BASE_URL}/resources",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=12,
        )
        if r.status_code == 200 and r.text and r.text.strip():
            return r.json().get("attributes", {}).get("current_state", "offline")
    except Exception:
        pass
    return "offline"


async def trigger_via_websocket(headers, proxies):
    print("🔌 方案 1: 尝试通过 WebSocket 信道发送 [set state -> start]...")
    try:
        ws_info_res = cffi_requests.get(
            f"{BASE_URL}/websocket",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if ws_info_res.status_code != 200 or not ws_info_res.text.strip():
            print(f"  └─ 获取 WS Token 失败，HTTP {ws_info_res.status_code}")
            return False

        ws_data = ws_info_res.json().get("data", {})
        token, socket_url = ws_data.get("token"), ws_data.get("socket")

        if not token or not socket_url:
            return False

        async with websockets.connect(socket_url, origin="https://my.rustix.me", open_timeout=15) as ws:
            await ws.send(json.dumps({"event": "auth", "args": [token]}))
            await asyncio.sleep(1)
            await ws.send(json.dumps({"event": "set state", "args": ["start"]}))
            await asyncio.sleep(2)
            print("  └─ WebSocket 启动指令下发成功！")
            return True
    except Exception as e:
        print(f"  └─ WebSocket 触发跳过: {e}")
        return False


async def trigger_via_playwright():
    print("🌐 方案 2: 启动 Playwright 打开控制台网页进行物理点击与截图...")
    async with async_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]

        proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None

        browser = await p.chromium.launch(headless=True, args=launch_args, proxy=proxy_config)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            print(f"⏳ 打开控制台: {CONSOLE_URL}")
            await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(6000)

            shot1 = "screenshots/01_before_click.png"
            await page.screenshot(path=shot1, full_page=True)
            send_tg_photo(shot1, "点击前网页控制台画面")

            clicked = False
            for selector in ["button:has-text('Старт')", "button:has-text('Start')", "button:has-text('启动')"]:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    print("🎉 成功锁定网页 [Старт] 按钮，执行点击...")
                    await btn.click(force=True)
                    clicked = True
                    break

            if not clicked:
                await page.evaluate(
                    """async () => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const startBtn = btns.find(b => b.innerText.includes('Старт') || b.innerText.includes('Start'));
                        if (startBtn) startBtn.click();
                    }"""
                )

            await page.wait_for_timeout(6000)

            shot2 = "screenshots/02_after_click.png"
            await page.screenshot(path=shot2, full_page=True)
            send_tg_photo(shot2, "点击后网页控制台最新画面")

            await browser.close()
            return True
        except Exception as e:
            print(f"  └─ Playwright 点击过程异常: {e}")
            err_shot = "screenshots/error_page.png"
            try:
                await page.screenshot(path=err_shot)
                send_tg_photo(err_shot, "异常时的网页画面")
            except Exception:
                pass
            await browser.close()
            return False


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活脚本 (支持 WebSocket + 网页仿真点击 + 全程截图)...")
    proxies = {"http": PROXY_URL_SOCKS5H, "https": PROXY_URL_SOCKS5H} if PROXY_URL_SOCKS5H else None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }

    init_status = get_server_status(headers, proxies)
    print(f"📊 初始状态: [{init_status}]")

    if init_status in ["running", "starting"]:
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    await trigger_via_websocket(headers, proxies)
    time.sleep(4)

    current_status = get_server_status(headers, proxies)
    if current_status not in ["running", "starting"]:
        await trigger_via_playwright()

    final_status = "offline"
    for i in range(1, 6):
        time.sleep(4)
        curr = get_server_status(headers, proxies)
        print(f"  └─ 轮询第 {i}/5 次: [{curr}]")
        if curr in ["running", "starting"]:
            final_status = curr
            break

    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 状态变更为: [{final_status.upper()}]")
        sys.exit(0)
    else:
        notify(f"⚠️ Rustix 保活异常！\n\n- 最终状态仍为: [{final_status}]")
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
