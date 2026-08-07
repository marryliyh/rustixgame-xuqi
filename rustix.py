import os
import sys
import time
import json
import asyncio
import subprocess

# 自动检查并补全必要依赖
for pkg in ["requests", "playwright"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

import requests
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


async def inject_stealth_scripts(page):
    """注入深度伪装脚本，抹去 Playwright / CDP 自动化特征，防止触发 Cloudflare TCP/TLS 断开"""
    stealth_js = """
    () => {
        // 抹除 navigator.webdriver 标志
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        
        # 伪造 Chrome 插件列表
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        
        // 伪造语言与硬件特征
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });
        
        // 抹除 window.chrome 特征识别
        window.chrome = { runtime: {} };
    }
    """
    await page.add_init_script(stealth_js)


async def same_origin_fetch(page, endpoint, method="GET", body_data=None):
    """在当前已通过 Cloudflare 盾的同源页面内部执行原生 fetch，彻底消除 CORS 跨域限制与 TLS 指纹断开问题"""
    js_code = """
    async ({ endpoint, method, apiKey, bodyData }) => {
        try {
            const options = {
                method: method,
                headers: {
                    'Authorization': 'Bearer ' + apiKey,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }
            };
            if (bodyData) {
                options.body = JSON.stringify(bodyData);
            }
            const res = await fetch(endpoint, options);
            const text = await res.text();
            return { status: res.status, text: text };
        } catch (err) {
            return { status: 0, text: err.toString() };
        }
    }
    """
    payload = {
        "endpoint": endpoint,
        "method": method,
        "apiKey": API_KEY,
        "bodyData": body_data,
    }
    result = await page.evaluate(js_code, payload)
    
    status = result.get("status", 0)
    raw_text = result.get("text", "").strip()
    
    json_data = None
    if raw_text:
        try:
            json_data = json.loads(raw_text)
        except Exception:
            pass
            
    return status, json_data, raw_text


async def async_main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 保活流程 (反自动化隐形引擎 + 同源栈穿透模式)...")

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
                "--disable-web-security",
            ],
            proxy=proxy_config,
        )

        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1366, "height": 768},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        page = await context.new_page()

        # 注入 Stealth 伪装脚本
        await inject_stealth_scripts(page)

        # 1. 访问主页解算 Cloudflare Challenge (支持自动重试机制)
        print("🌐 步骤 1: 访问 Rustix 控制台主页，解算 Cloudflare 防火墙...")
        loaded_success = False
        for attempt in range(1, 4):
            try:
                print(f"  └─ 第 {attempt}/3 次尝试连接主页...")
                res = await page.goto(BASE_URL, wait_until="commit", timeout=30000)
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                print(f"  └─ 页面建立成功，响应 HTTP 状态码: {res.status if res else '200'}")
                loaded_success = True
                break
            except Exception as e:
                print(f"  └─ 连接提示 (尝试 {attempt}): {e}")
                await asyncio.sleep(3)

        if not loaded_success:
            print("⚠️ 主页直连受阻，尝试直接注入上下文完成通信...")

        # 留出 6 秒供 Cloudflare 完成 JS / Turnstile 演算并下发 clearance
        print("⏳ 等待 Cloudflare 前端 JS 盾校验通过...")
        await asyncio.sleep(6)
        await page.screenshot(path="screenshots/cf_stealth_passed.png")

        # 2. 查询服务器初始状态 (同源 API 访问，不经过外部跨域)
        print("🔍 步骤 2: 在已校验的浏览器环境内请求 API 探测服务器状态...")
        status_endpoint = f"/api/client/servers/{SERVER_ID}/resources"
        code, json_data, raw_text = await same_origin_fetch(page, status_endpoint, "GET")

        init_status = "unknown"
        if json_data and isinstance(json_data, dict):
            init_status = json_data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ API 请求成功，解析服务器状态: [{init_status}]")
        else:
            print(f"  └─ 接口响应 HTTP {code}，内容片段: {raw_text[:120]}")

        # 若已经在运行，直接成功退出
        if init_status in ["running", "starting"]:
            print("🎉 服务器正在正常运行中，无需重启。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
            await browser.close()
            sys.exit(0)

        # 3. 发送开机指令
        print("⚡ 步骤 3: 下发 [start] 电源指令...")
        power_endpoint = f"/api/client/servers/{SERVER_ID}/power"
        p_code, p_json, p_raw = await same_origin_fetch(
            page, power_endpoint, "POST", {"signal": "start"}
        )
        print(f"  └─ [start] 指令响应 HTTP 状态码: {p_code}")
        power_success = p_code in [200, 204]

        # 4. 轮询确认开机状态
        print("⏳ 步骤 4: 轮询确认服务器状态更新...")
        final_status = "unknown"
        for i in range(1, 6):
            await asyncio.sleep(4)
            c_code, c_json, _ = await same_origin_fetch(page, status_endpoint, "GET")
            if c_json and isinstance(c_json, dict):
                curr = c_json.get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break
            else:
                print(f"  └─ 轮询第 {i}/5 次 HTTP {c_code}")

        await browser.close()

        # 5. 结果判定与 Telegram 通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif power_success:
            notify(f"🚀 Rustix 开机指令下发成功！\n\n- API 响应状态码: HTTP {p_code}\n- 开机信号已成功送到后台。")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 保活失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
