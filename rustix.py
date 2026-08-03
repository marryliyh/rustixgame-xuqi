import os
import sys
import time
import requests

TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
API_KEY = os.getenv("API_KEY", "")
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808")

SERVER_ID = "226fd977"
BASE_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
}

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

def get_session():
    session = requests.Session()
    session.headers.update(headers)
    if PROXY_URL:
        session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
    return session

def get_server_status(session):
    """获取服务器当前实时状态"""
    try:
        res = session.get(f"{BASE_URL}/resources", timeout=15)
        if res.status_code == 200:
            data = res.json()
            state = data.get("attributes", {}).get("current_state", "unknown")
            return state
        else:
            print(f"⚠️ 获取服务器状态失败: HTTP {res.status_code}")
            return "unknown"
    except Exception as e:
        print(f"⚠️ 查询状态异常: {e}")
        return "unknown"

def send_power_signal(session, signal):
    """发送电源指令 (start / restart)"""
    url = f"{BASE_URL}/power"
    res = session.post(url, json={"signal": signal}, timeout=15)
    return res.status_code == 204

def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY Secrets！")
        sys.exit(1)

    session = get_session()
    
    print("🔍 正在检查服务器当前运行状态...")
    initial_state = get_server_status(session)
    print(f"📊 当前服务器状态: [{initial_state}]")

    # 如果服务器已经处于运行中 (running)，则执行重启 (restart) 进行保活；如果是关机 (offline)，则执行开机 (start)
    target_signal = "restart" if initial_state == "running" else "start"
    
    print(f"⚡ 正在通过官方 Client API 发送 [{target_signal}] 指令...")
    if send_power_signal(session, target_signal):
        print(f"🎉 成功发送 [{target_signal}] 指令！等待 8 秒获取最新状态...")
        time.sleep(8)
        
        final_state = get_server_status(session)
        print(f"✅ 执行后服务器最新状态: [{final_state}]")
        
        notify_msg = f"🚀 操作执行成功！\n\n- 操作类型: {target_signal.upper()}\n- 初始状态: {initial_state}\n- 最终状态: {final_state}"
        notify(notify_msg)
    else:
        print("❌ 发送指令失败！请检查 API_KEY 是否有效。")
        notify("❌ Rustix 保活失败：API 拒绝请求，请检查 API_KEY。")
        sys.exit(1)

if __name__ == "__main__":
    main()
