"""
Flask API Server - Web管理界面后端（单进程架构）
"""
import asyncio
import json
import time
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from bot_engine import (
    config, log_manager, TaskScheduler, WaterEngine, 
    GiveawayEngine, AccountPool
)

app = Flask(__name__, static_folder='ui', static_url_path='')
app.config['SECRET_KEY'] = 'tg-matrix-bot-secret!'

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode=None,  # 使用默认模式
    ping_timeout=60,
    ping_interval=25
)

# 初始化
log_manager.set_socketio(socketio)
scheduler = TaskScheduler()
account_pool = AccountPool()
account_pool.refresh_accounts()


# ============== 静态文件路由 ==============
@app.route('/')
def index():
    return send_file('ui/dashboard.html')


@app.route('/<path:filename>')
def static_files(filename):
    return send_file(f'ui/{filename}')


# ============== 账号管理API ==============
@app.route('/api/accounts')
def list_accounts():
    """列出所有账号"""
    account_pool.refresh_accounts()
    accounts = []
    data_dir = Path(__file__).parent / "data"
    
    for phone in account_pool._phones:
        water_running = phone in scheduler._water_tasks
        session_file = data_dir / "sessions" / f"{phone}.session"
        
        accounts.append({
            "phone": phone,
            "water_running": water_running,
            "giveaway_running": False,
            "has_session": session_file.exists(),
            "weight": account_pool._weights.get(phone, 1)
        })
    
    return jsonify(accounts)


@app.route('/api/accounts', methods=['POST'])
def add_account():
    """添加新账号"""
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"error": "手机号必填"}), 400
    
    config_data = {
        'api_id': data.get('api_id'),
        'api_hash': data.get('api_hash'),
        'proxy': data.get('proxy'),
        **config.account_defaults
    }
    
    config.save_account_config(phone, config_data)
    account_pool.refresh_accounts()
    
    log_manager.log('system', 'INFO', f"添加账号: {phone}", "系统")
    return jsonify({"status": "success", "phone": phone})


@app.route('/api/accounts/<phone>', methods=['DELETE'])
def delete_account(phone):
    """删除账号"""
    scheduler.stop_water(phone)
    
    data_dir = Path(__file__).parent / "data"
    
    # 删除配置文件
    config_file = data_dir / "configs" / f"{phone}.yaml"
    if config_file.exists():
        config_file.unlink()
    
    # 删除会话文件
    session_file = data_dir / "sessions" / f"{phone}.session"
    if session_file.exists():
        session_file.unlink()
    
    # 删除日志文件
    log_file = data_dir / "logs" / f"bot_{phone}.log"
    if log_file.exists():
        log_file.unlink()
    
    # 删除历史文件
    history_file = data_dir / "history" / f"{phone}.json"
    if history_file.exists():
        history_file.unlink()
    
    account_pool.refresh_accounts()
    log_manager.log('system', 'INFO', f"删除账号: {phone}", "系统")
    return jsonify({"status": "success"})


# ============== Bot控制API ==============
@app.route('/api/water/start', methods=['POST'])
def start_water():
    """启动水群（跨账号轮询）"""
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"error": "手机号必填"}), 400
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(scheduler.start_water_loop(phone))
    
    log_manager.log('system', 'INFO', f"🚀 启动水群: {phone}", "系统")
    return jsonify({"status": "started"})


@app.route('/api/water/stop', methods=['POST'])
def stop_water():
    """停止水群"""
    data = request.json
    phone = data.get('phone')
    scheduler.stop_water(phone)
    return jsonify({"status": "stopped"})


@app.route('/api/giveaway/start', methods=['POST'])
def start_giveaway():
    """启动抽奖"""
    data = request.json
    phone = data.get('phone')
    
    if not phone:
        return jsonify({"error": "手机号必填"}), 400
    
    log_manager.log('system', 'INFO', f"🎯 启动抽奖: {phone}", "系统")
    
    # 异步执行抽奖任务
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    future = loop.run_in_executor(None, lambda: asyncio.run(
        GiveawayEngine(phone).run_loop()
    ))
    
    return jsonify({"status": "started"})


