import os
import sys
import time
import json
import asyncio
from curl_cffi import requests as cffi_requests
import requests
import websockets
from playwright.async_api import async_playwright

# 1. 环境变量配置
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
    """通过 API 查询服务器真实状态"""
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
            except Exception:
                print("  └─ API 返回了非 JSON 结构 (疑似 Cloudflare 人机验证)")
                return "unknown", "CLOUDFLARE_CHALLENGE"
        else:
            print(f"  └─ API 状态码异常: {r.status_code}")
            return "unknown", f"HTTP_{r.status_code}"
    except Exception as e:
        print(f"  └─ 请求发生网络异常: {e}")
        return "unknown", "NETWORK_ERROR"


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
    """使用 Playwright 打开控制台网页进行渲染与截图"""
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
            print(f"📸 本地截图已保存至: {shot_path}")

            img_url = upload_to_website(shot_path)
            return img_url
        except Exception as e:
            print(f"  └─ 网页截图捕捉异常: {e}")
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

    # 1. 探测初始状态
    init_status, err_reason = get_server_status(headers, proxies)
    print(f"📊 探测结果: 状态=[{init_status}], 诊断信息=[{err_reason}]")

    # 2. 如果正常运行，直接退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正常运行中，无需触发重启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 3. 尝试下发 WebSocket 命令拉起
    await trigger_via_websocket(headers, proxies)

    # 4. 强制执行 Playwright 截图
    shot_url = await capture_page_screenshot()
    link_info = f"\n📸 最新截图链接: {shot_url}" if shot_url else "\n📸 截图文件已打包存入 GitHub Artifacts"

    # 5. 再次轮询确认状态
    time.sleep(5)
    final_status, final_reason = get_server_status(headers, proxies)

    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 当前状态: {final_status.upper()}{link_info}")
        sys.exit(0)
    else:
        notify(f"⚠️ Rustix 保活状态: [{final_status}]\n- 诊断信息: {final_reason}{link_info}")
        sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
