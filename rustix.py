import os
import sys
import time
import asyncio
import subprocess

# 自动检查并补全必要依赖
for pkg in ["requests", "playwright", "curl_cffi"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
from curl_cffi import requests as cffi_requests
from playwright.async_api import async_playwright

# 环境变量配置
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"

os.makedirs("screenshots", exist_ok=True)


def notify(text):
    """发送 Telegram 消息通知"""
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
    except Exception as e:
        print(f"[TG] 通知发送失败: {e}")


async def get_cf_session():
    """使用 Playwright 配合代理穿透 Cloudflare，安全提取 Cookie 且不崩溃"""
    print("🌐 步骤 0: 启动 Chromium 浏览器，解算 Cloudflare 防火墙...")
    
    proxy_server = PROXY_URL.replace("socks5h://", "socks5://") if PROXY_URL else None
    proxy_config = {"server": proxy_server} if proxy_server else None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()

        cookies_dict = {}
        try:
            print(f"⏳ 正在通过代理 ({proxy_server}) 访问 Rustix 控制台...")
            response = await page.goto(BASE_URL, wait_until="domcontentloaded", timeout=45000)
            
            # 等待 Cloudflare 前端 JS 演算完毕
            await page.wait_for_timeout(8000)
            
            # 保存运行过程截图
            await page.screenshot(path="screenshots/cf_pass_check.png")
            
            # 获取通过盾后的 Cookies
            cookies_list = await context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}
            print(f"  └─ 页面响应 HTTP: {response.status if response else 'UNKNOWN'}")
            print(f"  └─ 提取 Cookie 数量: {len(cookies_dict)} 项")

        except Exception as e:
            print(f"  └─ ⚠️ 页面加载阻断或超时: {e}")
            try:
                await page.screenshot(path="screenshots/cf_error.png")
            except Exception:
                pass
        finally:
            await browser.close()

        return cookies_dict


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (Playwright 代理穿透 + curl_cffi 组合防护模式)...")

    # 1. 穿透 Cloudflare 抓取凭证 Cookies
    cookies = asyncio.run(get_cf_session())

    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    } if PROXY_URL else None

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }

    # 创建带 TLS 指纹伪装与 Cookies 复用的 Session
    session = cffi_requests.Session()
    session.headers.update(headers)
    if cookies:
        session.cookies.update(cookies)

    # 2. 查询服务器初始状态
    print("🔍 步骤 1: 请求 API 探测服务器当前状态...")
    init_status = "unknown"
    try:
        res = session.get(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ API HTTP 状态码: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            init_status = data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 当前服务器状态: [{init_status}]")
        else:
            print(f"  └─ 响应片段: {res.text[:150]}")
    except Exception as e:
        print(f"  └─ API 请求异常: {e}")

    # 若正在运行则直接成功退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正在正常运行中，无需重复开启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 3. 发送开机指令
    print("⚡ 步骤 2: 下发 [start] 电源指令...")
    power_success = False
    try:
        p_res = session.post(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/power",
            json={"signal": "start"},
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ 电源指令 HTTP 状态码: {p_res.status_code}")
        if p_res.status_code in [200, 204]:
            power_success = True
    except Exception as e:
        print(f"  └─ 电源指令发送异常: {e}")

    # 4. 轮询确认开机状态
    print("⏳ 步骤 3: 轮询确认服务器状态变更...")
    final_status = "unknown"
    for i in range(1, 6):
        time.sleep(4)
        try:
            check_res = session.get(
                f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
                proxies=proxies,
                impersonate="chrome120",
                timeout=20,
            )
            if check_res.status_code == 200:
                curr = check_res.json().get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break
            else:
                print(f"  └─ 轮询第 {i}/5 次响应 HTTP {check_res.status_code}")
        except Exception as e:
            print(f"  └─ 轮询网络异常: {e}")

    # 5. 结果判定与 Telegram 通知
    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
        sys.exit(0)
    elif power_success:
        notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应: HTTP 200/204\n- 开机指令已成功送达服务端后台。")
        sys.exit(0)
    else:
        notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
        sys.exit(1)


if __name__ == "__main__":
    main()
