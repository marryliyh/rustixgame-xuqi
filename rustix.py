import os
import subprocess
import sys

# 1. 自动检测并补全安装 curl_cffi (Chrome TLS 指纹伪装库)
try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    print("📦 正在自动安装 curl_cffi 依赖...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "curl_cffi", "requests[socks]"]
    )
    from curl_cffi import requests as cffi_requests

import requests

# 环境变量获取
TG_TOKEN = os.getenv("TG_TOKEN", "")
TG_CHAT_ID = os.getenv("TG_CHAT_ID", "")
API_KEY = os.getenv("API_KEY", "").strip()
PROXY_URL = os.getenv("PROXY_URL", "socks5://127.0.0.1:10808").strip()

# 强制转换为 socks5h:// (让代理节点解析 DNS，彻底解决 GitHub Actions 本地 DNS 断连)
if PROXY_URL.startswith("socks5://"):
    PROXY_URL_SOCKS5H = PROXY_URL.replace("socks5://", "socks5h://")
elif (
    PROXY_URL
    and not PROXY_URL.startswith("socks5h://")
    and not PROXY_URL.startswith("http")
):
    PROXY_URL_SOCKS5H = f"socks5h://{PROXY_URL}"
else:
    PROXY_URL_SOCKS5H = PROXY_URL

SERVER_ID = "226fd977"
API_POWER_URL = f"https://my.rustix.me/api/client/servers/{SERVER_ID}/power"
API_STATUS_URL = (
    f"https://my.rustix.me/api/client/servers/{SERVER_ID}/resources"
)


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[TG] 未配置，跳过通知")
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
        print(f"[TG] 通知异常: {exc}")


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 使用 curl_cffi (Chrome 120 TLS 指纹 + SOCKS5h 远程 DNS) 发起保活请求...")
    print(f"🌐 当前代理配置: {PROXY_URL_SOCKS5H}")

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

    # 1. 查询服务器当前运行状态
    print("🔍 正在查询服务器状态...")
    status_res = "unknown"
    try:
        r_status = cffi_requests.get(
            API_STATUS_URL,
            headers=headers,
            proxies=proxies,
            impersonate="chrome120",
            timeout=25,
        )
        print(f"📊 状态查询 HTTP 响应码: {r_status.status_code}")
        if r_status.status_code == 200:
            data = r_status.json()
            status_res = (
                data.get("attributes", {}).get("current_state", "unknown")
            )
    except Exception as e:
        print(f"⚠️ 状态查询发生异常: {e}")

    print(f"📊 当前服务器运行状态: [{status_res}]")

    target_signal = "restart" if status_res == "running" else "start"
    print(f"⚡ 正在发送电源指令 [{target_signal}]...")

    # 2. 发送电源指令
    try:
        r_power = cffi_requests.post(
            API_POWER_URL,
            headers=headers,
            json={"signal": target_signal},
            proxies=proxies,
            impersonate="chrome120",
            timeout=25,
        )

        print(f"📄 电源指令响应 HTTP 码: {r_power.status_code}")

        if r_power.status_code in [200, 204]:
            print(f"🎉 成功发送 [{target_signal}] 指令！保活成功！")
            notify(
                f"🚀 Rustix 保活成功！\n\n- 操作类型:"
                f" {target_signal.upper()}\n- 初始状态: {status_res}\n- HTTP 码:"
                f" {r_power.status_code}"
            )
            sys.exit(0)
        else:
            print(f"❌ 发送失败，响应内容: {r_power.text[:300]}")
            notify(
                f"❌ Rustix 保活失败：HTTP {r_power.status_code}\n{r_power.text[:100]}"
            )
            sys.exit(1)

    except Exception as e:
        print(f"❌ 请求发生严重网络异常: {e}")
        notify(f"❌ Rustix 保活异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
