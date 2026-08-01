import os
import json
import sys
import requests
from bs4 import BeautifulSoup

# --- 从环境变量读取敏感信息 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
ACCOUNTS_JSON = os.environ.get("ACCOUNTS_JSON")

BASE_URL = "https://my.rustix.me"
LOGIN_URL = f"{BASE_URL}/auth/login"

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

def process_account(account):
    """使用 requests 会话底层直接交互"""
    print(f"\n>>> 开始处理账户: {account['user']}")
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,*/*;q=0.8"
    })

    # 1. 访问登录页获取 cookies / CSRF token（如有）
    try:
        res = session.get(LOGIN_URL, timeout=30)
        print(f"打开登录页状态码: {res.status_code}")
    except Exception as e:
        print(f"❌ 连接登录页失败: {e}")
        raise e

    # 2. 模拟表单提交登录
    # 注意：如果 rustix.me 的登录接口是 API 或者是标准表单，请根据实际情况调整字段
    login_data = {
        "email": account['user'],
        "password": account['pwd']
    }
    
    # 尝试 POST 登录
    login_res = session.post(LOGIN_URL, data=login_data, timeout=30, allow_redirects=True)
    print(f"登录请求响应状态码: {login_res.status_code}")

    # 3. 访问后台面板检查服务器状态
    # 假设登录成功后重定向或直接访问控制台列表页
    dashboard_url = f"{BASE_URL}/" # 根据实际后台面板调整
    dash_res = session.get(dashboard_url, timeout=30)
    
    soup = BeautifulSoup(dash_res.text, 'html.parser')
    page_text = soup.get_text().lower()

    print("🔍 正在检查服务器状态...")
    if "включён" in page_text or "включен" in page_text or "online" in page_text or "running" in page_text:
        print("🎉 服务器当前状态：运行中 (Online/Включён)")
        send_tg_message(f"👤 账户: `{account['user']}`\n状态: *Online*\n操作: 无需重启。")
    else:
        print("⚠️ 当前状态不是运行中，尝试发送重启请求...")
        # 如果有具体的重启 API 链接可以在此发起请求
        # 示例：session.get(f"{BASE_URL}/api/restart", timeout=30)
        
        send_tg_message(f"👤 账户: `{account['user']}`\n服务器可能离线，请注意检查。")

    print(f"账户 {account['user']} 操作完成。")

def main():
    if not ACCOUNTS_JSON:
        print("错误: 未找到 ACCOUNTS_JSON 环境变量，请检查 GitHub Secrets 配置。")
        sys.exit(1)

    try:
        accounts = json.loads(ACCOUNTS_JSON)
        for account in accounts:
            process_account(account)
        send_tg_message("所有账户操作完毕。 🎉")
    except Exception as e:
        print(f"脚本运行错误: {str(e)}")
        send_tg_message(f"⚠️ 脚本运行出现错误，请检查 GitHub Actions 日志。\n错误详情: `{str(e)}`")

if __name__ == "__main__":
    main()
