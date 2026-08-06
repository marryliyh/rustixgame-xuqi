import os
import sys
import time
import json
import asyncio
import subprocess

# 1. 动态依赖检查与自动安装
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
        print(f"📦 自动安装缺失依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

from curl_cffi import requests as cffi_requests
import requests
import websockets
from playwright.async_api import async_playwright

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


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
        print("[TG] 通知发送成功" if r.ok else f"[TG] 通知失败: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[TG] 通知发送异常: {exc}")


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
            data = r.json()
            return data.get("attributes", {}).get("current_state", "offline")
    except Exception:
        pass
    return "offline"


async def trigger_via_websocket(headers, proxies):
    """模拟网页前端：获取 WS Token 并发送 set state 启动指令"""
    print("🔌 方案 1: 尝试通过翼龙面板 WebSocket 信道发送 [set state -> start]...")
    try:
        ws_info_res = cffi_requests.get(
            f"{BASE_URL}/websocket",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if ws_info_res.status_code != 200:
            print(f"  └─ 获取 WS Token 失败，HTTP {ws_info_res.status_code}")
            return False

        ws_data = ws_info_res.json().get("data", {})
        token = ws_data.get("token")
        socket_url = ws_data.get("socket")

        if not token or not socket_url:
            print("  └─ WS 返回参数不完整，跳过 WebSocket 触发")
            return False

        print(f"  └─ 成功获取 WebSocket 地址: {socket_url[:45]}...")

        # 通过 WebSocket 握手认证并发送启动指令
        async with websockets.connect(
            socket_url,
            origin="https://my.rustix.me",
            open_timeout=15,
        ) as ws:
            # 1. 认证
            await ws.send(json.dumps({"event": "auth", "args": [token]}))
            await asyncio.sleep(1)

            # 2. 发送启动信号
            print("  └─ 已发送 WebSocket [auth] 凭证，正在发送 [set state: start] 指令...")
            await ws.send(json.dumps({"event": "set state", "args": ["start"]}))
            await asyncio.sleep(2)
            print("  └─ WebSocket 启动指令下发成功！")
            return True
    except Exception as e:
        print(f"  └─ WebSocket 触发异常: {e}")
        return False


async def trigger_via_playwright():
    """回退方案：Playwright 启动真实浏览器点击按钮"""
    print("🌐 方案 2: WebSocket 未能拉起，启动 Playwright 拟人化点击网页 [Старт] 按钮...")
    async with async_playwright() as p:
        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
            "--ignore-certificate-errors",
        ]

        proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None

        browser = await p.chromium.launch(
            headless=True,
            args=launch_args,
            proxy=proxy_config,
        )

        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            print(f"⏳ 正在打开控制台页面: {CONSOLE_URL}")
            await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(6000)

            # 查找并点击 [Старт / Start] 按钮
            clicked = False
            for selector in ["button:has-text('Старт')", "button:has-text('Start')", "button:has-text('启动')"]:
                btn = page.locator(selector).first
                if await btn.is_visible():
                    print(f"🎉 成功锁定网页 [Старт] 按钮，执行物理点击...")
                    await btn.click(force=True)
                    clicked = True
                    break

            if not clicked:
                print("⚠️ 网页未显式捕获到按钮，尝试在网页上下文内部直接触发点击...")
                await page.evaluate(
                    """async () => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const startBtn = btns.find(b => b.innerText.includes('Старт') || b.innerText.includes('Start'));
                        if (startBtn) startBtn.click();
                    }"""
                )

            await page.wait_for_timeout(5000)
            await browser.close()
            return True
        except Exception as e:
            print(f"  └─ Playwright 点击过程异常: {e}")
            await browser.close()
            return False


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 终极保活脚本 (WebSocket + 浏览器模拟双重保障)...")

    proxies = {"http": PROXY_URL_SOCKS5H, "https": PROXY_URL_SOCKS5H} if PROXY_URL_SOCKS5H else None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }

    # 1. 检测初始状态
    print("🔍 步骤 1: 检测服务器初始状态...")
    init_status = get_server_status(headers, proxies)
    print(f"📊 当前初始状态: [{init_status}]")

    if init_status in ["running", "starting"]:
        print(f"🎉 服务器目前处于 [{init_status}] 状态，无需重复启动！")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}\n- 结论: 已处于活跃状态")
        sys.exit(0)

    # 2. 方案 1：优先采用 WebSocket 发送真实启动信号
    await trigger_via_websocket(headers, proxies)

    time.sleep(4)
    current_status = get_server_status(headers, proxies)
    print(f"📊 WebSocket 触发后检测状态: [{current_status}]")

    # 3. 方案 2：如果 WebSocket 依然卡 offline，自动切换 Playwright 浏览器模拟
    if current_status not in ["running", "starting"]:
        print("⚠️ WebSocket 指令下发后状态未变更，自动唤醒 Playwright 回退机制...")
        await trigger_via_playwright()

    # 4. 最终状态轮询确认 (每 4 秒轮询一次，共 5 次)
    print("\n⏳ 步骤 3: 验证服务器是否真实进入启动状态...")
    final_status = "offline"
    for i in range(1, 6):
        time.sleep(4)
        curr = get_server_status(headers, proxies)
        print(f"  └─ 轮询第 {i}/5 次: [{curr}]")
        if curr in ["running", "starting"]:
            final_status = curr
            break

    # 5. Telegram 结果通知
    if final_status in ["running", "starting"]:
        print(f"🎉 成功！服务器已真正拉起，最终状态: [{final_status}]")
        notify(f"🚀 Rustix 保活成功！\n\n- 初始状态: {init_status.upper()}\n- 最终状态: {final_status.upper()}\n- 结论: 服务器已成功变为 [{final_status.upper()}] 状态！")
        sys.exit(0)
    else:
        print(f"❌ 最终校验状态仍为 [{final_status}]")
        notify(f"⚠️ Rustix 保活失败告警！\n\n- 初始状态: {init_status}\n- 最终状态: {final_status}\n- 建议: 请登录面板手动查看。")
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
