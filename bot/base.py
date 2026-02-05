"""
机器人基类 - 抽像基础类
"""
import asyncio
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from telethon import TelegramClient
from core.config import config
from core.logger import get_logger
from core.session import SessionManager


class BotBase(ABC):
    """机器人基类"""
    
    def __init__(self, phone: str, bot_type: str):
        self.phone = phone
        self.bot_type = bot_type
        self.logger = get_logger(f"{bot_type}_{phone}")
        self.client: Optional[TelegramClient] = None
        self.running = False
        self.start_time: Optional[float] = None
        self.task_count = 0
        self.error_count = 0
        
        # 配置
        self.bot_config = config.load_account_config(phone)
    
    async def start(self):
        """启动机器人"""
        if self.running:
            self.logger.warning("Bot already running")
            return
        
        self.client = SessionManager.get_client(self.phone)
        if not self.client:
            self.logger.error("Failed to get client")
            return
        
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            self.logger.error("Not authorized")
            return
        
        self.running = True
        self.start_time = time.time()
        self.logger.info(f"Bot started successfully")
        
        # 注册事件处理器
        self._register_handlers()
        
        # 启动主循环
        await self._run_loop()
    
    async def stop(self):
        """停止机器人"""
        self.running = False
        if self.client and self.client.is_connected():
            self.client.disconnect()
        SessionManager.disconnect(self.phone)
        self.logger.info(f"Bot stopped. Tasks: {self.task_count}, Errors: {self.error_count}")
    
    def _register_handlers(self):
        """注册事件处理器 - 子类实现"""
        pass
    
    @abstractmethod
    async def _run_loop(self):
        """主循环 - 子类实现"""
        pass
    
    async def _safe_execute(self, coro, fallback=None):
        """安全执行协程"""
        try:
            return await asyncio.wait_for(coro, timeout=60)
        except asyncio.TimeoutError:
            self.logger.warning("Operation timeout")
            self.error_count += 1
            return fallback
        except Exception as e:
            self.logger.error(f"Execute failed: {e}")
            self.error_count += 1
            return fallback
    
    @property
    def uptime(self) -> float:
        """运行时间"""
        if not self.start_time:
            return 0
        return time.time() - self.start_time
    
    @property
    def status(self) -> Dict[str, Any]:
        """获取状态"""
        return {
            "phone": self.phone,
            "type": self.bot_type,
            "running": self.running,
            "uptime": f"{self.uptime:.1f}s",
            "tasks": self.task_count,
            "errors": self.error_count
        }
