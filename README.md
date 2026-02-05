# Telegram Matrix Bot

重构后的Telegram矩阵管理系统，支持多账号管理、水群、抽奖等功能。

## 功能特性

- 🤖 **多账号管理** - 支持同时管理多个Telegram账号
- 💧 **水群机器人** - AI自动生成回复，避免广告检测
- 🎁 **抽奖机器人** - 自动识别并参与Telegram抽奖活动
- 🧠 **AI集成** - 使用DeepSeek API生成自然回复
- 📊 **Web仪表盘** - 实时监控、日志查看、统计展示
- ⏰ **定时任务** - 支持定时启动和休眠策略

## 项目结构

```
tg_matrix_bot/
├── core/              # 核心模块
│   ├── config.py       # 配置管理
│   ├── logger.py       # 日志系统
│   └── session.py      # Session管理
├── bot/                # 机器人模块
│   ├── base.py         # 基类
│   ├── water.py        # 水群机器人
│   ├── giveaway.py     # 抽奖机器人
│   └── ai_utils.py     # AI工具
├── api/                # Web API
│   └── server.py       # Flask服务
├── ui/                 # 前端界面
│   └── index.html      # 管理界面
├── data/               # 数据目录
│   ├── sessions/       # Session文件
│   ├── configs/        # 配置文件
│   ├── logs/           # 日志文件
│   └── history/        # 历史记录
├── config.yaml         # 主配置
├── requirements.txt    # 依赖
├── main.py            # 入口
├── Dockerfile         # Docker配置
└── docker-compose.yml # Docker编排
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置账号

编辑 `config.yaml` 或通过Web界面添加账号。

### 3. 启动服务

```bash
python main.py
```

服务启动后访问 http://localhost:5000

### 4. Docker部署

```bash
docker-compose up -d
```

## 配置说明

### config.yaml

```yaml
app:
  host: "0.0.0.0"
  port: 5000

account_defaults:
  api_id: 32841554  # Telegram API ID
  api_hash: "xxx"  # Telegram API Hash
  proxy: null      # SOCKS5代理 (格式: host:port:user:pass)
  
  # 行为配置
  min_delay: 60    # 最小循环间隔(秒)
  max_delay: 180   # 最大循环间隔(秒)
  sleep_start: 0   # 休眠开始时间(小时)
  sleep_end: 8     # 休眠结束时间(小时)
  
  # AI配置
  ai_key: "sk-xxx"  # DeepSeek API Key
  ai_max_length: 20 # AI回复最大长度
  context_count: 5 # 上下文消息数

giveaway:
  monitor_channel: "Haifpcj"  # 监控的抽奖频道
  timeout: 120                # 任务超时(秒)
```

## API 接口

### 账号管理
- `GET /api/accounts` - 列出所有账号
- `POST /api/accounts` - 添加账号
- `DELETE /api/accounts/<phone>` - 删除账号

### Bot控制
- `POST /api/start` - 启动机器人
- `POST /api/stop` - 停止机器人
- `POST /api/pause` - 暂停
- `POST /api/resume` - 恢复

### 配置
- `GET /api/config/<phone>` - 获取配置
- `POST /api/config/<phone>` - 保存配置

### 统计
- `GET /api/stats/global` - 全局统计
- `GET /api/stats/<phone>` - 单账号统计

### 日志
- `GET /api/logs/<phone>` - 获取日志

## WebSocket

支持实时日志推送：

```javascript
socket.on('log_update', (data) => {
    console.log(data.phone, data.content);
});
```

## 安全提醒

⚠️ **重要**：
- 不要泄露你的 `api_id`、`api_hash` 和 `api_key`
- 妥善保管账号Session文件
- 建议使用专用账号进行测试
- 遵守Telegram使用条款

## License

MIT
