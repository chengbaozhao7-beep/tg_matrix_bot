"""
配置管理器 - 统一管理应用配置
"""
import os
import yaml
from typing import Any, Dict, Optional
from pathlib import Path


class ConfigManager:
    """配置管理器 - 支持多账号配置"""
    
    _instance: Optional['ConfigManager'] = None
    _config: Dict[str, Any] = {}
    _accounts: Dict[str, Dict[str, Any]] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load_config()
        return cls._instance
    
    def _load_config(self):
        """加载主配置文件"""
        config_path = Path(__file__).parent.parent / "config.yaml"
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
    
    @property
    def app(self) -> Dict[str, Any]:
        return self._config.get('app', {})
    
    @property
    def account_defaults(self) -> Dict[str, Any]:
        return self._config.get('account_defaults', {})
    
    @property
    def giveaway(self) -> Dict[str, Any]:
        return self._config.get('giveaway', {})
    
    @property
    def logging_config(self) -> Dict[str, Any]:
        return self._config.get('logging', {})
    
    def get_account_config(self, phone: str) -> Dict[str, Any]:
        """获取账号配置（合并默认配置）"""
        if phone not in self._accounts:
            self._accounts[phone] = self.account_defaults.copy()
        return self._accounts[phone]
    
    def update_account_config(self, phone: str, updates: Dict[str, Any]):
        """更新账号配置"""
        if phone not in self._accounts:
            self._accounts[phone] = self.account_defaults.copy()
        self._accounts[phone].update(updates)
        self._save_account_config(phone)
    
    def _save_account_config(self, phone: str):
        """保存账号配置到文件"""
        config_dir = Path(__file__).parent.parent / "data" / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / f"{phone}.yaml"
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(self._accounts[phone], f, default_flow_style=False, allow_unicode=True)
    
    def load_account_config(self, phone: str) -> Dict[str, Any]:
        """从文件加载账号配置"""
        config_file = Path(__file__).parent.parent / "data" / "configs" / f"{phone}.yaml"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                self._accounts[phone] = yaml.safe_load(f) or self.account_defaults.copy()
        return self.get_account_config(phone)
    
    def get_proxy(self, phone: str) -> Optional[tuple]:
        """获取代理配置"""
        config = self.get_account_config(phone)
        proxy_str = config.get('proxy')
        if proxy_str and ':' in proxy_str:
            parts = proxy_str.split(':')
            if len(parts) >= 4:
                import socks
                return (
                    socks.SOCKS5,
                    parts[0],
                    int(parts[1]),
                    True,
                    parts[2],
                    parts[3]
                )
        return None


# 全局配置实例
config = ConfigManager()
