import asyncio
import requests
import os
import json
import sys
import urllib.parse
import base64
import subprocess
import time
import shutil
import tarfile
import urllib.request
from playwright.async_api import async_playwright

# --- 从环境变量读取敏感信息 ---
TG_TOKEN = os.environ.get("TG_TOKEN")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID")
ACCOUNTS_JSON = os.environ.get("ACCOUNTS_JSON")
NODE_LINK = os.environ.get("NODE_LINK")

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

def ensure_sing_box():
    """检测或下载 sing-box 核心程序"""
    if shutil.which("sing-box") or os.path.exists("./sing-box"):
        return "./sing-box" if os.path.exists("./sing-box") else "sing-box"
    
    print("📥 未在系统中找到 sing-box，正在自动下载客户端...")
    url = "https://github.com/SagerNet/sing-box/releases/download/v1.11.0/sing-box-1.11.0-linux-amd64.tar.gz"
    tar_path = "sing-box.tar.gz"
    urllib.request.urlretrieve(url, tar_path)
    
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("/sing-box"):
                f = tar.extractfile(member)
                with open("sing-box", "wb") as out:
                    out.write(f.read())
                os.chmod("sing-box", 0o755)
                break
    if os.path.exists(tar_path):
        os.remove(tar_path)
    return "./sing-box"

def parse_and_setup_proxy(node_link):
    """解析 NODE_LINK 并配置代理，返回 (proxy_server_url, subprocess_handle)"""
    if not node_link or not node_link.strip():
        print("ℹ️ 未提供 NODE_LINK，将使用直连模式。")
        return None, None

    node_link = node_link.strip()

    # 1. 如果是基础 HTTP/SOCKS5 代理，Playwright 原生支持
    if node_link.startswith(("http://", "https://", "socks5://", "socks5h://")):
        print(f"✅ 检测到标准 HTTP/SOCKS5 代理，直接加载使用。")
        return node_link, None

    # 2. 如果是节点链接（VLESS / VMess / Trojan / Hysteria2 / TUIC）
    print("🌐 正在解析 NODE_LINK 节点信息，启动 sing-box 本地中转...")
    sing_box_bin = ensure_sing_box()

    parsed = urllib.parse.urlparse(node_link)
    scheme = parsed.scheme.lower()
    query = urllib.parse.parse_qs(parsed.query)

    def get_q(k, default=""):
        return query.get(k, [default])[0]

    outbound = {}

    if scheme == "vless":
        outbound = {
            "type": "vless",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port or 443,
            "uuid": parsed.username
        }
        if get_q("flow"):
            outbound["flow"] = get_q("flow")
        security = get_q("security")
        if security in ["tls", "reality"]:
            tls_cfg = {
                "enabled": True,
                "server_name": get_q("sni", parsed.hostname),
                "insecure": get_q("allowInsecure") == "1" or get_q("insecure") == "1"
            }
            if security == "reality":
                tls_cfg["reality"] = {
                    "enabled": True,
                    "public_key": get_q("pbk"),
                    "short_id": get_q("sid")
                }
            outbound["tls"] = tls_cfg
        net_type = get_q("type")
        if net_type in ["ws", "grpc"]:
            trans = {"type": net_type}
            if net_type == "ws":
                trans["path"] = get_q("path", "/")
                if get_q("host"):
                    trans["headers"] = {"Host": get_q("host")}
            elif net_type == "grpc":
                trans["service_name"] = get_q("serviceName")
            outbound["transport"] = trans

    elif scheme == "vmess":
        b64_str = node_link[8:]
        b64_str += "=" * (-len(b64_str) % 4)
        vdata = json.loads(base64.b64decode(b64_str).decode('utf-8'))
        outbound = {
            "type": "vmess",
            "tag": "proxy",
            "server": vdata.get("add"),
            "server_port": int(vdata.get("port", 443)),
            "uuid": vdata.get("id"),
            "alter_id": int(vdata.get("aid", 0)),
            "security": "auto"
        }
        if vdata.get("tls") == "tls":
            outbound["tls"] = {
                "enabled": True,
                "server_name": vdata.get("sni") or vdata.get("host") or vdata.get("add")
            }
        net_type = vdata.get("net")
        if net_type in ["ws", "grpc"]:
            trans = {"type": net_type}
            if net_type == "ws":
                trans["path"] = vdata.get("path", "/")
                if vdata.get("host"):
                    trans["headers"] = {"Host": vdata.get("host")}
            elif net_type == "grpc":
                trans["service_name"] = vdata.get("path")
            outbound["transport"] = trans

    elif scheme == "trojan":
        outbound = {
            "type": "trojan",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port or 443,
            "password": parsed.username or parsed.password,
            "tls": {
                "enabled": True,
                "server_name": get_q("sni", parsed.hostname),
                "insecure": get_q("allowInsecure") == "1"
            }
        }

    elif scheme in ["hysteria2", "hy2"]:
        outbound = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port or 443,
            "password": parsed.username or parsed.password,
            "tls": {
                "enabled": True,
                "server_name": get_q("sni", parsed.hostname),
                "insecure": get_q("insecure") == "1"
            }
        }

    elif scheme == "tuic":
        outbound = {
            "type": "tuic",
            "tag": "proxy",
            "server": parsed.hostname,
            "server_port": parsed.port or 443,
            "uuid": parsed.username,
            "password": parsed.password,
            "congestion_control": "bbr",
            "tls": {
                "enabled": True,
                "server_name": get_q("sni", parsed.hostname),
                "insecure": get_q("allow_insecure") == "1"
            }
        }
    else:
        raise ValueError(f"暂不支持的节点协议: {scheme}")

    config = {
        "inbounds": [
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 10808
            }
        ],
        "outbounds": [outbound]
    }

    with open("sing_box_config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    proc = subprocess.Popen([sing_box_bin, "run", "-c", "sing_box_config.json"])
    time.sleep(3)
    print("🚀 本地中转代理已启动 (127.0.0.1:10808)")
    return "http://127.0.0.1:10808", proc

async def process_account(account, proxy_server=None):
    """处理单个账户的逻辑"""
    async with async_playwright() as p:
        # 配置代理参数
        launch_kwargs = {"headless": True}
        if proxy_server:
            launch_kwargs["proxy"] = {"server": proxy_server}

        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        page = await context.new_page()

        print(f"\n>>> 开始处理账户: {account['user']}")
        # 优化点：调整等待条件为 domcontentloaded 并延长超时至 60 秒
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        # 1. 登录
        await page.fill('//*[@id="app"]/div[2]/div/div/div[2]/form/div/div[1]/div/input', account['user'])
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

    proxy_server, singbox_proc = None, None
    try:
        # 解析代理
        proxy_server, singbox_proc = parse_and_setup_proxy(NODE_LINK)

        accounts = json.loads(ACCOUNTS_JSON)
        for account in accounts:
            await process_account(account, proxy_server)
        send_tg_message("所有账户操作完毕。 🎉")
    except Exception as e:
        print(f"脚本运行错误: {str(e)}")
        send_tg_message(f"⚠️ 脚本运行出现错误，请检查 GitHub Actions 日志。\n错误详情: `{str(e)}`")
    finally:
        # 结束 sing-box 进程
        if singbox_proc:
            singbox_proc.terminate()

if __name__ == "__main__":
    asyncio.run(main())
