FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用
COPY *.py *.yaml ./
COPY ui/ ./ui/
COPY bot/ ./bot/

# 创建数据目录
RUN mkdir -p data/{configs,sessions,logs,history}

EXPOSE 5000

CMD ["python", "server.py"]
