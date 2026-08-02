import asyncio
import requests
import os
import json
import sys
from playwright.async_api import async_playwright

# --- 从环境变量读取敏感信息 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
ACCOUNTS_JSON = os.environ.get("ACCOUNTS_JSON")

LOGIN_URL = "https://my.rustix.me/auth/login"

def send_tg_message(text):
    """发送带 Markdown 格式的 Telegram 消息"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("💡 [TG] TG_TOKEN 或 TG_CHAT_ID 未配置，跳过 Telegram 通知。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    formatted_text = f"*✅ rustix.me 服务器自动启动/重启通知*\n\n{text}"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ [TG] 发送 Telegram 消息失败: {e}")

async def process_account(account):
    """处理单个账户的逻辑"""
    user_email = account.get("user")
    user_pwd = account.get("pwd")
    
    print(f"\n==========================================")
    print(f"🚀 开始处理账户: {user_email}")
    print(f"==========================================")

    async with async_playwright() as p:
        # 核心改动：ignore_default_args 去掉 chromium 自带的 --enable-automation
        browser = await p.chromium.launch(
            headless=False,
            ignore_default_args=["--enable-automation"],
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--window-size=1366,768"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            locale="ru-RU"
        )

        # 原生注入全套 Anti-Detection 指纹伪装
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['ru-RU', 'ru', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        # 1. 打开登录页面
        print(f"🌐 1/4 打开登录页面: {LOGIN_URL}")
        try:
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
        except Exception:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        print("⏳ 等待页面与 Cloudflare 验证加载完成...")
        
        # 显式等待登录输入框出现（最多等待 35 秒）
        try:
            await page.wait_for_selector(
                'input[type="text"], input[type="email"], input[name="username"], input[name="email"], input[type="password"]',
                timeout=35000
            )
            print("✅ 已成功找到登录组件，突破 Cloudflare 盾！")
        except Exception:
            print("❌ 未能成功找到登录框，正在保存页面截图 cloudflare_block.png ...")
            await page.screenshot(path="cloudflare_block.png")
            raise Exception("登录输入框寻找超时，可能依然卡在 Cloudflare 人机验证页面。")

        # 智能搜寻输入框
        email_input = await page.query_selector('input[type="text"], input[type="email"], input[name="username"], input[name="email"], form input:nth-of-type(1)')
        pwd_input = await page.query_selector('input[type="password"]')

        if not email_input or not pwd_input:
            inputs = await page.query_selector_all('input')
            if len(inputs) >= 2:
                email_input = inputs[0]
                pwd_input = inputs[1]

        if not email_input or not pwd_input:
            await page.screenshot(path="cloudflare_block.png")
            raise Exception("登录输入框寻找超时或未匹配到输入组件。")

        print("🔑 正在输入账号密码登录...")
        # 模拟真实打字输入
        await email_input.fill("")
        await email_input.type(user_email, delay=50)
        await pwd_input.fill("")
        await pwd_input.type(user_pwd, delay=50)
        await asyncio.sleep(1)

        # 点击登录按钮
        login_btn = await page.query_selector('button[type="submit"], button:has-text("Войти"), button:has-text("Login"), button:has-text("Вход"), form button')
        if login_btn:
            await login_btn.click()
        else:
            await page.keyboard.press("Enter")

        print("⏳ 登录完成，等待跳转面板...")
        await asyncio.sleep(6)

        # 2. 寻找管理服务器入口
        print("🌐 2/4 正在访问服务器列表并准备进入管理控制台...")
        manage_btn = await page.query_selector('text=Управлять сервером, text=管理服务器, a[href*="/server/"]')
        if manage_btn:
            await manage_btn.click()
            print("✅ 已点击【Управлять сервером / 管理服务器】按钮")
            await asyncio.sleep(5)
        else:
            print("ℹ️ 未能匹配到 [Управлять сервером] 按钮，尝试直接检测控制台组件...")

        # 3. 检测控制台及运行状态
        print("🔍 3/4 正在检查服务器控制台状态...")
        try:
            await page.wait_for_selector('text=Консоль, text=Console, text=Старт, text=Рестарт', timeout=30000)
        except Exception:
            print("⚠️ 未识别到控制台核心按钮，保存当前页面截图 error_page_load.png ...")
            await page.screenshot(path="error_page_load.png")

        await asyncio.sleep(2)
        page_text = (await page.locator('body').inner_text()).lower()

        # 根据俄语面板状态判断：Включён (运行中) / Отключён (已关机/已禁用)
        if "включён" in page_text or "online" in page_text or "running" in page_text:
            print("🎉 服务器当前状态：【Включён / 运行中】")
            send_tg_message(f"👤 账户: `{user_email}`\n🖥️ 服务器状态: *Включён (运行中)*\n💡 操作结果: 无需重复启动。")
        else:
            print("⚠️ 服务器当前状态：【Отключён / 已关机】，准备发送启动指令...")
            
            start_btn = await page.query_selector('button:has-text("Старт"), button:has-text("Start"), button:has-text("开始")')
            restart_btn = await page.query_selector('button:has-text("Рестарт"), button:has-text("Restart"), button:has-text("重启")')

            target_btn = start_btn or restart_btn
            if target_btn:
                await target_btn.click()
                print("✅ 已成功点击【Старт 开始 / Рестарт 重启】按钮！")
            else:
                try:
                    await page.click('//button[contains(., "Старт") or contains(., "Рестарт")]')
                    print("✅ 保底选择器：已点击控制按钮！")
                except Exception as e:
                    print(f"⚠️ 保底点击未成功: {e}")

            # 处理二次弹窗确认
            await asyncio.sleep(2)
            confirm_btn = await page.query_selector("//button[contains(text(), '确认') or contains(text(), 'Yes') or contains(text(), 'Да')]")
            if confirm_btn:
                await confirm_btn.click()
                print("✅ 已点击二次确认弹窗")

            print("⏳ 等待 15 秒确认服务器启动状态...")
            await asyncio.sleep(15)

            new_text = (await page.locator('body').inner_text()).lower()
            if "включён" in new_text or "online" in new_text or "running" in new_text or "запуск" in new_text:
                print("🎉 服务器成功启动！状态已更新为 Включён")
                send_tg_message(f"👤 账户: `{user_email}`\n🖥️ 服务器状态: *已成功从 Отключён 启动 ✅*\n🚀 当前状态: Включён (运行中)")
            else:
                print("💡 已成功触发启动点击，服务器正在后台初始化开机...")
                send_tg_message(f"👤 账户: `{user_email}`\n🖥️ 服务器状态: *启动指令已发出 🚀*\n💡 请稍后在面板手动查看。")

        print(f"✅ 账户 {user_email} 处理完毕。")
        await browser.close()

async def main():
    if not ACCOUNTS_JSON:
        print("错误: 未找到 ACCOUNTS_JSON 环境变量，请检查 GitHub Secrets 配置。")
        sys.exit(1)

    try:
        accounts = json.loads(ACCOUNTS_JSON)
        for account in accounts:
            await process_account(account)
        send_tg_message("所有账户自动开机/检测轮询完毕。 🎉")
    except Exception as e:
        print(f"脚本运行错误: {str(e)}")
        send_tg_message(f"⚠️ 脚本运行出现错误，请检查 GitHub Actions 日志。\n错误详情: `{str(e)}`")

if __name__ == "__main__":
    asyncio.run(main())
