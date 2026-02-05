"""
Telegram Matrix Bot - 单进程异步架构
=====================================
核心设计：
- 所有账号共享一个 asyncio 事件循环
- 抽奖：手动触发补录，串行处理，完整日志
- 水群：跨账号随机轮询，负载均衡
"""
import asyncio
import json
import logging
import logging.handlers
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
import yaml

# ============ 配置管理 ============
class ConfigManager:
    """配置管理器"""
    
    def __init__(self):
        self._config: Dict = {}
        self._accounts: Dict[str, Dict] = {}
        self._load_main_config()
    
    def _load_main_config(self):
        """加载主配置"""
        config_path = Path(__file__).parent / "config.yaml"
        if config_path.exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f) or {}
    
    @property
    def app(self) -> Dict:
        return self._config.get('app', {})
    
    @property
    def account_defaults(self) -> Dict:
        return self._config.get('account_defaults', {})
    
    @property
    def giveaway(self) -> Dict:
        return self._config.get('giveaway', {})
    
    @property
    def water(self) -> Dict:
        return self._config.get('water', {})
    
    def load_account_config(self, phone: str) -> Dict:
        """加载账号配置"""
        if phone in self._accounts:
            return self._accounts[phone]
        
        config_file = Path(__file__).parent / "data" / "configs" / f"{phone}.yaml"
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                account_cfg = yaml.safe_load(f) or {}
        else:
            account_cfg = {}
        
        # 合并默认配置
        merged = self.account_defaults.copy()
        merged.update(account_cfg)
        self._accounts[phone] = merged
        return merged
    
    def save_account_config(self, phone: str, config: Dict):
        """保存账号配置"""
        self._accounts[phone] = config
        config_dir = Path(__file__).parent / "data" / "configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / f"{phone}.yaml"
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    
    def get_all_accounts(self) -> List[str]:
        """获取所有账号"""
        config_dir = Path(__file__).parent / "data" / "configs"
        if not config_dir.exists():
            return []
        return [f.stem for f in config_dir.glob('*.yaml')]


# ============ 日志管理 ============
class LogManager:
    """日志管理器 - 支持实时推送"""
    
    def __init__(self):
        self._loggers: Dict[str, logging.Logger] = {}
        self._log_dir = Path(__file__).parent / "data" / "logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._socketio: Optional[SocketIO] = None
    
    def set_socketio(self, socketio: SocketIO):
        self._socketio = socketio
    
    def get_logger(self, name: str, phone: str = None) -> logging.Logger:
        """获取logger"""
        logger_id = f"{name}_{phone}" if phone else name
        if logger_id in self._loggers:
            return self._loggers[logger_id]
        
        logger = logging.getLogger(logger_id)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        
        # 文件Handler
        log_file = self._log_dir / f"{logger_id}.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        
        # 格式
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
        self._loggers[logger_id] = logger
        return logger
    
    def log(self, phone: str, level: str, message: str, task_type: str = None):
        """记录日志并推送"""
        logger = self.get_logger("bot", phone)
        log_msg = f"[{phone}] {message}" if phone else message
        log_method = getattr(logger, level.lower(), logger.info)
        log_msg_full = f"【{task_type}】{log_msg}" if task_type else log_msg
        log_method(log_msg_full)
        
        # 推送WebSocket
        if self._socketio:
            self._socketio.emit('log_update', {
                'phone': phone or 'system',
                'level': level,
                'message': log_msg_full,
                'timestamp': datetime.now().isoformat()
            })


# ============ 全局实例 ============
config = ConfigManager()
log_manager = LogManager()


