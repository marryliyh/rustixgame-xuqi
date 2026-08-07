import os
import sys
import time
import json
import asyncio
import subprocess

# 1. 动态依赖检查与安装
required_pkgs = ["requests", "websockets", "playwright", "curl_cffi"]
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
    """发送纯文字通知到 Telegram"""
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


def upload_to_website(file_path):
    """上传截图到公网图床网址，返回在线查看/下载链接"""
    if not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            r = requests.post(
                "https://catbox.moe/user/api.php",
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=20,
            )
        if r.status_code == 200 and r.text.startswith("http"):
            url = r.text.strip()
            print(f"🔗 截图已成功上传至网址: {url}")
            return url
    except Exception as e:
        print(f"⚠️ 截图上传网址失败: {e}")
    return None


def get_server_status(headers, proxies):
    """通过 API 查询服务器真实状态，并处理 Cloudflare 拦截"""
    try:
        r = cffi_requests.get(
            f"{BASE_URL}/resources",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if r.status_code == 200:
            try:
                res_data = r.json()
                state = res_data.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ API 响应成功，当前状态: [{state}]")
                return state, "OK"
            except Exception as e:
                print(f"  └─ JSON 解析失败 (可能返回了非 JSON 内容): {e}")
                return "unknown", "JSON_PARSE_ERROR"
        elif r.status_code in [403, 503]:
            print(f"  └─ ⚠️ 被 Cloudflare 拦截 (HTTP {r.status_code})")
            return "unknown", f"CLOUDFLARE_BLOCKED_{r.status_code}"
        else:
            print(f"  └─ API 返回异常状态码: {r.status_code}")
            return "unknown", f"HTTP_{r.status_code}"
    except Exception as e:
        print(f"  └─ 请求发生网络异常: {e}")
        return "unknown", f"NETWORK_ERROR"


async def trigger_via_websocket(headers, proxies):
    """通过 WebSocket 信道下发启动指令"""
    print("🔌 尝试通过 WebSocket 信道下发启动指令...")
    try:
        ws_info_res = cffi_requests.get(
            f"{BASE_URL}/websocket",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if ws_info_res.status_code != 200:
            print(f"  └─ 获取 WebSocket Token 失败，HTTP 状态码: {ws_info_res.status_code}")
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
            print("  └─ WebSocket 启动指令已成功下发！")
            return True
    except Exception as e:
        print(f"  └─ WebSocket 触发跳过: {e}")
        return False


async def capture_page_screenshot():
    """打开控制台网页进行物理渲染截图并上传"""
    print("📸 启动 Playwright 打开控制台网页截图...")
    proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None
    
    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
                proxy=proxy_config,
            )
            page = await browser.new_page(viewport={"width": 1366, "height": 768})
            await page.goto(CONSOLE_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(5000)

            shot_path = "screenshots/console_status.png"
            await page.screenshot(path=shot_path, full_page=True)
            await browser.close()

            img_url = upload_to_website(shot_path)
            return img_url
        except Exception as e:
            print(f"  └─ 网页截图捕捉提示: {e}")
            return None


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 状态检测与保活流程...")
    
    proxies = {"http": PROXY_URL_SOCKS5H, "https": PROXY_URL_SOCKS5H} if PROXY_URL_SOCKS5H else None
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }

    # 1. 查询初始状态
    init_status, err_reason = get_server_status(headers, proxies)
    print(f"📊 探测结果: 状态=[{init_status}], 诊断信息=[{err_reason}]")

    # 2. 如果正常运行中，直接完成并退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正常运行中，无需触发重启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 3. 如果因 Cloudflare 防火墙问题无法检测状态
    if "CLOUDFLARE_BLOCKED" in err_reason or "HTTP_403" in err_reason:
        print("⚠️ 警告: 当前代理节点节点被 Cloudflare 防火墙拦截，尝试通过 WebSocket/Power 指令唤醒...")

    # 4. 尝试通过 WebSocket 下发启动指令
    await trigger_via_websocket(headers, proxies)

    # 5. 截图并上传网址
    shot_url = await capture_page_screenshot()
    link_info = f"\n📸 最新截图链接: {shot_url}" if shot_url else "\n📸 截图请去 GitHub Actions Artifacts 查看"

    # 6. 再次轮询确认
    time.sleep(5)
    final_status, final_reason = get_server_status(headers, proxies)

    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 当前状态: {final_status.upper()}{link_info}")
        sys.exit(0)
    elif "CLOUDFLARE_BLOCKED" in final_reason:
        notify(f"⚠️ Rustix 保活提醒：请求被 Cloudflare 防火墙拦截 (HTTP 403)\n\n建议换个 proxy 节点或更新 NODE_LINK，目前无法直接通过 API 获取状态。{link_info}")
        sys.exit(1)
    else:
        notify(f"⚠️ Rustix 保活状态: [{final_status}]\n- 诊断信息: {final_reason}{link_info}")
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
