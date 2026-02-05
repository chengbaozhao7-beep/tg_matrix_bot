"""
抽奖机器人 - 自动参与Telegram抽奖
"""
import asyncio
import json
import random
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from telethon import events, functions, types
from bot.base import BotBase
from core.logger import get_logger


class GiveawayBot(BotBase):
    """抽奖机器人"""
    
    # 抽奖按钮关键词（扩大检测范围）
    GIVEAWAY_BTN_KEYWORDS = [
        "参加抽奖", "立即参加", "点我参与", "参与抽奖", "我要参加",
        "马上参加", "立即参与", "参与活动", "参加活动", "领取福利",
        "获取资格", "登记参与", "确认参加", "立即领取"
    ]
    
    # 加入群组按钮关键词
    JOIN_BTN_KEYWORDS = [
        "加入", "Joined", "订阅", "加入群组", "加入频道", "点击加入",
        "join", "Join", "加入 telegram"
    ]
    
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
        super().__init__(phone, "giveaway")
        self.queue: asyncio.Queue = asyncio.Queue()
        self.current_task: Optional[Dict] = None
        self.task_done = asyncio.Event()
        self.context_file = self._get_data_file("giveaway_context.json")
        self.joined_db = self._get_data_file("joined_channels.json")
        self.consecutive_failures = 0
        
        # 补录模式
        self.backfill_mode = False
        self.backfill_days = 1
        
        # 加载持久化上下文
        self.active_context = self._load_context()
    
    def _get_data_file(self, filename: str):
        """获取数据文件路径"""
        data_dir = Path(__file__).parent.parent.parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / filename
    
    def _load_context(self) -> Optional[Dict]:
        """加载上下文"""
        if not self.context_file.exists():
            return None
        try:
            with open(self.context_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if time.time() - data.get('start_time', 0) > 600:
                return None
            return data
        except:
            return None
    
    def _save_context(self, ctx: Optional[Dict]):
        """保存上下文"""
        if ctx:
            with open(self.context_file, 'w', encoding='utf-8') as f:
                json.dump(ctx, f, ensure_ascii=False)
        elif self.context_file.exists():
            self.context_file.unlink()
    
    def _load_joined_db(self) -> Dict:
        """加载已加入频道数据库"""
        if not self.joined_db.exists():
            return {}
        try:
            with open(self.joined_db, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    
    def _save_joined_db(self, db: Dict):
        """保存已加入频道数据库"""
        with open(self.joined_db, 'w', encoding='utf-8') as f:
            json.dump(db, f, ensure_ascii=False, indent=2)
    
    async def _record_join(self, entity_id: int):
        """记录加入"""
        db = self._load_joined_db()
        sid = str(entity_id)
        if sid not in db:
            db[sid] = time.time()
            self._save_joined_db(db)
    
    def _register_handlers(self):
        """注册消息处理器"""
        monitor_channels = self.bot_config.get('monitor_channel', [])
        if isinstance(monitor_channels, str):
            monitor_channels = [monitor_channels]
        
        @self.client.on(events.NewMessage(chats=monitor_channels))
        async def handle_new_message(event):
            if not event.message:
                return
            await self._handle_giveaway_message(event.message)
        
        # 注册机器人回复监听
        self.client.add_event_handler(
            self._handle_bot_response,
            events.NewMessage(incoming=True)
        )
    
    async def _handle_giveaway_message(self, message):
        """处理抽奖消息"""
        # 检查是否包含抽奖关键词
        text = message.text or ""
        allow_keywords = self.bot_config.get('allow_keywords', [])
        block_keywords = self.bot_config.get('block_keywords', [])
        
        # 关键词过滤
        if block_keywords and any(kw in text for kw in block_keywords):
            self.logger.info(f"🚫 命中黑名单，跳过")
            return
        
        if allow_keywords and not any(kw in text for kw in allow_keywords):
            return
        
        # 检查所有可能的抽奖按钮
        if message.reply_markup:
            join_urls = []  # 存储需要加入的频道链接
            giveaway_task = None
            
            for row in message.reply_markup.rows:
                for btn in row.buttons:
                    if not hasattr(btn, 'text') or not hasattr(btn, 'url'):
                        continue
                    
                    btn_text = btn.text
                    btn_url = btn.url
                    
                    # 检测加入群组按钮
                    if any(kw in btn_text for kw in self.JOIN_BTN_KEYWORDS):
                        if btn_url and 'joinchat/' in btn_url:
                            join_urls.append((btn_text, btn_url))
                    
                    # 检测参加抽奖按钮
                    if any(kw in btn_text for kw in self.GIVEAWAY_BTN_KEYWORDS):
                        if 'start=' in btn_url:
                            match = re.search(r't\.me/(\w+)\?start=([\w-]+)', btn_url)
                            if match:
                                bot_name, payload = match.group(1), match.group(2)
                                giveaway_task = (bot_name, payload, message.id)
                                chat_title = message.chat.title if message.chat else "Unknown"
                                self.logger.info(f"🎯 【{chat_title}】发现抽奖: @{bot_name} | 按钮: {btn_text}")
            
            # 如果有加入按钮，先加入群组
            if join_urls:
                for btn_text, join_url in join_urls:
                    try:
                        chat_title = message.chat.title if message.chat else "Unknown"
                        self.logger.info(f"🔗 【{chat_title}】准备加入群组: {btn_text}")
                        # 加入频道
                        if 'joinchat/' in join_url:
                            hash_match = re.search(r'joinchat/([a-zA-Z0-9_-]+)', join_url)
                            if hash_match:
                                await self.client(functions.messages.ImportChatInviteRequest(hash_match.group(1)))
                                self.logger.info(f"✅ 【{chat_title}】成功加入群组: {btn_text}")
                                await asyncio.sleep(random.randint(3, 5))
                    except Exception as e:
                        self.logger.warning(f"⚠️ 【{chat_title}】加入失败: {btn_text} | 错误: {e}")
            
            # 入队抽奖任务
            if giveaway_task:
                bot_name, payload, origin_id = giveaway_task
                await self._queue_task(bot_name, payload, origin_id, message.chat.title if message.chat else "Unknown")
                return  # 找到一个就入队，避免重复
    
    async def _queue_task(self, bot_name: str, payload: str, origin_id: int, chat_title: str = "Unknown"):
        """将任务加入队列"""
        await self.queue.put({
            "bot": bot_name,
            "payload": payload,
            "origin_id": origin_id,
            "chat_title": chat_title
        })
        self.logger.info(f"📥 【{chat_title}】任务入队: @{bot_name}")
    
    async def _run_backfill(self):
        """补录扫描 - 扫描历史消息"""
        try:
            monitor_channels = self.bot_config.get('monitor_channel', [])
            if isinstance(monitor_channels, str):
                monitor_channels = [monitor_channels]
            
            # 计算时间范围
            cutoff_time = datetime.now(timezone.utc) - timedelta(days=self.backfill_days)
            self.logger.info(f"📅 回溯时间点: {cutoff_time}")
            
            for channel in monitor_channels:
                try:
                    self.logger.info(f"🔍 扫描频道: {channel}")
                    entity = await self.client.get_entity(channel)
                    
                    # 扫描消息
                    task_count = 0
                    async for msg in self.client.iter_messages(entity, limit=100):
                        if msg.date < cutoff_time:
                            self.logger.info(f"⏹️ 到达时间边界，停止扫描")
                            break
                        
                        # 检查是否是抽奖消息
                        if msg.text:
                            allow_keywords = self.bot_config.get('allow_keywords', [])
                            if allow_keywords and any(kw in msg.text for kw in allow_keywords):
                                self.logger.info(f"📝 发现抽奖消息: {msg.text[:50]}...")
                                await self._handle_giveaway_message(msg)
                                task_count += 1
                        
                        await asyncio.sleep(0.5)  # 避免请求过快
                    
                    self.logger.info(f"✅ 频道 {channel} 扫描完成，发现 {task_count} 个任务")
                    
                except Exception as e:
                    self.logger.error(f"❌ 扫描频道 {channel} 失败: {e}")
            
            self.logger.info(f"🎯 补录扫描全部完成")
            
        except Exception as e:
            self.logger.error(f"💥 补录过程出错: {e}")
    
    async def _run_loop(self):
        """主循环 - 处理队列"""
        self.logger.info("🎁 抽奖主循环启动")
        
        if self.backfill_mode:
            # 补录模式：先扫描历史消息
            self.logger.info(f"🔍 开始补录扫描 (回溯{self.backfill_days}天)")
            await self._run_backfill()
            self.logger.info(f"📋 扫描完成，发现 {self.queue.qsize()} 个待处理任务")
            
            if self.queue.qsize() > 0:
                self.logger.info("🔄 补录完成，继续处理队列任务...")
                self.backfill_mode = False  # 切换为普通模式
            else:
                self.logger.info("🏁 补录完成，无待处理任务，停止任务")
                self.running = False
                return
        
        # 直接在主循环中处理队列
        while self.running:
            # 处理队列任务
            try:
                if not self.queue.empty():
                    task = await self.queue.get()
                    await self._process_task(task)
                else:
                    await asyncio.sleep(2)
            except Exception as e:
                self.logger.error(f"❌ 处理队列出错: {e}")
                await asyncio.sleep(5)
    
    async def _process_task(self, task: Dict):
        """处理单个任务"""
        bot_name = task['bot']
        payload = task['payload']
        chat_title = task.get('chat_title', 'Unknown')
        
        self.logger.info(f"▶️ 【{chat_title}】开始处理抽奖: @{bot_name}")
        
        # 设置上下文
        try:
            entity = await self.client.get_entity(bot_name)
            bot_id = entity.id
        except:
            bot_id = 0
        
        self.active_context = {
            "bot": bot_name,
            "bot_id": bot_id,
            "payload": payload,
            "start_time": time.time()
        }
        self._save_context(self.active_context)
        self.task_done.clear()
        
        # 发送/start
        try:
            await self.client.send_message(bot_name, f"/start {payload}")
        except Exception as e:
            self.logger.error(f"发送start失败: {e}")
            self._save_context(None)
            return
        
        # 等待完成信号
        try:
            await asyncio.wait_for(self.task_done.wait(), timeout=120)
            self.logger.info(f"✅ 任务完成: @{bot_name}")
            self.task_count += 1
            self.consecutive_failures = 0
        except asyncio.TimeoutError:
            self.logger.warning(f"⏱️ 任务超时: @{bot_name}")
            self.consecutive_failures += 1
        
        # 清理上下文
        self._save_context(None)
        await asyncio.sleep(random.randint(8, 12))
    
    async def _handle_bot_response(self, event):
        """处理机器人回复"""
        if not self.active_context:
            return
        
        msg_bot_id = event.chat_id
        ctx_bot_id = self.active_context.get('bot_id', 0)
        
        # 验证来源
        if ctx_bot_id != 0 and msg_bot_id != ctx_bot_id:
            return
        
        text = event.text or ""
        self.logger.info(f"📨 收到回复: {text[:80]}...")
        
        # 成功关键词
        if any(kw in text for kw in self.SUCCESS_KEYWORDS):
            self.consecutive_failures = 0
            self.logger.info(f"🏆 报名成功！")
            self.task_done.set()
            return
        
        # 失败/结束关键词
        if any(kw in text for kw in self.ENDED_KEYWORDS):
            self.consecutive_failures += 1
            if self.consecutive_failures >= 6:
                self.logger.critical(f"⛔ 连续失败6次，停止运行")
                self.running = False
            self.logger.warning(f"❌ 任务失败")
            self.task_done.set()
            return
        
        # 处理加入群组按钮和参加抽奖按钮
        if event.reply_markup:
            # 首先尝试加入所有频道
            for row_idx, row in enumerate(event.reply_markup.rows):
                for btn in row.buttons:
                    if not hasattr(btn, 'text'):
                        continue
                    
                    btn_text = btn.text
                    
                    # 检测加入群组按钮
                    if any(kw in btn_text for kw in self.JOIN_BTN_KEYWORDS):
                        self.logger.info(f"🔗 检测到加入按钮: {btn_text}")
                        
                        # 等待后点击按钮
                        await asyncio.sleep(random.randint(3, 5))
                        
                        # 处理URL按钮
                        if hasattr(btn, 'url') and btn.url:
                            if 'joinchat/' in btn.url:
                                hash_match = re.search(r'joinchat/([a-zA-Z0-9_-]+)', btn.url)
                                if hash_match:
                                    try:
                                        await self.client(functions.messages.ImportChatInviteRequest(hash_match.group(1)))
                                        self.logger.info(f"✅ 加入频道成功 (joinchat)")
                                        await asyncio.sleep(random.randint(5, 8))
                                    except Exception as e:
                                        self.logger.warning(f"⚠️ 加入频道失败: {e}")
                            elif 't.me/' in btn.url:
                                match = re.search(r't\.me/([a-zA-Z0-9_]+)', btn.url)
                                if match:
                                    username = match.group(1)
                                    try:
                                        await self.client(functions.channels.JoinChannelRequest(username))
                                        self.logger.info(f"✅ 加入频道成功: @{username}")
                                        await asyncio.sleep(random.randint(5, 8))
                                    except Exception as e:
                                        self.logger.warning(f"⚠️ 加入频道 @{username} 失败: {e}")
                        else:
                            # 回调按钮，直接点击
                            try:
                                await event.click(row_idx, row.buttons.index(btn))
                                self.logger.info(f"✅ 点击加入按钮成功")
                                await asyncio.sleep(random.randint(5, 8))
                            except Exception as e:
                                self.logger.warning(f"⚠️ 点击加入按钮失败: {e}")
            
            # 再次检测参加抽奖按钮
            for row_idx, row in enumerate(event.reply_markup.rows):
                for btn in row.buttons:
                    if not hasattr(btn, 'text'):
                        continue
                    
                    btn_text = btn.text
                    
                    if any(kw in btn_text for kw in self.GIVEAWAY_BTN_KEYWORDS):
                        self.logger.info(f"🎰 检测到参加抽奖按钮: {btn_text}")
                        await asyncio.sleep(random.randint(3, 5))
                        
                        if hasattr(btn, 'url') and btn.url:
                            if 'start=' in btn.url:
                                match = re.search(r't\.me/(\w+)\?start=([\w-]+)', btn.url)
                                if match:
                                    self.logger.info(f"🔄 发送 /start 给 @{match.group(1)}")
                                    await self.client.send_message(match.group(1), f"/start {match.group(2)}")
                                    await asyncio.sleep(random.randint(5, 8))
                                    return
                        else:
                            try:
                                await event.click(row_idx, row.buttons.index(btn))
                                self.logger.info(f"✅ 点击参加抽奖按钮成功")
                                await asyncio.sleep(random.randint(5, 8))
                                return
                            except Exception as e:
                                self.logger.warning(f"⚠️ 点击参加按钮失败: {e}")
        
        # 数学验证码
        math_match = re.search(r'(\d+)\s*([\+\-\*\/])\s*(\d+)', text)
        if math_match:
            n1, op, n2 = int(math_match.group(1)), math_match.group(2), int(math_match.group(3))
            
            # 避免年份计算（如2026-01）
            if not (n1 > 2000 and n2 < 13 and op == '-'):
                result = eval(f"{n1}{op}{n2}")
                self.logger.info(f"🧠 解出数学题: {n1}{op}{n2}={result}")
                
                await asyncio.sleep(random.randint(8, 12))
                
                # 点击答案按钮或发送答案
                if event.reply_markup:
                    for row_idx, row in enumerate(event.reply_markup.rows):
                        for col_idx, btn in enumerate(row.buttons):
                            if hasattr(btn, 'text') and str(result) == btn.text:
                                await event.click(row_idx, col_idx)
                                self.logger.info(f"✅ 点击答案按钮")
                                return
                
                await self.client.send_message(msg_bot_id, str(result))
    
    async def _auto_leave_old(self):
        """自动离开过期频道"""
        self.logger.info("🧹 清理过期频道...")
        
        db = self._load_joined_db()
        threshold = datetime.now(timezone.utc) - timedelta(days=3)
        monitors = self.bot_config.get('monitor_channel', [])
        
        to_remove = []
        for cid, join_time in db.items():
            if cid in monitors:
                continue
            
            try:
                chat = await self.client.get_entity(int(cid))
                msgs = await self.client.get_messages(chat, limit=1)
                
                should_leave = False
                if not msgs:
                    if time.time() - join_time > 5 * 24 * 3600:
                        should_leave = True
                elif msgs[0].date < threshold:
                    should_leave = True
                
                if should_leave:
                    await self.client(functions.channels.LeaveChannelRequest(chat))
                    to_remove.append(cid)
                
                await asyncio.sleep(random.randint(2, 5))
            except:
                to_remove.append(cid)
        
        for k in to_remove:
            db.pop(k, None)
        
        self._save_joined_db(db)
        self.logger.info(f"🧹 清理完成，移除 {len(to_remove)} 个频道")
    
    @property
    def stats(self) -> Dict[str, Any]:
        """统计信息"""
        return {
            **super().status,
            "queue_size": self.queue.qsize(),
            "failures": self.consecutive_failures
        }