# ============ 抽奖引擎 ============
class GiveawayEngine:
    """
    抽奖引擎 - 手动触发补录
    特点：
    - 手动触发，不自动运行
    - 选择账号 + 时间范围（例如1天前、2天前）
    - 串行处理每个抽奖任务
    - 完整详细日志输出
    """
    
    SUCCESS_KEYWORDS = [
        "成功参加", "报名成功", "您已成功", "成功参与", "成功增加",
        "中奖率", "已参加", "参与成功", "获得奖票", "你已参加",
        "已经参加", "重复参加", "祝福仪式", "请勿重复点击"
    ]
    
    ENDED_KEYWORDS = [
        "活动已结束", "链接已失效", "验证失败", "通宝不足",
        "积分不足", "余额不足", "暂不能参加", "不存在", "已过期"
    ]
    
    def __init__(self, phone: str):
        self.phone = phone
        self.config = config.load_account_config(phone)
        self.logger = log_manager.get_logger("giveaway", phone)
        self.client = None
        self.running = False
        self.task_count = 0
        self.consecutive_failures = 0
        self.active_context: Optional[Dict] = None
        self.task_done = asyncio.Event()
    
    async def get_client(self):
        """获取Telegram客户端"""
        from telethon import TelegramClient
        from telethon.sessions import SQLiteSession
        
        session_file = Path(__file__).parent / "data" / "sessions" / f"{self.phone}.session"
        api_id = self.config.get('api_id')
        api_hash = self.config.get('api_hash')
        
        proxy = None
        proxy_str = self.config.get('proxy')
        if proxy_str and ':' in proxy_str:
            parts = proxy_str.split(':')
            if len(parts) >= 4:
                import socks
                proxy = (socks.SOCKS5, parts[0], int(parts[1]), True, parts[2], parts[3])
        
        self.client = TelegramClient(
            str(session_file), api_id, api_hash, proxy=proxy
        )
        await self.client.connect()
        return self.client
    
    async def _ensure_authorized(self):
        """确保已授权"""
        if not await self.client.is_user_authorized():
            raise Exception(f"账号 {self.phone} 未授权，请先登录")
    
    async def run_loop(self) -> Dict[str, Any]:
        """
        抽奖循环任务 - 持续监控抽奖频道
        """
        self.running = True
        self.task_count = 0
        self.consecutive_failures = 0
        
        log_manager.log(self.phone, 'INFO', f"🎯 启动抽奖监控", "抽奖")
        
        try:
            await self.get_client()
            await self._ensure_authorized()
            
            monitor_channels = self.config.get('monitor_channel', [])
            if isinstance(monitor_channels, str):
                monitor_channels = [monitor_channels]
            
            if not monitor_channels:
                log_manager.log(self.phone, 'WARNING', '未配置监控频道', '抽奖')
                return {"status": "no_channels"}
            
            allow_keywords = self.config.get('allow_keywords', [])
            
            while self.running and self.consecutive_failures < 10:
                try:
                    for channel in monitor_channels:
                        if not self.running:
                            break
                        
                        log_manager.log(self.phone, 'INFO', f'🔍 扫描频道: {channel}', '抽奖')
                        entity = await self.client.get_entity(channel)
                        
                        async for msg in self.client.iter_messages(entity, limit=50):
                            if not self.running:
                                break
                            
                            if msg.text and any(kw in msg.text for kw in allow_keywords):
                                if msg.id not in (self.active_context or {}):
                                    log_manager.log(self.phone, 'INFO', f'📝 发现抽奖: {msg.text[:50]}...', '抽奖')
                                    await self._process_giveaway_message(msg)
                                    self.task_count += 1
                                    self.consecutive_failures = 0
                            
                            await asyncio.sleep(2)
                    
                    await asyncio.sleep(60)
                    
                except Exception as e:
                    self.consecutive_failures += 1
                    log_manager.log(self.phone, 'ERROR', f'抽奖循环错误: {e}', '抽奖')
                    await asyncio.sleep(30)
            
            log_manager.log(self.phone, 'INFO', f'✅ 抽奖监控结束，共处理 {self.task_count} 个任务', '抽奖')
            return {"status": "completed", "tasks": self.task_count}
            
        except Exception as e:
            log_manager.log(self.phone, 'ERROR', f'抽奖启动失败: {e}', '抽奖')
            return {"status": "error", "message": str(e)}
        finally:
            self.running = False
            if self.client:
                await self.client.disconnect()
    
    async def run_backfill(self, days: int = 1) -> Dict[str, Any]:
        """
        手动触发补录抽奖
        
        Args:
            days: 回溯天数，默认1天
        
        Returns:
            处理结果统计
        """
        self.running = True
        self.task_count = 0
        self.consecutive_failures = 0
        
        log_manager.log(self.phone, 'INFO', f"🚀 开始抽奖补录，回溯{days}天", "抽奖")
        
        try:
            await self.get_client()
            await self._ensure_authorized()
            
            # 获取监控频道
            monitor_channels = self.config.get('monitor_channel', [])
            if isinstance(monitor_channels, str):
                monitor_channels = [monitor_channels]
            
            if not monitor_channels:
                log_manager.log(self.phone, 'WARNING', "未配置监控频道", "抽奖")
                return {'success': 0, 'failed': 0, 'total': 0}
            
            # 计算时间范围
            now = datetime.now(timezone.utc)
            start_time = now - timedelta(days=days)
            
            log_manager.log(self.phone, 'INFO', 
                f"📅 时间范围: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')}", 
                "抽奖")
            
            total_found = 0
            total_success = 0
            
            # 串行处理每个频道
            for channel in monitor_channels:
                if not self.running:
                    break
                
                try:
                    success, found = await self._process_channel(channel, start_time)
                    total_success += success
                    total_found += found
                    log_manager.log(self.phone, 'INFO', 
                        f"📊 频道 {channel}: 成功{success}, 发现{found}", "抽奖")
                except Exception as e:
                    log_manager.log(self.phone, 'ERROR', f"处理频道失败: {channel} - {e}", "抽奖")
                
                # 频道间随机延迟
                await asyncio.sleep(random.randint(5, 10))
            
            log_manager.log(self.phone, 'INFO', 
                f"✅ 补录完成: 成功{total_success}, 总计{total_found}", "抽奖")
            
            return {'success': total_success, 'failed': total_found - total_success, 'total': total_found}
            
        except Exception as e:
            log_manager.log(self.phone, 'ERROR', f"补录失败: {e}", "抽奖")
            raise
        finally:
            self.running = False
            if self.client:
                await self.client.disconnect()
    
    async def _process_channel(self, channel: str, start_time: datetime) -> tuple:
        """处理单个频道的消息"""
        entity = await self.client.get_entity(channel)
        
        # 获取时间范围内的消息
        messages = []
        async for msg in self.client.iter_messages(entity, offset_date=start_time, reverse=True):
            if msg.text and ('抽奖' in msg.text or 'giveaway' in msg.text.lower()):
                messages.append(msg)
        
        if not messages:
            return 0, 0
        
        found = len(messages)
        success = 0
        
        for msg in messages:
            if not self.running:
                break
            
            # 检查是否有报名按钮
            if msg.reply_markup:
                for row in msg.reply_markup.rows:
                    for btn in row.buttons:
                        if hasattr(btn, 'text') and '参加' in btn.text:
                            if hasattr(btn, 'url') and 'start=' in btn.url:
                                match = re.search(r't\.me/(\w+)\?start=([\w-]+)', btn.url)
                                if match:
                                    bot_name, payload = match.group(1), match.group(2)
                                    log_manager.log(self.phone, 'INFO', 
                                        f"发现抽奖: @{bot_name}", "抽奖")
                                    
                                    # 尝试参与
                                    result = await self._participate(bot_name, payload, msg.id)
                                    if result:
                                        success += 1
                                    await asyncio.sleep(random.randint(10, 15))
            
            await asyncio.sleep(random.randint(3, 5))
        
        return success, found
    
    async def _participate(self, bot_name: str, payload: str, origin_id: int) -> bool:
        """参与单个抽奖"""
        self.active_context = {
            'bot': bot_name,
            'payload': payload,
            'origin_id': origin_id,
            'start_time': time.time()
        }
        self.task_done.clear()
        
        try:
            # 发送/start
            log_manager.log(self.phone, 'INFO', f"发送 /start @{bot_name}", "抽奖")
            await self.client.send_message(bot_name, f"/start {payload}")
            
            # 等待回复
            try:
                await asyncio.wait_for(self.task_done.wait(), timeout=120)
                log_manager.log(self.phone, 'INFO', f"✅ 抽奖成功: @{bot_name}", "抽奖")
                self.task_count += 1
                self.consecutive_failures = 0
                return True
            except asyncio.TimeoutError:
                log_manager.log(self.phone, 'WARNING', f"⏱️ 抽奖超时: @{bot_name}", "抽奖")
                self.consecutive_failures += 1
                return False
            
        except Exception as e:
            log_manager.log(self.phone, 'ERROR', f"参与失败: @{bot_name} - {e}", "抽奖")
            self.consecutive_failures += 1
            return False
        finally:
            self.active_context = None
    
    async def _handle_response(self, event):
        """处理机器人回复"""
        if not self.active_context:
            return
        
        text = event.text or ""
        bot_id = event.chat_id
        
        log_manager.log(self.phone, 'INFO', f"📨 回复: {text[:50]}...", "抽奖")
        
        # 成功关键词
        if any(kw in text for kw in self.SUCCESS_KEYWORDS):
            self.consecutive_failures = 0
            log_manager.log(self.phone, 'INFO', "🏆 报名成功！", "抽奖")
            self.task_done.set()
            return
        
        # 失败关键词
        if any(kw in text for kw in self.ENDED_KEYWORDS):
            self.consecutive_failures += 1
            log_manager.log(self.phone, 'WARNING', "❌ 活动已结束或失败", "抽奖")
            self.task_done.set()
            return
        
        # 数学验证码
        math_match = re.search(r'(\d+)\s*([\+\-\*\/])\s*(\d+)', text)
        if math_match:
            n1, op, n2 = int(math_match.group(1)), math_match.group(2), int(math_match.group(3))
            if not (n1 > 2000 and n2 < 13 and op == '-'):  # 排除年份计算
                result = eval(f"{n1}{op}{n2}")
                log_manager.log(self.phone, 'INFO', f"🧠 解出数学题: {n1}{op}{n2}={result}", "抽奖")
                await asyncio.sleep(random.randint(8, 12))
                await self.client.send_message(bot_id, str(result))
    
    def stop(self):
        """停止运行"""
        self.running = False


