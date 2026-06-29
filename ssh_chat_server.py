"""
ssh_chat_server.py  —  Production-ready WebSocket Chat Server (English/Japanese)

Environment Variables:
  ADMIN_IP    : IP address recognized as administrator (Required)
  PORT        : Listen port (Default: 10000)
  LOG_FILE    : Path to log file (Default: /data/messages.txt)
  NTFY_TOPIC  : Topic name for ntfy.sh notifications (Optional)
"""

import asyncio
import logging
import os
import signal
import datetime
import http
import aiohttp
from typing import Optional

from websockets.server import serve

# ------------------------------------------------------------------ #
#  Language Configuration
# ------------------------------------------------------------------ #

MESSAGES = {
    "en": {
        "select_lang": "Select Language: 1) English, 2) 日本語",
        "welcome": "Welcome to Anonymous Message System v2.0",
        "ip_label": "Your IP: ",
        "user_prompt": "Username (1-20 chars)",
        "welcome_back": "Welcome back, {name}!",
        "hello": "Hello, {name}!",
        "menu": "Menu: 1) Send Message, 2) Check Replies",
        "msg_prompt": "Enter your message:",
        "msg_sent": "Message sent successfully.",
        "no_replies": "No new replies.",
        "reply_header": "Reply:"
    },
    "ja": {
        "select_lang": "言語を選択してください: 1) English, 2) 日本語",
        "welcome": "匿名メッセージシステム v2.0 へようこそ",
        "ip_label": "あなたのIP: ",
        "user_prompt": "ユーザー名 (1-20文字):",
        "welcome_back": "おかえりなさい、{name}さん！",
        "hello": "こんにちは、{name}さん！",
        "menu": "メニュー: 1) メッセージ送信, 2) 返信を確認",
        "msg_prompt": "メッセージを入力してください:",
        "msg_sent": "メッセージを送信しました。",
        "no_replies": "新しい返信はありません。",
        "reply_header": "返信:"
    }
}

# ------------------------------------------------------------------ #
#  Configuration / Logging
# ------------------------------------------------------------------ #

LOG_FILE = os.environ.get("LOG_FILE", "/data/messages.txt")
ADMIN_IP = os.environ.get("ADMIN_IP", "")
PORT = int(os.environ.get("PORT", "10000"))
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
_file_lock = asyncio.Lock()

# ------------------------------------------------------------------ #
#  Helper Functions
# ------------------------------------------------------------------ #

async def append_log(line: str) -> None:
    async with _file_lock:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    if NTFY_TOPIC:
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=line.encode("utf-8"))
        except Exception:
            pass

def _read_log_lines():
    if not os.path.exists(LOG_FILE) or os.path.getsize(LOG_FILE) == 0: return []
    with open(LOG_FILE, "r", encoding="utf-8") as f: return f.readlines()

def username_exists(username: str) -> bool:
    return any(f"[USER: {username}]" in line for line in _read_log_lines())

def get_messages_by_user(username: str) -> list[str]:
    return [line.rstrip() for line in _read_log_lines() if f"[USER: {username}]" in line]

def get_all_usernames() -> list[str]:
    seen = []
    for line in _read_log_lines():
        if "[USER: " in line:
            name = line.split("[USER: ")[1].split("]")[0]
            if name not in seen: seen.append(name)
    return seen

def check_username_reply(username: str) -> Optional[str]:
    path = os.path.join(os.path.dirname(LOG_FILE), f"reply_user_{username.replace(' ', '_')}.txt")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f: return f.read()
    return None

def save_username_reply(username: str, reply_text: str) -> None:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(os.path.join(os.path.dirname(LOG_FILE), f"reply_user_{username.replace(' ', '_')}.txt"), "w", encoding="utf-8") as f:
        f.write(f"[{now}] 返信: {reply_text}\n")

# ------------------------------------------------------------------ #
#  Session Handlers
# ------------------------------------------------------------------ #

def get_client_ip(websocket) -> str:
    headers = getattr(websocket, 'request_headers', {})
    for header in ["CF-Connecting-IP", "X-Forwarded-For", "X-Real-IP"]:
        if val := headers.get(header):
            return val.split(",")[0].strip()
    return websocket.remote_address[0]

async def run_admin_session(websocket, ip: str) -> None:
    await websocket.send(f"--- 管理者画面 ---\n認証成功 (あなたのIP: {ip})\n")
    usernames = get_all_usernames()
    if not usernames:
        await websocket.send("現在メッセージはありません。\n")
    else:
        await websocket.send(f"登録ユーザー数: {len(usernames)}\n")
        for uname in usernames:
            msgs = get_messages_by_user(uname)
            await websocket.send(f"\nユーザー: {uname} ({len(msgs)} 件)\n")
            for m in msgs: await websocket.send(f"  {m}\n")

    while True:
        await websocket.send("\n返信対象のユーザー名を入力 (exitで終了):\n> ")
        target = (await websocket.recv()).strip()
        if target.lower() == "exit": break
        if target not in usernames:
            await websocket.send("そのユーザーはいません。\n")
            continue
        await websocket.send(f"'{target}' への返信内容:\n> ")
        reply = (await websocket.recv()).strip()
        save_username_reply(target, reply)
        await websocket.send("返信を保存しました。\n")

async def run_guest_session(websocket, ip: str) -> None:
    await websocket.send(MESSAGES["en"]["select_lang"])
    lang_choice = (await websocket.recv()).strip()
    lang = "ja" if lang_choice == "2" else "en"
    msgs = MESSAGES[lang]

    await websocket.send(f"{msgs['welcome']}\n{msgs['ip_label']}{ip}\n{msgs['user_prompt']}")
    username = (await websocket.recv()).strip()[:20]
    
    await websocket.send(msgs["welcome_back"].format(name=username) if username_exists(username) else msgs["hello"].format(name=username) + "\n")

    await websocket.send(msgs["menu"])
    choice = (await websocket.recv()).strip()

    if choice == "1":
        await websocket.send(msgs["msg_prompt"])
        body = (await websocket.recv()).strip()
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await append_log(f"[{now}] [USER: {username}] [IP: {ip}] DATA: {body}")
        await websocket.send(f"{msgs['msg_sent']}\n")

    elif choice == "2":
        reply = check_username_reply(username)
        await websocket.send(f"\n{reply if reply else msgs['no_replies']}\n")

async def handle_ws(websocket) -> None:
    ip = get_client_ip(websocket)
    # デバッグ用に接続IPを表示するように変更
    print(f"Connection from: {ip} (Admin IP: {ADMIN_IP})")
    if ip == ADMIN_IP:
        await run_admin_session(websocket, ip)
    else:
        await run_guest_session(websocket, ip)

async def main() -> None:
    async with serve(handle_ws, "0.0.0.0", PORT):
        await asyncio.get_running_loop().create_future()

if __name__ == "__main__":
    asyncio.run(main())
