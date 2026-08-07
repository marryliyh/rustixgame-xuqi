import os
import sys
import time
import json
import asyncio
import subprocess

# 1. 动态依赖检查与安装
required_pkgs = ["requests", "playwright"]
for pkg in required_pkgs:
    try:
        if pkg == "playwright":
            from playwright.async_api import async_playwright
        else:
            import requests
    except ImportError:
        print(f"📦 自动安装 Python 依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
from playwright.async_api import async_playwright

# 2. 环境变量配置
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"
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
    """上传截图到 catbox.moe 图床，返回在线查看链接"""
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


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 状态检测与保活流程 (Chromium 原生网络栈)...")
    
    proxy_config = {"server": PROXY_URL.replace("socks5h://", "socks5://")} if PROXY_URL else None

    async with async_playwright() as p:
        # 使用 Chromium 原生 API 上下文（具备完整的 Chrome 协议栈，避开 TLS 拦截）
        api = await p.request.new_context(
            base_url=BASE_URL,
            extra_http_headers={
                "Authorization": f"Bearer {API_KEY}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            },
            proxy=proxy_config,
        )

        # 1. 探测初始状态
        print("🔍 步骤 1: 请求 API 探测服务器当前状态...")
        init_status = "unknown"
        cf_blocked = False

        try:
            res = await api.get(f"/api/client/servers/{SERVER_ID}/resources")
            if res.status == 200:
                data = await res.json()
                init_status = data.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ API 响应成功，当前状态: [{init_status}]")
            elif res.status in [403, 503]:
                print(f"  └─ ⚠️ 被 Cloudflare 拦截 (HTTP {res.status})")
                cf_blocked = True
            else:
                print(f"  └─ API 状态码异常: {res.status}")
        except Exception as e:
            print(f"  └─ 请求发生网络异常: {e}")

        # 2. 如果检测到服务器已经在运行，直接成功退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正常运行中，无需重启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            sys.exit(0)

        # 3. 若未在运行，发送 Start 电源指令拉起
        print("⚡ 步骤 2: 发送 [start] 开机指令...")
        try:
            p_res = await api.post(f"/api/client/servers/{SERVER_ID}/power", data=json.dumps({"signal": "start"}))
            print(f"  └─ [start] 指令响应状态码: {p_res.status}")
        except Exception as e:
            print(f"  └─ [start] 指令下发失败: {e}")

        # 4. 尝试通过网页渲染截图
        print("📸 步骤 3: 打开控制台网页渲染截图...")
        shot_url = None
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

            shot_url = upload_to_website(shot_path)
        except Exception as e:
            print(f"  └─ 截图渲染异常: {e}")

        link_info = f"\n📸 最新截图链接: {shot_url}" if shot_url else "\n📸 截图文件已打包存入 GitHub Artifacts"

        # 5. 轮询确认最终状态
        print("⏳ 步骤 4: 轮询确认服务器最新状态...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            try:
                check_res = await api.get(f"/api/client/servers/{SERVER_ID}/resources")
                if check_res.status == 200:
                    curr_data = await check_res.json()
                    curr = curr_data.get("attributes", {}).get("current_state", "unknown")
                    print(f"  └─ 轮询第 {i}/5 次: [{curr}]")
                    if curr in ["running", "starting"]:
                        final_status = curr
                        break
            except Exception:
                pass

        # 6. 处理结果并发送 TG 通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 当前状态: {final_status.upper()}{link_info}")
            sys.exit(0)
        elif cf_blocked:
            notify(f"⚠️ Rustix 保活提醒：代理节点 IP 被 Cloudflare 拦截 (Access Denied)\n\n请更换 GitHub Secrets 中的 NODE_LINK 订阅节点后再试！{link_info}")
            sys.exit(1)
        else:
            notify(f"⚠️ Rustix 保活状态: [{final_status}]\n- 初始状态: [{init_status}]{link_info}")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
