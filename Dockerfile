FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY bot.py .
COPY config.py .
COPY database.py .

# 创建数据目录
RUN mkdir -p /data

# 环境变量默认值（生产环境应覆盖）
ENV PYTHONUNBUFFERED=1
ENV DATABASE_FILE=/data/shared_album_bot.db
ENV LOG_FILE=/data/bot.log
ENV LOG_LEVEL=INFO

# 暴露端口（如果有 HTTP 服务）
EXPOSE 8080

# 启动命令
CMD ["python", "bot.py"]
