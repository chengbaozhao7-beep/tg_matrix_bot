#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bot import giveaway
from bot_engine import config, log_manager

# 重新初始化log_manager以使用subprocess
log_manager._logs = {}
log_manager._file_handler = None
log_manager._socketio = None  # 子进程不使用WebSocket

async def run():
    bot = giveaway.GiveawayBot("+254746852508")
    bot.backfill_mode = True
    bot.backfill_days = 1
    await bot.start()

asyncio.run(run())
