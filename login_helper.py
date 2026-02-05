#!/usr/bin/env python3
"""登录辅助脚本"""
import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

data_dir = Path(__file__).parent / "data"

def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "send_code"
    phone = sys.argv[2] if len(sys.argv) > 2 else ""
    api_id = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    api_hash = sys.argv[4] if len(sys.argv) > 4 else ""
    code = sys.argv[5] if len(sys.argv) > 5 else ""
    phone_code_hash = sys.argv[6] if len(sys.argv) > 6 else ""
    password = sys.argv[7] if len(sys.argv) > 7 else ""
    
    session_file = data_dir / "sessions" / f"{phone}.session"
    
    async def send_code():
        client = TelegramClient(str(session_file), api_id, api_hash)
        await client.connect()
        sent = await client.send_code_request(phone)
        await client.disconnect()
        return sent.phone_code_hash
    
    async def verify_code():
        client = TelegramClient(str(session_file), api_id, api_hash)
        await client.connect()
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash, password=password)
        await client.disconnect()
        return True
    
    if action == "send_code":
        try:
            result = asyncio.run(send_code())
            print(f"SUCCESS:{result}")
        except Exception as e:
            print(f"ERROR:{e}")
    elif action == "verify_code":
        try:
            asyncio.run(verify_code())
            print("SUCCESS")
        except Exception as e:
            print(f"ERROR:{e}")

if __name__ == "__main__":
    main()
