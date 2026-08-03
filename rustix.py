import asyncio
import os
import sys

from playwright.async_api import async_playwright
import requests

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")

SERVER_ID = "226fd977"
MAIN_URL = "https://my.rustix.me"
API_POWER_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/power"
API_STATUS_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/resources"


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": f"Rustix 自动保活通知\n\n{text}"},
            timeout=15,
        )
        print("[TG] 通知发送成功" if r.ok else f"[TG] 通知失败: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[TG] 通知异常: {exc}")


async def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🌐 启动 Google Chrome 浏览器并加载防护屏障...")

    async with async_playwright() as p:
        launch_kwargs = {
            "channel": "chrome",
            "headless": False,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--ignore-certificate-errors",
                "--ignore-certificate-errors-spki-list",
                "--window-size=1366,768",
            ],
        }
        if PROXY_URL:
            launch_kwargs["proxy"] = {"server": PROXY_URL}

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        # 1. 打开主页过盾（无需访问具体控制台页面）
        print(f"⏳ 正在打开 Rustix 主页以通过 Mitelis JS 防火墙: {MAIN_URL}")
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await page.goto(MAIN_URL, wait_until="domcontentloaded", timeout=60000)
                print("✅ 成功连通 Rustix 主页！")
                break
            except Exception as e:
                print(f"⚠️ 第 {attempt + 1} 次打开主页异常: {e}")
                if attempt == max_retries - 1:
                    raise
                await page.wait_for_timeout(5000)

        # 留出 8 秒给 Mitelis 盾写入 Cookie
        print("⏳ 等待 8 秒让 Mitelis 完成 JS 验证并注入 Cookie...")
        await page.wait_for_timeout(8000)

        # 2. 在过盾后的 Chrome 浏览器内部，调用 API 查询实时状态
        print("🔍 正在通过 Chrome 同源 API 查询服务器当前运行状态...")
        status_res = await page.evaluate(
            """async ({ url, key }) => {
                try {
                    const res = await fetch(url, {
                        headers: {
                            'Authorization': 'Bearer ' + key,
                            'Accept': 'application/json'
                        }
                    });
                    if (res.ok) {
                        const data = await res.json();
                        return data.attributes?.current_state || 'unknown';
                    }
                    return 'http_' + res.status;
                } catch(e) {
                    return 'err:' + e.message;
                }
            }""",
            {"url": API_STATUS_URL, "key": API_KEY},
        )
        print(f"📊 服务器当前实时状态: [{status_res}]")

        target_signal = "restart" if status_res == "running" else "start"

        # 3. 在 Chrome 内部直接发送 API 电源指令
        print(f"⚡ 正在通过 API 发送电源指令 [{target_signal}]...")
        api_status = await page.evaluate(
            """async ({ url, key, signal }) => {
                try {
                    const res = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'Authorization': 'Bearer ' + key,
                            'Content-Type': 'application/json',
                            'Accept': 'application/json'
                        },
                        body: JSON.stringify({ signal: signal })
                    });
                    return res.status;
                } catch(e) {
                    return 'err:' + e.message;
                }
            }""",
            {"url": API_POWER_URL, "key": API_KEY, "signal": target_signal},
        )

        print(f"📄 API 接口响应状态码: {api_status}")

        if api_status in [200, 204]:
            print(f"🎉 成功发送 [{target_signal}] 指令！")
            notify(
                f"🚀 Rustix 保活成功！\n\n- 操作类型: {target_signal.upper()}\n- 初始状态: {status_res}\n- API 状态码: {api_status}"
            )
            await browser.close()
            sys.exit(0)
        else:
            print(f"❌ API 发送失败，状态码: {api_status}")
            notify(f"❌ Rustix 保活失败：API 响应状态码 HTTP {api_status}，请检查 API_KEY 配置。")
            await browser.close()
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
