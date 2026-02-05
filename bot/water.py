"""
水群机器人 - 自动在群里发消息
"""
import asyncio
import random
import re
import time
from datetime import datetime
from typing import List, Dict, Any
from telethon import events
from bot.base import BotBase
from core.logger import get_logger


class WaterBot(BotBase):
    """水群机器人"""
    
    def __init__(self, phone: str):
        super().__init__(phone, "water")
        self.history: Dict[str, List[Dict]] = {}
        self.load_history()
    
    def load_history(self):
        """加载历史记录"""
        history_file = self._get_data_file("history", f"{self.phone}.json")
        if history_file.exists():
            import json
            try:
                mtime = datetime.fromtimestamp(history_file.stat().st_mtime)
                if mtime.date() < datetime.now().date():
                    self.history = {}
                else:
                    with open(history_file, 'r', encoding='utf-8') as f:
                        self.history = json.load(f)
            except:
                self.history = {}
    
    def save_history(self):
        """保存历史记录"""
        import json
        history_file = self._get_data_file("history", f"{self.phone}.json")
        with open(history_file + ".tmp", 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)
        history_file.replace(history_file.with_suffix(".tmp"))
    
    def _get_data_file(self, folder: str, filename: str):
        """获取数据文件路径"""
        from pathlib import Path
        data_dir = Path(__file__).parent.parent.parent / "data" / folder
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / filename
    
    def _register_handlers(self):
        """注册实时@监听"""
        @self.client.on(events.NewMessage(incoming=True, func=lambda e: e.mentioned))
        async def handle_mention(event):
            if not event.is_group:
                return
            await self._handle_mention(event)
    
    async def _handle_mention(self, event):
        """处理@提及"""
        chat = await event.get_chat()
        chat_id = str(chat.id)
        
        self.logger.info(f"⚡ 收到@提及: {chat_id}")
        await self._process_group(chat, 999, True)
    
    async def _run_loop(self):
        """主循环"""
        self.logger.info("💧 水群主循环启动")
        
        while self.running:
            try:
                # 检查休眠时间
                now_hour = datetime.now().hour
                sleep_start = self.bot_config.get('sleep_start', 0)
                sleep_end = self.bot_config.get('sleep_end', 8)
                
                in_sleep = (sleep_start > sleep_end and (now_hour >= sleep_start or now_hour < sleep_end)) or \
                          (sleep_start <= now_hour < sleep_end)
                
                if in_sleep:
                    self.logger.info(f"💤 休眠中 ({sleep_start}-{sleep_end})")
                    await asyncio.sleep(300)
                    continue
                
                # 获取目标群组
                target_groups = self.bot_config.get('target_groups', [])
                if not target_groups:
                    await asyncio.sleep(60)
                    continue
                
                # 扫描群组
                await self._scan_groups(target_groups)
                
                # 随机休息
                min_delay = self.bot_config.get('min_delay', 120)
                max_delay = self.bot_config.get('max_delay', 240)
                await asyncio.sleep(random.randint(min_delay, max_delay))
                
            except Exception as e:
                self.logger.error(f"Loop error: {e}")
                await asyncio.sleep(10)
    
    async def _scan_groups(self, groups: List[str]):
        """扫描群组"""
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
            
            # 过滤已达上限的群组
            if sent_count >= self.bot_config.get('messages_per_day', 21):
                continue
            
            # 低活跃度随机跳过
            if unread <= 80 and random.random() < 0.7:
                continue
            
            candidates.append((target_id, unread))
        
        if not candidates:
            self.logger.info("⏳ 本轮无目标群组")
            return
        
        # 加权选择
        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[:min(3, len(candidates))]
        
        self.logger.info(f"🎯 选择 {len(selected)}/{len(groups)} 个群组")
        
        for group_id, unread in selected:
            if not self.running:
                break
            try:
                entity = await self.client.get_entity(group_id)
                await self._process_group(entity, unread, False)
                
                # 群组间隔
                if group_id != selected[-1][0]:
                    group_min = self.bot_config.get('group_min', 40)
                    group_max = self.bot_config.get('group_max', 100)
                    await asyncio.sleep(random.randint(group_min, group_max))
            except Exception as e:
                self.logger.warning(f"处理群组失败 {group_id}: {e}")
    
    async def _process_group(self, group, unread_count: int, is_mention: bool):
        """处理单个群组"""
        group_id = str(group.id)
        
        # 检查今日上限
        if not is_mention and len(self.history.get(group_id, [])) >= \
           self.bot_config.get('messages_per_day', 21):
            return
        
        # 获取上下文
        context_count = self.bot_config.get('context_count', 5)
        messages = await self.client.get_messages(group, limit=context_count)
        
        # 构建上下文
        context_text = " ".join([m.text for m in messages if m.text])
        
        # 获取AI回复
        reply = await self._get_ai_reply(context_text)
        if not reply:
            return
        
        # 检查违禁词
        forbidden = self._check_forbidden(reply)
        if forbidden:
            self.logger.warning(f"🚫 违禁词拦截: {forbidden}")
            return
        
        # 发送消息
        await self.client.send_read_acknowledge(group)
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
        
        self.task_count += 1
        self.logger.info(f"✅ 发送成功: {reply}")
    
    async def _get_ai_reply(self, context: str) -> str:
        """获取AI回复"""
        try:
            from ai_utils import get_ai_raw_reply
            ai_config = {
                'ai_key': self.bot_config.get('ai_key', ''),
                'system_prompt': self.bot_config.get('system_prompt', ''),
                'forbidden_words': self.bot_config.get('forbidden_words', []),
                'ai_max_length': self.bot_config.get('ai_max_length', 20)
            }
            return await get_ai_raw_reply(context, ai_config)
        except Exception as e:
            self.logger.error(f"AI reply failed: {e}")
            return None
    
    def _check_forbidden(self, text: str) -> str:
        """检查违禁词"""
        keywords = self.bot_config.get('forbidden_words', [])
        for kw in keywords:
            if kw and kw in text:
                return kw
        return None
    
    @property
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        total = sum(len(v) for v in self.history.values())
        return {
            **super().status,
            "total_messages": total,
            "active_groups": len(self.history)
        }