@app.route('/api/giveaway/backfill', methods=['POST'])
def run_giveaway_backfill():
    """
    抽奖手动补录
    请求体: {"phone": "+86...", "days": 1}
    """
    data = request.json
    phone = data.get('phone')
    days = data.get('days', 1)
    
    if not phone:
        return jsonify({"error": "手机号必填"}), 400
    
    log_manager.log('system', 'INFO', f"🚀 启动抽奖补录: {phone}, 回溯{days}天", "系统")
    
    # 使用subprocess启动后台任务
    import subprocess
    script_path = Path(__file__).parent / "giveaway_backfill.py"
    
    # 创建补录脚本
    script_content = f'''#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path
from bot_engine import config, log_manager, GiveawayEngine

data_dir = Path(__file__).parent / "data"
sys.path.insert(0, str(Path(__file__).parent))

async def run():
    engine = GiveawayEngine("{phone}")
    await engine.run_backfill({days})

asyncio.run(run())
'''
    
    script_file = Path(__file__).parent / "giveaway_backfill.py"
    with open(script_file, 'w') as f:
        f.write(script_content)
    
    # 后台运行
    subprocess.Popen(
        [sys.executable, str(script_file)],
        cwd=str(Path(__file__).parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )
    
    task_id = f"giveaway_{phone}_{int(time.time())}"
    return jsonify({"status": "started", "task_id": task_id, "days": days})


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
    
    old_config = config.load_account_config(phone)
    new_config['api_id'] = old_config.get('api_id')
    new_config['api_hash'] = old_config.get('api_hash')
    
    config.save_account_config(phone, new_config)
    
    weight = new_config.get('weight', 1)
    account_pool.set_weight(phone, weight)
    
    log_manager.log('system', 'INFO', f"💾 保存配置: {phone}", "系统")
    return jsonify({"status": "success"})


# ============== 统计API ==============
@app.route('/api/stats/<phone>')
def get_stats(phone):
    """获取账号统计"""
    data_dir = Path(__file__).parent / "data"
    history_file = data_dir / "history" / f"{phone}.json"
    
    stats = {
        "phone": phone,
        "water_running": phone in scheduler._water_tasks,
        "total_messages": 0,
        "active_groups": 0
    }
    
    if history_file.exists():
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            stats["total_messages"] = sum(len(v) for v in data.values())
            stats["active_groups"] = len(data)
        except:
            pass
    
    return jsonify(stats)


@app.route('/api/stats/global')
def global_stats():
    """全局统计"""
    total_messages = 0
    data_dir = Path(__file__).parent / "data"
    
    history_dir = data_dir / "history"
    if history_dir.exists():
        for f in history_dir.glob('*.json'):
            try:
                with open(f, 'r', encoding='utf-8') as fp:
                    data = json.load(fp)
                total_messages += sum(len(v) for v in data.values())
            except:
                pass
    
    return jsonify({
        "total_messages": total_messages,
        "total_giveaway_entries": 0,
        "active_bots": len(scheduler._water_tasks),
        "total_accounts": len(account_pool._phones)
    })


# ============== 日志API ==============
@app.route('/api/logs/<phone>')
def get_logs(phone):
    """获取日志"""
    log_file = Path(__file__).parent / "data" / "logs" / f"bot_{phone}.log"
    if log_file.exists():
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            return "".join(lines[-200:])
    return "无日志内容"


# ============== 登录API ==============
@app.route('/api/login/step1', methods=['POST'])
def login_step1():
    """发送验证码"""
    data = request.json
    phone = data.get('phone')
    api_id = int(data.get('api_id'))
    api_hash = data.get('api_hash')
    
    try:
        import subprocess
        
        script_path = Path(__file__).parent / "login_helper.py"
        result = subprocess.run(
            [sys.executable, str(script_path), "send_code", phone, str(api_id), api_hash],
            capture_output=True, text=True, cwd=str(Path(__file__).parent)
        )
        
        output = result.stdout.strip()
        if output.startswith("SUCCESS:"):
            phone_code_hash = output.replace("SUCCESS:", "")
            return jsonify({"status": "sent", "phone_code_hash": phone_code_hash})
        else:
            return jsonify({"status": "error", "message": output.replace("ERROR:", "")}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


@app.route('/api/login/step2', methods=['POST'])
def login_step2():
    """验证验证码"""
    data = request.json
    phone = data.get('phone')
    code = data.get('code')
    phone_code_hash = data.get('phone_code_hash')
    password = data.get('password')
    
    try:
        import subprocess
        
        script_path = Path(__file__).parent / "login_helper.py"
        result = subprocess.run(
            [sys.executable, str(script_path), "verify_code", phone, "0", "", code, phone_code_hash, password],
            capture_output=True, text=True, cwd=str(Path(__file__).parent)
        )
        
        output = result.stdout.strip()
        if output == "SUCCESS":
            return jsonify({"status": "success"})
        else:
            error_msg = output.replace("ERROR:", "") if output.startswith("ERROR:") else "验证码错误"
            return jsonify({"status": "error", "message": error_msg}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400
        api_hash = account_config.get('api_hash')
        
        client = TelegramClient(str(session_file), api_id, api_hash)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        async def sign_in():
            await client.connect()
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash, password=password)
            return True
        
        success = loop.run_until_complete(sign_in())
        loop.close()
        
        if success:
            return jsonify({"status": "success"})
        return jsonify({"status": "error", "message": "验证码错误"}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400


# ============== 运行服务器 ==============
def run_server():
    """启动服务器"""
    socketio.run(
        app,
        host=config.app.get('host', '0.0.0.0'),
        port=config.app.get('port', 5000),
        debug=False
    )


if __name__ == '__main__':
    run_server()
