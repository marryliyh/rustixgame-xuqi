import os
import sys
import time
import json
import subprocess

# 强制安装与更新 SeleniumBase 物理穿透引擎
for pkg in ["seleniumbase", "requests"]:
    try:
        __import__(pkg)
    except ImportError:
        print(f"📦 正在自动安装底层物理穿透依赖: {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", pkg])

import requests
from seleniumbase import Driver

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


def main():
    if not API_KEY:
        print("❌ 错误：未配置 API_KEY！")
        sys.exit(1)

    print("🚀 启动 Rustix 终极保活引擎 (SeleniumBase UC 模式 + OS 级物理坐标击穿)...")

    # 配置代理 (SeleniumBase 支持 socks5 代理格式)
    proxy_str = PROXY_URL if PROXY_URL else None

    # 初始化 SeleniumBase UC (Undetected-Chromedriver) 真机避险内核
    driver = Driver(
        uc=True,
        headless=False,  # 配合 Xvfb 显示真实 GUI 窗口，避免 Cloudflare 无头指纹拦截
        proxy=proxy_str,
        page_load_strategy="normal",
    )

    try:
        # 1. 带自动重连与 TLS 握手伪装地打开目标主页
        print("🌐 步骤 1: 启动 UC 浏览器，伪装物理指纹访问主页...")
        driver.uc_open_with_reconnect(BASE_URL, reconnect_time=6)

        # 2. 触发底层 OS 坐标物理点击击穿 Cloudflare Turnstile 人机验证框
        print("⏳ 步骤 2: 正在执行物理 GUI 坐标点击算法击穿 Cloudflare 防火墙...")
        try:
            # 自动寻找 Turnstile 验证框并使用 PyAutoGUI / xdotool 模拟真实鼠标轨迹点击
            driver.uc_gui_click_captcha()
            print("  └─ 🎯 物理点击指令已下发！")
        except Exception as e:
            print(f"  └─ 物理点击识别提示: {e}")

        # 等待页面通过 CF 校验并获取 cf_clearance
        time.sleep(5)

        # 验证页面标题，确认完全通过 Cloudflare 盾
        for attempt in range(1, 15):
            title = driver.title
            if "Just a moment" not in title and "Cloudflare" not in title and title.strip() != "":
                print(f"  └─ ✅ 防火墙击穿成功！目标页面标题: [{title}]")
                break
            time.sleep(1)

        # 3. 在完全解算且带有合法 cf_clearance Cookie 的浏览器环境中执行 API 查询
        print("🔍 步骤 3: 在已放行的浏览器栈内执行 API 查询运行状态...")
        get_status_js = f"""
        var callback = arguments[arguments.length - 1];
        fetch('/api/client/servers/{SERVER_ID}/resources', {{
            method: 'GET',
            headers: {{
                'Authorization': 'Bearer {API_KEY}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }}
        }})
        .then(response => response.json().then(data => ({ status: response.status, data: data })))
        .then(res => callback(res))
        .catch(err => callback({{ status: 0, error: err.toString() }}));
        """

        res_info = driver.execute_async_script(get_status_js)
        init_status = "unknown"

        if res_info and res_info.get("status") == 200:
            init_status = res_info.get("data", {}).get("attributes", {}).get("current_state", "unknown")
            print(f"  └─ 当前服务器状态: [{init_status.upper()}]")
        else:
            print(f"  └─ API 读取反馈: {res_info}")

        # 如果服务器已经在运行中，直接退出并通知
        if init_status in ["running", "starting"]:
            print("🎉 服务器正处于启动/运行状态，无需再次点击开机。")
            notify(f"🚀 Rustix 服务器运行正常！\n\n- 当前状态: [{init_status.upper()}]")
            driver.quit()
            sys.exit(0)

        # 4. 如果处于停止/未知状态，下发开机指令
        print(f"⚡ 步骤 4: 当前状态为 [{init_status}]，下发 [start] 电源启动指令...")
        send_start_js = f"""
        var callback = arguments[arguments.length - 1];
        fetch('/api/client/servers/{SERVER_ID}/power', {{
            method: 'POST',
            headers: {{
                'Authorization': 'Bearer {API_KEY}',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }},
            body: JSON.stringify({{ signal: 'start' }})
        }})
        .then(response => callback({{ status: response.status }}))
        .catch(err => callback({{ status: 0, error: err.toString() }}));
        """

        power_res = driver.execute_async_script(send_start_js)
        p_code = power_res.get("status", 0) if power_res else 0
        print(f"  └─ 电源指令响应 HTTP 状态码: {p_code}")

        # 5. 轮询确认服务器状态变更
        print("⏳ 步骤 5: 轮询确认服务器状态更新...")
        final_status = "unknown"
        for i in range(1, 6):
            time.sleep(4)
            check_res = driver.execute_async_script(get_status_js)
            if check_res and check_res.get("status") == 200:
                curr = check_res.get("data", {}).get("attributes", {}).get("current_state", "unknown")
                print(f"  └─ 轮询第 {i}/5 次状态: [{curr.upper()}]")
                if curr in ["running", "starting"]:
                    final_status = curr
                    break

        driver.quit()

        # 6. 判定结果并发送通知
        if final_status in ["running", "starting"]:
            notify(f"🚀 Rustix 保活成功！\n\n- 最新状态: [{final_status.upper()}]")
            sys.exit(0)
        elif p_code in [200, 204]:
            notify(f"🚀 Rustix 开机信号已送达！\n\n- API 响应: HTTP {p_code}\n- 指令已送达后台。")
            sys.exit(0)
        else:
            notify(f"❌ Rustix 启动失败！\n\n- 初始状态: [{init_status}]\n- 最终状态: [{final_status}]")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 运行遇到异常: {e}")
        try:
            driver.quit()
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
