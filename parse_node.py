import os
import sys
import json
import base64
from urllib.parse import urlparse, parse_qs, unquote

def parse_link_to_singbox():
    node_url = os.environ.get("NODE_LINK", "").strip()
    if not node_url:
        print("⚠️ NODE_LINK 为空，跳过解析。")
        sys.exit(0)

    outbound = {}
    try:
        if node_url.startswith("vless://"):
            u = urlparse(node_url)
            q = parse_qs(u.query)
            uuid = u.username
            host = u.hostname
            port = u.port or 443
            sni = q.get("sni", [q.get("host", [host])[0]])[0]
            sec = q.get("security", ["none"])[0]
            net_type = q.get("type", ["tcp"])[0]
            path = q.get("path", ["/"])[0]
            ws_host = q.get("host", [sni])[0]
            insecure = q.get("insecure", q.get("allowInsecure", ["0"]))[0] in ["1", "true"]

            outbound = {
                "type": "vless", "tag": "proxy", "server": host, "server_port": int(port), "uuid": uuid
            }
            if sec in ["tls", "reality"]:
                outbound["tls"] = {"enabled": True, "server_name": sni, "insecure": insecure}
                if sec == "reality":
                    outbound["tls"]["reality"] = {
                        "enabled": True,
                        "public_key": q.get("pbk", [""])[0],
                        "short_id": q.get("sid", [""])[0]
                    }
            if net_type == "ws":
                outbound["transport"] = {"type": "ws", "path": unquote(path), "headers": {"Host": ws_host}}

        elif node_url.startswith("trojan://"):
            u = urlparse(node_url)
            q = parse_qs(u.query)
            pwd = u.username
            host = u.hostname
            port = u.port or 443
            sni = q.get("sni", [q.get("host", [host])[0]])[0]
            net_type = q.get("type", ["tcp"])[0]
            path = q.get("path", ["/"])[0]
            ws_host = q.get("host", [sni])[0]
            insecure = q.get("insecure", q.get("allowInsecure", ["0"]))[0] in ["1", "true"]

            outbound = {
                "type": "trojan", "tag": "proxy", "server": host, "server_port": int(port), "password": pwd,
                "tls": {"enabled": True, "server_name": sni, "insecure": insecure}
            }
            if net_type == "ws":
                outbound["transport"] = {"type": "ws", "path": unquote(path), "headers": {"Host": ws_host}}

        elif node_url.startswith("vmess://"):
            b64_str = node_url[8:].strip()
            b64_str += "=" * (-len(b64_str) % 4)
            v_data = json.loads(base64.b64decode(b64_str).decode("utf-8"))
            host = v_data.get("add")
            port = int(v_data.get("port", 443))
            uuid = v_data.get("id")
            aid = int(v_data.get("aid", 0))
            scy = v_data.get("scy", "auto")
            net = v_data.get("net", "tcp")
            tls_str = v_data.get("tls", "")
            sni = v_data.get("sni", v_data.get("host", host))
            ws_host = v_data.get("host", sni)
            path = v_data.get("path", "/")
            insecure = str(v_data.get("insecure", "0")) in ["1", "true"]

            outbound = {
                "type": "vmess", "tag": "proxy", "server": host, "server_port": port,
                "uuid": uuid, "security": scy, "alter_id": aid
            }
            if tls_str == "tls":
                outbound["tls"] = {"enabled": True, "server_name": sni, "insecure": insecure}
            if net == "ws":
                outbound["transport"] = {"type": "ws", "path": unquote(path), "headers": {"Host": ws_host}}

        elif node_url.startswith("hysteria2://") or node_url.startswith("hy2://"):
            u = urlparse(node_url)
            q = parse_qs(u.query)
            pwd = u.username
            host = u.hostname
            port = u.port or 443
            sni = q.get("sni", [host])[0]
            insecure = q.get("insecure", q.get("allowInsecure", ["0"]))[0] in ["1", "true"]

            outbound = {
                "type": "hysteria2", "tag": "proxy", "server": host, "server_port": int(port), "password": pwd,
                "tls": {"enabled": True, "server_name": sni, "insecure": insecure}
            }
        else:
            print(f"❌ 不支持的节点协议类型: {node_url[:15]}")
            sys.exit(1)

        config = {
            "inbounds": [{"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": 10808}],
            "outbounds": [outbound]
        }
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        print("✅ 节点链接已成功解析并生成 sing-box 配置 config.json！")

    except Exception as e:
        print(f"❌ 解析节点失败: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parse_link_to_singbox()
