"""
Flask API Server - Web管理界面后端
"""
import json
import asyncio
from datetime import datetime
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from pathlib import Path
from core.config import config
from core.logger import get_logger
from bot.water import WaterBot
from bot.giveaway import GiveawayBot

app = Flask(__name__, static_folder='../ui', static_url_path='')
app.config['SECRET_KEY'] = config.app.get('secret_key', 'secret!')
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='eventlet'
)

# 全局状态
running_bots: dict = {}
logger = get_logger("server")


# ============== 静态文件路由 ==============
@app.route('/')
def index():
    return send_file('../ui/index.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_file(f'../ui/{filename}')


# ============== 账号管理API ==============
@app.route('/api/accounts')
def list_accounts():
    """列出所有账号"""
    accounts = []
    config_dir = Path(__file__).parent.parent / "data" / "configs"
    
    if config_dir.exists():
        for f in config_dir.glob('*.yaml'):
            phone = f.stem
            water_running = f"{phone}_water" in running_bots and running_bots[f"{phone}_water"].running
            give_running = f"{phone}_giveaway" in running_bots and running_bots[f"{phone}_giveaway"].running
            
            session_file = Path(__file__).parent.parent / "data" / "sessions" / f"{phone}.session"
            
            accounts.append({
                "phone": phone,
                "water_running": water_running,
                "giveaway_running": give_running,
                "has_session": session_file.exists()
            })
    
    return jsonify(accounts)


@app.route('/api/accounts', methods=['POST'])
def add_account():
    """添加新账号"""
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"error": "Phone required"}), 400
    
    # 保存配置
    config_dir = Path(__file__).parent.parent / "data" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    
    account_config = {
        'api_id': data.get('api_id'),
        'api_hash': data.get('api_hash'),
        'proxy': data.get('proxy'),
        **config.account_defaults
    }
    
    config_file = config_dir / f"{phone}.yaml"
    with open(config_file, 'w', encoding='utf-8') as f:
        import yaml
        yaml.dump(account_config, f, default_flow_style=False, allow_unicode=True)
    
    return jsonify({"status": "success", "phone": phone})


@app.route('/api/accounts/<phone>', methods=['DELETE'])
def delete_account(phone):
    """删除账号"""
    import os
    
    # 停止运行的bot
    for key in list(running_bots.keys()):
        if key.startswith(phone):
            asyncio.create_task(running_bots[key].stop())
            del running_bots[key]
    
    # 删除文件
    for folder in ['sessions', 'configs', 'logs', 'history']:
        file_path = Path(__file__).parent.parent / "data" / folder / f"{phone}.*"
        for f in file_path.glob('*'):
            try:
                f.unlink()
            except:
                pass
    
    return jsonify({"status": "success"})


# ============== Bot控制API ==============
def start_bot_task(bot):
    """在eventlet环境中启动bot"""
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(bot.start())
    finally:
        loop.close()

@app.route('/api/start', methods=['POST'])
def start_bot():
    """启动机器人"""
    data = request.json
    phone = data.get('phone')
    bot_type = data.get('type', 'water')
    
    task_id = f"{phone}_{bot_type}"
    
    if task_id in running_bots and running_bots[task_id].running:
        return jsonify({"status": "running"})
    
    # 创建bot实例
    if bot_type == 'water':
        bot = WaterBot(phone)
    elif bot_type == 'giveaway':
        bot = GiveawayBot(phone)
    else:
        return jsonify({"error": "Unknown bot type"}), 400
    
    running_bots[task_id] = bot
    
    # 发送WebSocket日志到前端
    emoji = '🟢' if bot_type == 'water' else '🎯'
    source = '水群' if bot_type == 'water' else '监控抽奖'
    socketio.emit('log_update', {
        'phone': phone,
        'level': 'info',
        'source': source,
        'message': f'{emoji} 启动 {bot_type}: {phone}',
        'timestamp': datetime.now().isoformat()
    })
    
    # 使用新线程启动bot（避免eventlet事件循环问题）
    import threading
    thread = threading.Thread(target=start_bot_task, args=(bot,), daemon=True)
    thread.start()
    
    return jsonify({"status": "started"})


