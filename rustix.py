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
        print("警告: TG_TOKEN 或 TG_CHAT_ID 未设置，跳过消息发送。")
        return
        
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    formatted_text = f"*✅ rustix.me服务器自动重启通知*\n\n{text}"
    payload = {"chat_id": TG_CHAT_ID, "text": formatted_text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"发送 TG 消息失败: {e}")

async def process_account(account):
    """处理单个账户的逻辑"""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars"
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        print(f"\n>>> 开始处理账户: {account['user']}")
        # 建立连接即返回，防止死等 slow 资源
        await page.goto(LOGIN_URL, wait_until="commit", timeout=60000)

        # 1. 登录（显式等待输入框挂载）
        input_selector = '//*[@id="app"]/div[2]/div/div/div[2]/form/div/div[1]/div/input'
        await page.wait_for_selector(input_selector, timeout=30000)
        
        await page.fill(input_selector, account['user'])
        await page.fill('//*[@id="app"]/div[2]/div/div/div[2]/form/div/div[2]/div[2]/div/div/input', account['pwd'])
        await page.click('//*[@id="app"]/div[2]/div/div/div[2]/form/div/div[4]/button')

        # 2. 进入管理页
        await page.wait_for_selector('section', timeout=30000)
        await page.click('//*[@id="app"]/div[2]/div/div[3]/div[4]/section/div/div[1]/div[3]/div/div/div[2]/a')
        print("已进入管理页面，等待加载状态...")

        # 3. 智能等待控制台面板
        print("🔍 正在等待控制台面板加载...")
        try:
            await page.wait_for_selector('text=Стоп', timeout=30000)
        except Exception as e:
            print(f"❌ 页面加载超时，没看到控制台按钮。正在保存错误截图...")
            await page.screenshot(path="error_page_load.png")
            raise e
        
        # 4. 缓冲等待状态刷新
        await asyncio.sleep(2)
        page_text = await page.locator('body').inner_text()
        page_text_lower = page_text.lower()
        
        # 5. 判断服务器运行状态
        if "включён" in page_text_lower or "включен" in page_text_lower or "online" in page_text_lower or "running" in page_text_lower:
            print("🎉 服务器当前状态：运行中 (Online/Включён)")
            send_tg_message(f"👤 账户: `{account['user']}`\n状态: *Online*\n操作: 无需重启。")
        else:
            print("⚠️ 当前状态不是运行中，准备点击 🔄 Рестарт 按钮重启...")
            try:
                await page.locator('text=Рестарт').first.click()
                print("✅ 已成功点击 Рестарт 按钮")
            except Exception as e:
                print(f"❌ 点击重启按钮失败: {e}")
                await page.screenshot(path="error_click_restart.png")
                raise e
            
            # 确认弹窗
            confirm_btn = "//button[contains(text(), '确认') or contains(text(), 'Yes') or contains(text(), 'Да')]"
            if await page.query_selector(confirm_btn):
                await page.click(confirm_btn)
                print("✅ 已点击弹窗确认")
            
            # 等待 2 分钟刷新状态
            print("⏳ 等待 2 分钟让服务器缓一缓...")
            await asyncio.sleep(120)
            
            # 重新检查页面状态
            page_text_new = await page.locator('body').inner_text()
            page_text_new_lower = page_text_new.lower()
            if "включён" in page_text_new_lower or "включен" in page_text_new_lower or "online" in page_text_new_lower or "running" in page_text_new_lower:
                send_tg_message(f"👤 账户: `{account['user']}`\n服务器重启成功 ✅\n状态: *Online*")
            else:
                send_tg_message(f"👤 账户: `{account['user']}`\n服务器重启后状态异常 ⚠️\n请手动登录检查。")

        print(f"账户 {account['user']} 操作完成。")
        await browser.close()

async def main():
    if not ACCOUNTS_JSON:
        print("错误: 未找到 ACCOUNTS_JSON 环境变量，请检查 GitHub Secrets 配置。")
        sys.exit(1)

    try:
        accounts = json.loads(ACCOUNTS_JSON)
        for account in accounts:
            await process_account(account)
        send_tg_message("所有账户操作完毕。 🎉")
    except Exception as e:
        print(f"脚本运行错误: {str(e)}")
        send_tg_message(f"⚠️ 脚本运行出现错误，请检查 GitHub Actions 日志。\n错误详情: `{str(e)}`")

if __name__ == "__main__":
    asyncio.run(main())
