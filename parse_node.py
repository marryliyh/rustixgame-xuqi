import os
import json
import urllib.parse
import sys

node_link = os.environ.get("NODE_LINK", "").strip()

if not node_link:
    print("❌ 未在 Secrets 中配置 NODE_LINK！")
    sys.exit(1)

def parse_link(link):
    parsed = urllib.parse.urlparse(link)
    scheme = parsed.scheme.lower()
    query = urllib.parse.parse_qs(parsed.query)
    get_q = lambda k, default="": query.get(k, [default])[0]

    outbound = {}
    
    if scheme == "vless":
        uuid = parsed.username
        host = parsed.hostname
        port = parsed.port or 443
        
        outbound = {
            "type": "vless",
            "tag": "proxy",
            "server": host,
            "server_port": int(port),
            "uuid": uuid,
        }
        
        flow = get_q("flow")
        if flow:
            outbound["flow"] = flow
            
        security = get_q("security")
        net_type = get_q("type", "tcp")
        
        if net_type == "ws":
            outbound["transport"] = {
                "type": "ws",
                "path": get_q("path", "/"),
                "headers": {"Host": get_q("host")} if get_q("host") else {}
            }
        elif net_type == "grpc":
            outbound["transport"] = {
                "type": "grpc",
                "service_name": get_q("serviceName")
            }

        if security in ["tls", "reality"]:
            sni = get_q("sni") or get_q("peer") or host
            fp = get_q("fp", "chrome")
            
            tls_cfg = {
                "enabled": True,
                "server_name": sni,
                "utls": {
                    "enabled": True,
                    "fingerprint": fp
                }
            }
            
            if security == "reality":
                pbk = get_q("pbk")
                sid = get_q("sid") or get_q("spx") or ""
                tls_cfg["reality"] = {
                    "enabled": True,
                    "public_key": pbk,
                    "short_id": sid
                }
                
            outbound["tls"] = tls_cfg

    elif scheme in ["hysteria2", "hy2"]:
        auth = parsed.username or parsed.password
        host = parsed.hostname
        port = parsed.port or 443
        sni = get_q("sni") or host
        insecure = get_q("insecure") in ["1", "true"]
        
        outbound = {
            "type": "hysteria2",
            "tag": "proxy",
            "server": host,
            "server_port": int(port),
            "password": auth,
            "tls": {
                "enabled": True,
                "server_name": sni,
                "insecure": insecure
            }
        }

    elif scheme == "trojan":
        password = parsed.username
        host = parsed.hostname
        port = parsed.port or 443
        sni = get_q("sni") or host
        
        outbound = {
            "type": "trojan",
            "tag": "proxy",
            "server": host,
            "server_port": int(port),
            "password": password,
            "tls": {
                "enabled": True,
                "server_name": sni
            }
        }
    else:
        raise Exception(f"暂不支持的协议类型: {scheme}")

    return outbound

try:
    outbound = parse_link(node_link)
    config = {
        "log": {"level": "info"},
        "inbounds": [
            {
                "type": "mixed", # 💡 启用 mixed 模式，同时支持 HTTP (10809) 和 SOCKS5 (10808)
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": 10809
            }
        ],
        "outbounds": [
            outbound,
            {"type": "direct", "tag": "direct"}
        ]
    }
    with open("config.json", "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print("✅ 已成功写入 sing-box HTTP/Mixed 代理配置文件 (127.0.0.1:10809)！")
except Exception as e:
    print(f"❌ 解析 NODE_LINK 失败: {e}")
    sys.exit(1)