# ============ 水群引擎 ============
class WaterEngine:
    """
    水群引擎 - 跨账号随机轮询
    特点：
    - 账号池随机选择（负载均衡）
    - 保留权重和冷却逻辑
    - 小循环结束后切换账号
    """
    
    def __init__(self, phone: str):
        self.phone = phone
        self.config = config.load_account_config(phone)
        self.logger = log_manager.get_logger("water", phone)
        self.client = None
        self.running = False
        self.task_count = 0
        self.history: Dict[str, List] = {}
        self.load_history()
    
    def load_history(self):
        """加载今日历史"""
        history_file = Path(__file__).parent / "data" / "history" / f"{self.phone}.json"
        if history_file.exists():
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                mtime = datetime.fromtimestamp(history_file.stat().st_mtime)
                if mtime.date() >= datetime.now().date():
                    self.history = data
            except:
                self.history = {}
    
    def save_history(self):
        """保存历史"""
        history_file = Path(__file__).parent / "data" / "history" / f"{self.phone}.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
    
    async def get_client(self):
        """获取Telegram客户端"""
        from telethon import TelegramClient
        session_file = Path(__file__).parent / "data" / "sessions" / f"{self.phone}.session"
        api_id = self.config.get('api_id')
        api_hash = self.config.get('api_hash')
        
        proxy = None
        proxy_str = self.config.get('proxy')
        if proxy_str and ':' in proxy_str:
            parts = proxy_str.split(':')
            if len(parts) >= 4:
                import socks
                proxy = (socks.SOCKS5, parts[0], int(parts[1]), True, parts[2], parts[3])
        
        self.client = TelegramClient(
            str(session_file), api_id, api_hash, proxy=proxy
        )
        await self.client.connect()
        return self.client
    
    async def _ensure_authorized(self):
        """确保已授权"""
        if not await self.client.is_user_authorized():
            raise Exception(f"账号 {self.phone} 未授权")
    
    async def run_cycle(self) -> int:
        """
        运行一个小循环
        Returns:
            发送的消息数量
        """
        if self.running:
            return 0
        
        self.running = True
        messages_sent = 0
        
        try:
            await self.get_client()
            await self._ensure_authorized()
            
            log_manager.log(self.phone, 'INFO', "💧 开始水群小循环", "水群")
            
            # 获取目标群组
            target_groups = self.config.get('target_groups', [])
            if not target_groups:
                log_manager.log(self.phone, 'WARNING', "未配置目标群组", "水群")
                return 0
            
            # 检查休眠时间
            now_hour = datetime.now().hour
            sleep_start = self.config.get('sleep_start', 0)
            sleep_end = self.config.get('sleep_end', 8)
            
            in_sleep = (sleep_start > sleep_end and (now_hour >= sleep_start or now_hour < sleep_end)) or \
                      (sleep_start <= now_hour < sleep_end)
            
            if in_sleep:
                log_manager.log(self.phone, 'INFO', f"💤 休眠中 ({sleep_start}-{sleep_end})", "水群")
                return 0
            
            # 获取可用的群组
            candidates = await self._get_candidates(target_groups)
            if not candidates:
                log_manager.log(self.phone, 'INFO', "⏳ 本轮无可用群组", "水群")
                return 0
            
            # 加权选择群组
            selected = self._weighted_select(candidates)
            log_manager.log(self.phone, 'INFO', f"🎯 选择 {len(selected)} 个群组", "水群")
            
            # 串行发送
            for group_id, unread in selected:
                if not self.running:
                    break
                
                try:
                    entity = await self.client.get_entity(group_id)
                    sent = await self._send_message(entity)
                    if sent:
                        messages_sent += 1
                        self.task_count += 1
                    
                    # 群组间隔
                    if group_id != selected[-1][0]:
                        group_min = self.config.get('group_min', 40)
                        group_max = self.config.get('group_max', 100)
                        await asyncio.sleep(random.randint(group_min, group_max))
                        
                except Exception as e:
                    log_manager.log(self.phone, 'WARNING', f"处理群组失败: {e}", "水群")
            
            log_manager.log(self.phone, 'INFO', f"✅ 小循环完成，发送 {messages_sent} 条消息", "水群")
            return messages_sent
            
        except Exception as e:
            log_manager.log(self.phone, 'ERROR', f"水群失败: {e}", "水群")
            return messages_sent
        finally:
            self.running = False
            if self.client:
                await self.client.disconnect()
    
    async def _get_candidates(self, groups: List) -> List[tuple]:
        """获取候选群组"""
        dialogs = await self.client.get_dialogs(limit=None)
        unread_map = {str(d.id): d.unread_count for d in dialogs}
        
        candidates = []
        for group in groups:
            g_str = str(group).strip()
            if g_str.startswith('-') and g_str[1:].isdigit():
                target_id = int(g_str)
            else:
                target_id = g_str
            
            unread = unread_map.get(str(target_id), 0)
            sent_count = len(self.history.get(str(target_id), []))
            
            # 过滤已达上限
            max_per_day = self.config.get('messages_per_day', 21)
            if sent_count >= max_per_day:
                continue
            
            # 低活跃度随机跳过
            if unread <= 80 and random.random() < 0.7:
                continue
            
            candidates.append((target_id, unread))
        
        return candidates
    
    def _weighted_select(self, candidates: List[tuple]) -> List[tuple]:
        """加权选择群组"""
        if not candidates:
            return []
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[:min(3, len(candidates))]
        return selected
    
    async def _send_message(self, group) -> bool:
        """发送消息到群组"""
        group_id = str(group.id)
        
        # 检查今日上限
        max_per_day = self.config.get('messages_per_day', 21)
        if len(self.history.get(group_id, [])) >= max_per_day:
            return False
        
        # 获取上下文
        context_count = self.config.get('context_count', 5)
        messages = await self.client.get_messages(group, limit=context_count)
        context_text = " ".join([m.text for m in messages if m.text])
        
        # 获取AI回复
        reply = await self._get_ai_reply(context_text)
        if not reply:
            return False
        
        # 检查违禁词
        forbidden = self._check_forbidden(reply)
        if forbidden:
            log_manager.log(self.phone, 'WARNING', f"🚫 违禁词拦截: {forbidden}", "水群")
            return False
        
        # 发送消息
        async with self.client.action(group, 'typing'):
            await asyncio.sleep(random.randint(2, 4))
            await self.client.send_message(group, reply)
        
        # 记录
        if group_id not in self.history:
            self.history[group_id] = []
        self.history[group_id].append({
            "text": reply,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        self.save_history()
        
        log_manager.log(self.phone, 'INFO', f"✅ 发送: {reply[:30]}...", "水群")
        return True
    
    async def _get_ai_reply(self, context: str) -> str:
        """获取AI回复"""
        try:
            from bot.ai_utils import get_ai_raw_reply
            ai_config = {
                'ai_key': self.config.get('ai_key', ''),
                'system_prompt': self.config.get('system_prompt', ''),
                'forbidden_words': self.config.get('forbidden_words', []),
                'ai_max_length': self.config.get('ai_max_length', 20)
            }
            return await get_ai_raw_reply(context, ai_config)
        except Exception as e:
            log_manager.log(self.phone, 'ERROR', f"AI回复失败: {e}", "水群")
            return None
    
    def _check_forbidden(self, text: str) -> str:
        """检查违禁词"""
        keywords = self.config.get('forbidden_words', [])
        for kw in keywords:
            if kw and kw in text:
                return kw
        return None
    
    def stop(self):
        """停止运行"""
        self.running = False


# ============ 账号池管理器 ============
class AccountPool:
    """账号池 - 支持跨账号随机轮询"""
    
    def __init__(self):
        self._phones: List[str] = []
        self._weights: Dict[str, int] = {}  # 权重
        self._last_used: Dict[str, float] = {}  # 最后使用时间
        self._lock = asyncio.Lock()
    
    def refresh_accounts(self):
        """刷新账号列表"""
        self._phones = config.get_all_accounts()
        for phone in self._phones:
            if phone not in self._weights:
                self._weights[phone] = 1  # 默认权重
        log_manager.log('system', 'INFO', f"加载 {len(self._phones)} 个账号", "系统")
    
    def get_phone(self) -> Optional[str]:
        """获取下一个账号（加权随机 + 冷却）"""
        if not self._phones:
            return None
        
        now = time.time()
        min_cooldown = 30  # 最小冷却时间
        
        # 过滤冷却中的账号
        available = []
        for phone in self._phones:
            last = self._last_used.get(phone, 0)
            if now - last >= min_cooldown:
                available.append(phone)
        
        if not available:
            # 所有账号都在冷却中，返回最早的
            available = self._phones
        
        # 加权随机选择
        weights = [self._weights.get(p, 1) for p in available]
        total = sum(weights)
        if total == 0:
            return available[0]
        
        r = random.uniform(0, total)
        cumsum = 0
        for i, phone in enumerate(available):
            cumsum += weights[i]
            if r <= cumsum:
                self._last_used[phone] = now
                return phone
        
        return available[0]
    
    def set_weight(self, phone: str, weight: int):
        """设置账号权重"""
        self._weights[phone] = max(1, weight)


# ============ 单进程任务调度器 ============
class TaskScheduler:
    """单进程异步任务调度器"""
    
    def __init__(self):
        self._running = False
        self._water_tasks: Set[str] = set()  # 运行中的水群账号
        self._account_pool = AccountPool()
        self._executor = ThreadPoolExecutor(max_workers=10)
        self._water_cycle_lock = asyncio.Lock()
    
    async def start_water_loop(self, phone: str):
        """启动水群循环（跨账号轮询）"""
        if phone in self._water_tasks:
            log_manager.log(phone, 'WARNING', "水群已在运行", "水群")
            return
        
        self._water_tasks.add(phone)
        log_manager.log(phone, 'INFO', "💧 启动水群循环", "水群")
        
        while phone in self._water_tasks:
            engine = WaterEngine(phone)
            try:
                await engine.run_cycle()
            except Exception as e:
                log_manager.log(phone, 'ERROR', f"水群异常: {e}", "水群")
            
            # 小循环结束后，切换账号
            await asyncio.sleep(2)
            next_phone = self._account_pool.get_phone()
            
            if next_phone != phone and next_phone in self._water_tasks:
                log_manager.log('system', 'INFO', f"🔄 切换账号: {phone} -> {next_phone}", "水群")
                phone = next_phone
            
            # 随机休息后再继续
            await asyncio.sleep(random.randint(10, 20))
        
        self._water_tasks.discard(phone)
    
    def stop_water(self, phone: str):
        """停止水群"""
        self._water_tasks.discard(phone)
        log_manager.log(phone, 'INFO', "⏹ 停止水群", "水群")
    
    async def run_giveaway_backfill(self, phone: str, days: int) -> Dict:
        """运行抽奖补录"""
        engine = GiveawayEngine(phone)
        return await engine.run_backfill(days)
