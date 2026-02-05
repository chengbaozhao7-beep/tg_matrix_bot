#!/usr/bin/env python3
"""直接登录测试"""
import asyncio
import sys
from pathlib import Path
from telethon import TelegramClient

data_dir = Path(__file__).parent / "data"
session_file = data_dir / "sessions" / f"{sys.argv[1]}.session"
phone = sys.argv[1]
code = sys.argv[2]
phone_code_hash = sys.argv[3]

async def verify():
    client = TelegramClient(str(session_file), 36469988, "1a159cebdf6ac8f98840201138990c22")
    await client.connect()
    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
    await client.disconnect()
    return True

asyncio.run(verify())
print("SUCCESS")
