"""
Session管理器 - 管理Telegram会话
"""
import os
from pathlib import Path
from typing import Optional
from telethon import TelegramClient
from telethon.sessions import SQLiteSession
from core.config import config
from core.logger import get_logger


class SessionManager:
    """Session管理器"""
    
    _sessions: dict = {}
    
    @classmethod
    def get_session_path(cls, phone: str) -> Path:
        """获取session文件路径"""
        session_dir = Path(__file__).parent.parent / "data" / "sessions"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir / f"{phone}.session"
    
    @classmethod
    def get_client(cls, phone: str) -> Optional[TelegramClient]:
        """获取或创建Telegram客户端"""
        if phone in cls._sessions:
            client = cls._sessions[phone]
            if client.is_connected():
                return client
            else:
                del cls._sessions[phone]
        
        api_id = config.get_account_config(phone).get('api_id')
        api_hash = config.get_account_config(phone).get('api_hash')
        proxy = config.get_proxy(phone)
        
        session_path = cls.get_session_path(phone)
        client = TelegramClient(
            str(session_path),
            api_id,
            api_hash,
            proxy=proxy
        )
        
        cls._sessions[phone] = client
        return client
    
    @classmethod
    async def is_authorized(cls, phone: str) -> bool:
        """检查是否已授权"""
        client = cls.get_client(phone)
        if client:
            await client.connect()
            return await client.is_user_authorized()
        return False
    
    @classmethod
    async def send_code_request(cls, phone: str) -> tuple:
        """发送验证码请求"""
        client = cls.get_client(phone)
        await client.connect()
        sent = await client.send_code_request(phone)
        return sent.phone_code_hash
    
    @classmethod
    async def sign_in(cls, phone: str, code: str, phone_code_hash: str, 
                     password: Optional[str] = None) -> bool:
        """登录"""
        client = cls.get_client(phone)
        try:
            await client.sign_in(
                phone=phone,
                code=code,
                phone_code_hash=phone_code_hash,
                password=password
            )
            return True
        except Exception as e:
            get_logger(__name__).error(f"Sign in failed: {e}")
            return False
    
    @classmethod
    def disconnect(cls, phone: str):
        """断开连接"""
        if phone in cls._sessions:
            client = cls._sessions[phone]
            if client.is_connected():
                client.disconnect()
            del cls._sessions[phone]
    
    @classmethod
    def disconnect_all(cls):
        """断开所有连接"""
        for phone in list(cls._sessions.keys()):
            cls.disconnect(phone)
