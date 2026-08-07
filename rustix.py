import os
import sys
import time
import json
import subprocess

# 自动检查并补全必要依赖
for pkg in ["curl_cffi", "requests"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "requests[socks]"])

from curl_cffi import requests as cffi_requests
import requests

# 环境变量配置
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


def safe_parse_json(response):
    """安全解析 API 响应，防止 Cloudflare 人机验证网页或空内容导致程序崩溃"""
    if not response or not response.text:
        return None, "EMPTY_RESPONSE"
    
    text = response.text.strip()
    
    # 检查是否返回了 Cloudflare 人机验证网页
    if text.startswith("<") or "<html" in text.lower() or "just a moment" in text.lower():
        print("  └─ ⚠️ 收到 HTML 响应，疑似 Cloudflare 人机验证/防火墙拦截！")
        print(f"  └─ 响应片段: {text[:150]}...")
        return None, "CLOUDFLARE_HTML"
    
    try:
        data = response.json()
        return data, "OK"
    except json.JSONDecodeError:
        print(f"  └─ ⚠️ 非标准 JSON 响应: {text[:150]}...")
        return None, "JSON_DECODE_ERROR"


def check_status(headers, proxies):
    """查询服务器当前运行状态"""
    try:
        res = cffi_requests.get(
            f"{BASE_URL}/api/client/servers/{SERVER_ID}/resources",
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ API HTTP 状态码: {res.status_code}")
        
        data, parse_msg = safe_parse_json(res)
        if data and isinstance(data, dict):
            state = data.get("attributes", {}).get("current_state", "unknown")
            return state, parse_msg
        
        return "unknown", parse_msg
    except Exception as e:
        print(f"  └─ 请求发生网络异常: {e}")
        return "unknown", f"NETWORK_ERROR_{e}"


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    proxies = {
        "http": PROXY_URL,
        "https": PROXY_URL,
    } if PROXY_URL else None

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    print("🚀 启动 Rustix 保活流程 (完整防护与防崩溃模式)...")

    # 1. 探测服务器初始状态
    print("🔍 步骤 1: 请求 API 探测服务器当前状态...")
    init_status, diag_msg = check_status(headers, proxies)
    print(f"  └─ 探测结果: 状态=[{init_status}], 诊断=[{diag_msg}]")

    # 如果正常运行，直接完成退出
    if init_status in ["running", "starting"]:
        print("🎉 服务器正在正常运行中，无需重启。")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {init_status.upper()}")
        sys.exit(0)

    # 2. 下发开机/拉起指令
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
        print(f"  └─ 电源指令响应 HTTP 状态码: {p_res.status_code}")
        if p_res.status_code in [200, 204]:
            power_success = True
        else:
            _, p_msg = safe_parse_json(p_res)
            print(f"  └─ 电源指令响应异常: {p_msg}")
    except Exception as e:
        print(f"  └─ 下发电源指令异常: {e}")

    # 3. 轮询确认服务器状态
    print("⏳ 步骤 3: 轮询确认开机状态更新...")
    final_status = "unknown"
    final_diag = "NONE"
    
    for i in range(1, 6):
        time.sleep(4)
        curr_status, diag = check_status(headers, proxies)
        print(f"  └─ 轮询第 {i}/5 次: 状态=[{curr_status}], 诊断=[{diag}]")
        if curr_status in ["running", "starting"]:
            final_status = curr_status
            final_diag = diag
            break
        final_diag = diag

    # 4. 判定执行结果与通知
    if final_status in ["running", "starting"]:
        notify(f"🚀 Rustix 保活成功！\n\n- 当前最新状态: [{final_status.upper()}]")
        sys.exit(0)
    elif power_success:
        notify(
            f"⚠️ Rustix 保活注意\n\n"
            f"- 开机指令响应: HTTP 200/204 (指令已成功送到后台)\n"
            f"- 状态查询异常: [{final_diag}]\n"
            f"- 提示: API 接口可能返回了 Cloudflare 拦截页面或空响应，但开机信号已发出。"
        )
        sys.exit(0)
    else:
        notify(
            f"❌ Rustix 保活失败！\n\n"
            f"- 初始状态: [{init_status}]\n"
            f"- 最终状态: [{final_status}]\n"
            f"- 诊断信息: [{final_diag}]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
