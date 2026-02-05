"""
日志系统 - 统一日志管理
"""
import os
import logging
import logging.handlers
from pathlib import Path
from typing import Optional


class LoggerManager:
    """日志管理器"""
    
    _instance: Optional['LoggerManager'] = None
    _loggers: dict = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def _get_log_dir(self) -> Path:
        """获取日志目录"""
        log_dir = Path(__file__).parent.parent / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    
    def _setup_logger(self, name: str, level: str = "INFO") -> logging.Logger:
        """设置logger"""
        if name in self._loggers:
            return self._loggers[name]
        
        logger = logging.getLogger(name)
        log_level = getattr(logging, level.upper(), logging.INFO)
        logger.setLevel(log_level)
        
        # 避免重复添加handler
        if not logger.handlers:
            # 文件日志
            log_file = self._get_log_dir() / f"{name}.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,  # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            
            # 控制台日志
            console_handler = logging.StreamHandler()
            
            # 格式
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(formatter)
            console_handler.setFormatter(formatter)
            
            logger.addHandler(file_handler)
            logger.addHandler(console_handler)
        
        self._loggers[name] = logger
        return logger
    
    def get_logger(self, name: str) -> logging.Logger:
        """获取logger"""
        return self._setup_logger(name)


# 全局日志实例
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """便捷获取logger函数"""
    return logger_manager.get_logger(name)


def log_with_phone(phone: str, level: str, message: str, **kwargs):
    """带手机号的日志"""
    logger = get_logger(phone)
    log_method = getattr(logger, level.lower(), logger.info)
    
    extra_info = f" | {', '.join(f'{k}={v}' for k, v in kwargs.items())}" if kwargs else ""
    log_method(f"[{phone}] {message}{extra_info}")
