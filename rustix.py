import os
import sys
import time
import subprocess

# 自动补全必要依赖
for pkg in ["curl_cffi", "requests"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

from curl_cffi import requests as cffi_requests
import requests

# 环境变量获取
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

SERVER_ID = "226fd977"
BASE_URL = "https://my.rustix.me"


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


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    # 代理配置
    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    } if PROXY_URL else None

    # 伪装完整 Chrome 请求头
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    print("🚀 启动 Rustix 保活流程 (curl_cffi Chrome TLS 指纹抗封锁模式)...")

    # 1. 探测服务器状态
    init_status = "unknown"
    print("🔍 步骤 1: 请求 API 探测服务器状态...")
    try:
        res = cffi_requests.get(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ API 状态码: {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            init_status = data.get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 服务器状态: [{init_status}]")
        else:
            print(f"  └─ 响应异常内容: {res.text[:200]}")
    except Exception as e:
        print(f"  └─ 请求失败: {e}")

    # 若已经在运行，直接成功退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正在正常运行中，无需重启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 2. 下发开机指令
    print("⚡ 步骤 2: 下发 [start] 电源指令...")
    power_success = False
    try:
        p_res = cffi_requests.post(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/power",
            headers=headers,
            json={"signal": "start"},
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ 指令响应状态码: {p_res.status_code}")
        if p_res.status_code in [200, 204]:
            power_success = True
    except Exception as e:
        print(f"  └─ 下发指令异常: {e}")

    # 3. 轮询确认状态
    print("⏳ 步骤 3: 确认开机状态更新...")
    final_status = "unknown"
    for i in range(1, 6):
        time.sleep(4)
        try:
            check_res = cffi_requests.get(
                f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
                headers=headers,
                proxies=proxies,
                impersonate="chrome120",
                timeout=20,
            )
            if check_res.status_code == 200:
                curr_state = check_res.json().get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次: [{curr_state}]")
                if curr_state in ["running", "starting"]:
                    final_status = curr_state
                    break
        except Exception:
            pass

    # 4. 结果判定与通知
    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 最新状态: [{final_status.upper()}]")
        sys.exit(0)
    elif power_success:
        notify(f"🚀 Rustix 开机指令下发成功！\n\n- 接口返回 200/204\n- 服务器已成功触发拉起。")
        sys.exit(0)
    else:
        notify(f"⚠️ Rustix 保活状态异常！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
        sys.exit(1)


if __name__ == "__main__":
    main()
