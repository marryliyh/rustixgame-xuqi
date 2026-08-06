import os
import sys
import time
import subprocess

# 1. 自动依赖管理：按需安装 curl_cffi 与 requests
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("📦 正在自动安装 curl_cffi 依赖...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "curl_cffi", "requests[socks]"])
    from curl_cffi import requests as cffi_requests

import requests

# 2. 环境变量配置
TG_TOKEN = os.getenv("TG_TOKEN", "").strip()
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "").strip()
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

# 强制转换代理为 socks5h:// (由远程代理节点进行 DNS 解析，彻底解决 GitHub Actions 本地 DNS 断连问题)
if PROXY_URL.startswith("socks5://"):
    PROXY_URL_SOCKS5H = PROXY_URL.replace("socks5://", "socks5h://")
elif PROXY_URL and not PROXY_URL.startswith("socks5h://") and not PROXY_URL.startswith("http"):
    PROXY_URL_SOCKS5H = f"socks5h://{PROXY_URL}"
else:
    PROXY_URL_SOCKS5H = PROXY_URL

SERVER_ID = "226fd977"
BASE_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}"
API_SERVER_URL = BASE_URL
API_STATUS_URL = f"{BASE_URL}/resources"
API_POWER_URL = f"{BASE_URL}/power"


def notify(text):
    """发送 Telegram 机器人通知"""
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置 TG_TOKEN 或 TG_CHAT_ID，跳过通知")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT_ID,
                "text": f"Rustix 自动保活通知\n\n{text}",
            },
            timeout=15,
        )
        print("[TG] 通知发送成功" if r.ok else f"[TG] 通知失败: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[TG] 通知发送异常: {exc}")


def get_server_status(headers, proxies):
    """
    双重检测服务器状态：
    1. 优先查 /resources 接口获取 current_state (running / starting / offline)
    2. 若 /resources 接口为空或非 JSON 报错，回退查主 server 接口
    """
    # 尝试方法 1: /resources 接口
    try:
        r = cffi_requests.get(
            API_STATUS_URL,
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if r.status_code == 200 and r.text and r.text.strip():
            try:
                data = r.json()
                status = data.get("attributes", {}).get("current_state", "unknown")
                if status and status != "unknown":
                    return status
            except Exception:
                pass
    except Exception as e:
        print(f"  └─ [/resources 接口检测提示: {e}]")

    # 尝试方法 2: /api/client/servers/{SERVER_ID} 接口
    try:
        r_server = cffi_requests.get(
            API_SERVER_URL,
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=15,
        )
        if r_server.status_code == 200 and r_server.text and r_server.text.strip():
            try:
                data = r_server.json()
                attr = data.get("attributes", {})
                if attr.get("is_suspended"):
                    return "suspended"
                status_attr = attr.get("status")
                if status_attr:
                    return status_attr
            except Exception:
                pass
    except Exception as e:
        print(f"  └─ [/server 接口检测提示: {e}]")

    return "offline"


def send_power_signal(signal, headers, proxies):
    """发送电源指令 (start / restart / stop)"""
    try:
        r = cffi_requests.post(
            API_POWER_URL,
            headers=headers,
            json={"signal": signal},
            proxies=proxies,
            impersonate="chrome120",
            timeout=20,
        )
        print(f"  └─ 指令 [{signal}] 发送响应: HTTP {r.status_code}")
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"  └─ 发送 [{signal}] 异常: {e}")
        return False


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 强化版保活脚本 (Chrome TLS 指纹 + SOCKS5h + 状态真实校验)...")
    print(f"🌐 当前网络代理: {PROXY_URL_SOCKS5H}")

    proxies = (
        {"http": PROXY_URL_SOCKS5H, "https": PROXY_URL_SOCKS5H}
        if PROXY_URL_SOCKS5H
        else None
    )

    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    }

    # 1. 初始状态查询
    print("🔍 步骤 1: 正在检测服务器初始运行状态...")
    initial_status = get_server_status(headers, proxies)
    print(f"📊 服务器当前初始状态: [{initial_status}]")

    if initial_status in ["running", "starting"]:
        print(f"🎉 服务器目前已处于 [{initial_status}] 状态，无需重复启动！")
        notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: {initial_status.upper()}\n- 结论: 已处于活跃状态")
        sys.exit(0)

    # 2. 阶梯式发送电源指令（应对 "Сервер помечен как отключён" 假死问题）
    print("\n⚡ 步骤 2: 尝试解除假死状态并发送启动指令...")

    # 第一次：发送 start 指令
    print("👉 尝试发送 [start] 信号...")
    send_power_signal("start", headers, proxies)

    # 等待 4 秒，检查是否成功唤醒
    time.sleep(4)
    check_status = get_server_status(headers, proxies)
    print(f"📊 [start] 信号后检测状态: [{check_status}]")

    # 如果 start 没能成功拉起，发送 restart 强制重置后台 Wings 容器 Socket
    if check_status not in ["running", "starting"]:
        print("⚠️ 提示: [start] 指令未能唤醒容器，正在发送 [restart] 信号强制刷新后台 Wings 节点...")
        send_power_signal("restart", headers, proxies)
        time.sleep(5)

        # 补发一次 start 确认激活
        check_status = get_server_status(headers, proxies)
        if check_status not in ["running", "starting"]:
            print("👉 补发 [start] 指令进行再次确认...")
            send_power_signal("start", headers, proxies)

    # 3. 轮询验证最终运行状态（最多等待 20 秒）
    print("\n⏳ 步骤 3: 进入状态轮询确认阶段 (验证服务器是否真实启动)...")
    final_status = "offline"
    for i in range(1, 6):
        time.sleep(4)
        curr_status = get_server_status(headers, proxies)
        print(f"  └─ 轮询第 {i}/5 次校验: 当前状态 [{curr_status}]")
        if curr_status in ["running", "starting"]:
            final_status = curr_status
            break

    # 4. 最终结果评判与 Telegram 通知
    print("\n📋 步骤 4: 保活执行结果汇总")
    if final_status in ["running", "starting"]:
        print(f"🎉 成功！服务器已真正启动并运行，最终状态: [{final_status}]")
        notify(
            f"🚀 Rustix 保活成功！\n\n"
            f"- 初始状态: {initial_status.upper()}\n"
            f"- 最终状态: {final_status.upper()}\n"
            f"- 结论: 服务器已成功激活进入 [{final_status.upper()}] 状态！"
        )
        sys.exit(0)
    else:
        print(f"❌ 警告: 发送指令后，服务器最终状态仍为 [{final_status}]！")
        notify(
            f"⚠️ Rustix 保活异常告警！\n\n"
            f"- 初始状态: {initial_status}\n"
            f"- 最终状态: {final_status}\n"
            f"- 提示: API 返回 HTTP 200 但服务器仍为关闭状态，建议登录 Rustix 面板手动点击一次 [Старт]。"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
