import socket
import threading
import paramiko
import os
import datetime
import hashlib
import base64

LOG_FILE = "messages.txt"  # メッセージ、番号、訪問者の公開鍵を溜めるファイル

# === 🔓 あなた（管理者）の公開鍵を完全に埋め込みました ===
MY_PUBLIC_KEY_STR = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGLajl7T23hQ61UMs2c8Tjraar7svNb3heMkuUEs4PgD ryotagtagtag@gmail.com" 

def get_admin_key():
    try:
        parts = MY_PUBLIC_KEY_STR.strip().split()
        if len(parts) >= 2:
            key_type, key_data = parts[0], base64.b64decode(parts[1])
            if key_type == "ssh-rsa": return paramiko.RSAKey(data=key_data)
            elif key_type == "ssh-ed25519": return paramiko.Ed25519Key(data=key_data)
            elif key_type == "ecdsa-sha2-nistp256": return paramiko.ECDSAKey(data=key_data)
    except Exception as e:
        print(f"管理者鍵のパースエラー: {e}")
    return None

class ChatServer(paramiko.ServerInterface):
    def __init__(self):
        self.event = threading.Event()
        self.is_admin = False
        self.client_key_fingerprint = ""

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_REFUSED

    def check_auth_none(self, username):
        # 鍵なしの接続は拒否し、必ずクライアントにSSH鍵を出させる
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        """接続してきた人の公開鍵をチェック・記録する"""
        # 鍵の固有ハッシュ（SHA256）を計算
        self.client_key_fingerprint = hashlib.sha256(key.get_fingerprint()).hexdigest()
        
        # 管理者鍵との照合
        admin_key = get_admin_key()
        if admin_key and key == admin_key:
            self.is_admin = True
        else:
            self.is_admin = False
            
        return paramiko.AUTH_SUCCESSFUL

    def check_channel_shell_request(self, channel):
        self.event.set()
        return True

    def check_channel_pty_request(self, channel, *args): return True

def get_next_msg_id():
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0: return 1
    max_id = 0
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("ID:"):
                try:
                    # 「ID: #1」から「1」を抽出
                    parts = line.split()
                    msg_id = int(parts[1].replace("#", ""))
                    if msg_id > max_id: max_id = msg_id
                except: pass
    return max_id + 1

def check_visitor_reply(msg_id, current_key_fp):
    """訪問者が自分の番号の返信を読む権限があるか（鍵が一致するか）確認する"""
    if not os.path.exists(LOG_FILE): return "📭 まだメッセージ履歴がありません。"
    
    target_fp = ""
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(f"ID: #{msg_id} "):
                parts = line.split(" KEY_FP: ")
                if len(parts) > 1:
                    target_fp = parts[1].strip()
                    break
                    
    if not target_fp:
        return f"❌ #{msg_id} というメッセージは見つかりませんでした。"

    # 鍵ハッシュが完全一致した場合のみ返信を表示
    if target_fp == current_key_fp:
        reply_file = f"reply_{msg_id}.txt"
        if os.path.exists(reply_file):
            with open(reply_file, "r", encoding="utf-8") as f:
                return f.read()
        else:
            return "⏳ メッセージは届いていますが、まだ管理者からの返信はありません。"
    
    return "❌ 認証エラー: このメッセージを残した鍵とは異なるため、返信を読めません。"

def ssh_input(chan, prompt="> "):
    chan.send(prompt)
    buffer = ""
    while True:
        char = chan.recv(1).decode('utf-8', errors='ignore')
        if not char: break
        if char in ('\r', '\n'): break
        elif char == '\x7f':
            if len(buffer) > 0:
                buffer = buffer[:-1]
                chan.send("\b \b")
        else:
            buffer += char
            chan.send(char)
    chan.send("\r\n")
    return buffer.strip()

def handle_client(client_socket, host_key):
    transport = paramiko.Transport(client_socket)
    transport.add_server_key(host_key)
    server = ChatServer()
    try: 
        transport.start_server(server=server)
    except: 
        return
    chan = transport.accept(20)
    if chan is None: return
    server.event.wait()

    # 🔓 1. 管理者（あなた）がログインした場合
    if server.is_admin:
        chan.send("\r\n=== 📬 管理者コントロールパネル ===\r\n")
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > 0:
            chan.send("届いているメッセージ一覧:\r\n")
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                chan.send(f.read().replace("\n", "\r\n"))
            
            target_id = ssh_input(chan, "\r\n返信したいメッセージの番号を入力してください: ")
            if target_id.isdigit():
                reply_msg = ssh_input(chan, f"#{target_id} への返信内容を入力してください:\r\n> ")
                if reply_msg:
                    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    with open(f"reply_{target_id}.txt", "w", encoding="utf-8") as f:
                        f.write(f"[{now}] 管理者からの返信:\n{reply_msg}\n")
                    chan.send(f"✅ #{target_id} への返信を保存しました。\r\n")
        else:
            chan.send("📭 新しいメッセージはありません。\r\n")
        chan.close()
        transport.close()
        return

    # 👥 2. 訪問者が接続した場合
    else:
        chan.send("\r\n=== 📜 SSH 匿名伝言ポスト ===\r\n")
        chan.send("1: メッセージを残す\r\n2: 自分への返信を確認する\r\n")
        choice = ssh_input(chan, "メニュー番号を選んでください (1 or 2): ")

        # メッセージを残す
        if choice == "1":
            user_msg = ssh_input(chan, "メッセージを入力してください:\r\n> ")
            if user_msg:
                msg_id = get_next_msg_id()
                now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                with open(LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(f"ID: #{msg_id} TIME: [{now}] MSG: {user_msg} KEY_FP: {server.client_key_fingerprint}\n")
                
                chan.send(f"\r\n✅ 送信完了しました！\r\n")
                chan.send(f"あなたのメッセージ番号は 【 #{msg_id} 】 です。\r\n")
                chan.send(f"返信確認に必要ですのでメモしておいてください。\r\n")

        # 返信を確認する
        elif choice == "2":
            check_id = ssh_input(chan, "あなたのメッセージ番号を入力してください: ")
            if check_id.isdigit():
                result_text = check_visitor_reply(check_id, server.client_key_fingerprint)
                chan.send(f"\r\n{result_text}\r\n")
        
        chan.close()
        transport.close()

def main():
    # サーバー用ホスト鍵の生成・読み込み
    try: 
        host_key = paramiko.RSAKey(filename='test_rsa.key')
    except IOError:
        host_key = paramiko.RSAKey.generate(2048)
        host_key.write_private_key_file('test_rsa.key')

    # クラウドの割当ポートまたはデフォルト2222で起動
    port = int(os.environ.get("PORT", 2222))
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind(('0.0.0.0', port))
    server_socket.listen(100)
    print(f"SSH 伝言サーバーがポート {port} で起動しました...")

    while True:
        try:
            client_socket, addr = server_socket.accept()
            threading.Thread(target=handle_client, args=(client_socket, host_key), daemon=True).start()
        except KeyboardInterrupt:
            break

if __name__ == '__main__':
    main()