@app.route('/api/stop', methods=['POST'])
def stop_bot():
    """停止机器人"""
    data = request.json
    phone = data.get('phone')
    bot_type = data.get('type', 'water')
    
    task_id = f"{phone}_{bot_type}"
    
    if task_id in running_bots:
        socketio.start_background_task(running_bots[task_id].stop)
        del running_bots[task_id]
        
        # 发送WebSocket日志到前端
        source = '水群' if bot_type == 'water' else '监控抽奖'
        socketio.emit('log_update', {
            'phone': phone,
            'level': 'info',
            'source': source,
            'message': f'⏸️ 停止 {bot_type}: {phone}',
            'timestamp': datetime.now().isoformat()
        })
    
    return jsonify({"status": "stopped"})


# ============== 抽奖补录API ==============
@app.route('/api/giveaway/backfill', methods=['POST'])
def giveaway_backfill():
    """抽奖补录 - 手动触发"""
    data = request.json
    phone = data.get('phone')
    days = data.get('days', 1)
    
    if not phone:
        return jsonify({"error": "缺少手机号"}), 400
    
    task_id = f"{phone}_giveaway"
    
    # 检查是否已在运行
    if task_id in running_bots and running_bots[task_id].running:
        return jsonify({"status": "running", "message": "抽奖任务已在运行中"})
    
    logger.info(f"🚀 启动抽奖补录: {phone} (回溯{days}天)")
    
    # 发送WebSocket日志到前端
    socketio.emit('log_update', {
        'phone': phone,
        'level': 'info',
        'source': '监控抽奖',
        'message': f'🚀 启动抽奖补录: {phone} (回溯{days}天)',
        'timestamp': datetime.now().isoformat()
    })
    
    # 使用subprocess启动后台任务
    import subprocess
    import sys
    from pathlib import Path
    
    script_content = f'''#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from bot import giveaway
from bot_engine import config, log_manager

# 重新初始化log_manager以使用subprocess
log_manager._logs = {{}}
log_manager._file_handler = None
log_manager._socketio = None  # 子进程不使用WebSocket

async def run():
    bot = giveaway.GiveawayBot("{phone}")
    bot.backfill_mode = True
    bot.backfill_days = {days}
    await bot.start()

asyncio.run(run())
'''
    
    script_file = Path(__file__).parent.parent / "giveaway_backfill.py"
    with open(script_file, 'w') as f:
        f.write(script_content)
    
    # 后台运行
    subprocess.Popen(
        [sys.executable, str(script_file)],
        cwd=str(Path(__file__).parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )
    
    return jsonify({
        "status": "started",
        "message": f"抽奖补录已启动 (回溯{days}天)",
        "phone": phone,
        "days": days
    })


@app.route('/api/pause', methods=['POST'])
def pause_bot():
    """暂停机器人"""
    data = request.json
    phone = data.get('phone')
    
    pause_file = Path(__file__).parent.parent / "data" / f"pause_{phone}.flag"
    pause_file.parent.mkdir(parents=True, exist_ok=True)
    pause_file.touch()
    
    logger.info(f"⏸️ 暂停: {phone}")
    return jsonify({"status": "paused"})


@app.route('/api/resume', methods=['POST'])
def resume_bot():
    """恢复机器人"""
    data = request.json
    phone = data.get('phone')
    
    pause_file = Path(__file__).parent.parent / "data" / f"pause_{phone}.flag"
    if pause_file.exists():
        pause_file.unlink()
    
    logger.info(f"▶️ 恢复: {phone}")
    return jsonify({"status": "resumed"})


# ============== 配置API ==============
@app.route('/api/config/<phone>')
def get_config(phone):
    """获取账号配置"""
    account_config = config.load_account_config(phone)
    return jsonify(account_config)


@app.route('/api/config/<phone>', methods=['POST'])
def save_config(phone):
    """保存账号配置"""
    new_config = request.json
    
    # 确保api_id和api_hash不被覆盖
    old_config = config.load_account_config(phone)
    new_config['api_id'] = old_config.get('api_id')
    new_config['api_hash'] = old_config.get('api_hash')
    
    config.update_account_config(phone, new_config)
    
    # 发送WebSocket日志到前端
    socketio.emit('log_update', {
        'phone': phone,
        'level': 'success',
        'source': '系统',
        'message': f'💾 保存配置: {phone}',
        'timestamp': datetime.now().isoformat()
    })
    
    return jsonify({"status": "success"})


# ============== 统计API ==============
@app.route('/api/stats/<phone>')
def get_stats(phone):
    """获取统计数据"""
    task_id = f"{phone}_water"
    task_id_giveaway = f"{phone}_giveaway"
    
    # 检查是否有正在运行的bot
    water_running = task_id in running_bots and running_bots[task_id].running
    giveaway_running = task_id_giveaway in running_bots and running_bots[task_id_giveaway].running
    
    # 基础返回数据
    stats = {
        "water_running": water_running,
        "giveaway_running": giveaway_running,
        "today_messages": 0,
        "giveaway_participated": 0
    }
    
    # 如果有正在运行的bot，从bot获取统计
    if water_running and task_id in running_bots:
        bot_stats = running_bots[task_id].stats
        stats.update(bot_stats)
    
    # 如果有正在运行的抽奖bot
    if giveaway_running and task_id_giveaway in running_bots:
        giveaway_stats = running_bots[task_id_giveaway].stats
        stats.update(giveaway_stats)
    
    # 否则从历史文件读取
    history_file = Path(__file__).parent.parent / "data" / "history" / f"{phone}.json"
    if history_file.exists():
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if '🎁 抽奖参与总数' in data:
                stats['giveaway_participated'] = len(data['🎁 抽奖参与总数'])
        except:
            pass
    
    return jsonify(stats)


@app.route('/api/stats/global')
def global_stats():
    """全局统计"""
    total_messages = 0
    total_giveaway = 0
    
    history_dir = Path(__file__).parent.parent / "data" / "history"
    if history_dir.exists():
        for f in history_dir.glob('*.json'):
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                total_messages += sum(len(v) for k, v in data.items() if k != '🎁 抽奖参与总数')
                if '🎁 抽奖参与总数' in data:
                    total_giveaway += len(data['🎁 抽奖参与总数'])
            except:
                pass
    
    return jsonify({
        "total_messages": total_messages,
        "total_giveaway_entries": total_giveaway,
        "active_bots": len(running_bots)
    })


# ============== 日志API ==============
@app.route('/api/logs/<phone>')
def get_logs(phone):
    """获取日志"""
    # 支持多种日志文件名格式
    log_file = Path(__file__).parent.parent / "data" / "logs" / f"{phone}.log"
    if not log_file.exists():
        # 尝试 giveaway_{phone}.log
        log_file = Path(__file__).parent.parent / "data" / "logs" / f"giveaway_{phone}.log"
    if not log_file.exists():
        # 尝试 water_{phone}.log
        log_file = Path(__file__).parent.parent / "data" / "logs" / f"water_{phone}.log"
    
    if log_file.exists():
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            return "".join(lines[-150:])
    return "无日志内容"


# ============== 登录API ==============
@app.route('/api/login/step1', methods=['POST'])
def login_step1():
    """发送验证码"""
    import asyncio
    from core.session import SessionManager
    
    data = request.json
    phone = data.get('phone')
    api_id = int(data.get('api_id'))
    api_hash = data.get('api_hash')
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        phone_code_hash = loop.run_until_complete(
            SessionManager.send_code_request(phone)
        )
        loop.close()
        return jsonify({"status": "sent", "phone_code_hash": phone_code_hash})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/login/step2', methods=['POST'])
def login_step2():
    """验证验证码"""
    import asyncio
    from core.session import SessionManager
    
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    phone_code_hash = data.get('phone_code_hash')
    password = data.get('password')
    
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(
            SessionManager.sign_in(phone, code, phone_code_hash, password)
        )
        loop.close()
        if success:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "验证码错误"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ============== WebSocket日志 ==============
def start_log_watcher():
    """日志监控线程"""
    import threading
    import time
    
    files_map = {}
    
    def watch():
        log_dir = Path(__file__).parent.parent / "data" / "logs"
        
        while True:
            try:
                if not log_dir.exists():
                    time.sleep(2)
                    continue
                
                # 更新文件句柄
                for f in log_dir.glob('*.log'):
                    phone = f.stem
                    if phone not in files_map:
                        try:
                            fh = open(f, 'r', encoding='utf-8', errors='ignore')
                            fh.seek(0, 2)
                            files_map[phone] = fh
                        except:
                            pass
                
                # 读取新行
                for phone, fh in list(files_map.items()):
                    try:
                        lines = fh.readlines()
                        if lines:
                            socketio.emit('log_update', {
                                'phone': phone,
                                'content': "".join(lines)
                            })
                    except:
                        try:
                            fh.close()
                        except:
                            pass
                        del files_map[phone]
                
                time.sleep(0.5)
            except Exception as e:
                print(f"Log watcher error: {e}")
                time.sleep(5)
    
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()


def run_server():
    """启动服务器"""
    socketio.run(
        app,
        host=config.app.get('host', '0.0.0.0'),
        port=config.app.get('port', 5000),
        debug=config.app.get('debug', False)
    )


if __name__ == '__main__':
    run_server()
