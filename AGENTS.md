# AGENTS.md - 共享相册 Telegram 机器人

## 项目概述

Telegram 共享相册机器人，采用双通道机制（公开频道 + 私有群组）。使用 `python-telegram-bot==20.7`。

## 快速启动

```bash
# 本地开发
pip install -r requirements.txt
python bot.py

# Docker 部署
cp .env.example .env
# 编辑 .env 填入实际配置
docker-compose up -d

# 运行测试
pytest tests/
```

## 项目结构

```
├── bot.py              # 主程序入口，包含所有机器人逻辑
├── config.py           # 配置文件，支持环境变量
├── database.py        # SQLite 数据库封装
├── requirements.txt    # Python 依赖
├── Dockerfile          # Docker 镜像构建
├── docker-compose.yml  # Docker Compose 部署
├── .env.example        # 环境变量示例
├── tests/              # 测试目录
│   ├── conftest.py
│   └── test_database.py
└── shared-album-bot.service  # Linux systemd 服务
```

## 环境变量

| 变量名 | 必填 | 默认值 | 说明 |
|--------|------|--------|------|
| BOT_TOKEN | 是 | - | Telegram Bot Token |
| PUBLIC_CHANNEL_ID | 是 | -1003741187478 | 公开频道ID |
| PRIVATE_GROUP_ID | 是 | -1003194472806 | 私有群组ID |
| ADMIN_USER_ID | 是 | 1561094737 | 管理员用户ID |
| DATABASE_FILE | 否 | shared_album_bot.db | 数据库文件路径 |
| LOG_FILE | 否 | bot.log | 日志文件路径 |
| LOG_LEVEL | 否 | INFO | 日志级别 |

## 核心架构

- **入口函数**: `main()` 在 `bot.py:2775`
- **数据库**: SQLite (`shared_album_bot.db`)，首次运行自动创建
- **双通道机制**: 公开频道（展示审核通过内容）+ 私有群组（备份 + 管理员审核）
- **管理员审核**: 公开内容需通过私有群组内的内联按钮审核

## 命令

- `/start` - 主入口，检查频道订阅
- 其他所有交互均通过内联键盘按钮

## 配置验证

启动时机器人会验证必要配置，缺失会报错退出：
```python
errors = config.validate_config()
```

## 重要行为

- **私聊优先**: 机器人只在私聊中响应，群组消息会被忽略
- 用户必须订阅 PUBLIC_CHANNEL_ID 才能使用机器人（管理员除外）
- 标记为公开的内容先存入 `pending_reviews` 表，需管理员审核后才发布
- 相册有 `share_token`（8位随机字符串）用于安全分享链接

## 部署

### Systemd (Linux)
```bash
sudo cp shared-album-bot.service /etc/systemd/system/
sudo systemctl enable shared-album-bot
sudo systemctl start shared-album-bot
```

### Docker
```bash
docker-compose up -d
```

## 数据库结构

表: `users`, `albums`, `media`, `access_logs`, `blacklist`, `pending_reviews`, `topic_mappings`

## 测试

```bash
# 运行所有测试
pytest tests/

# 运行指定测试
pytest tests/test_database.py -v
```

## 已知问题

1. ~~**重复函数**: `handle_media` 定义了两次~~ ✅ 已修复
2. ~~**topic_mappings 表缺失**~~ ✅ 已修复
3. ~~**toggle_download_ 重复代码**~~ ✅ 已修复
4. ~~**配置写入竞态条件**~~ ✅ 已修复
5. ~~**日志泄露敏感信息**~~ ✅ 已修复
6. ~~**数据库索引缺失**~~ ✅ 已修复
7. ~~**approve_review 回调应答缺失**~~ ✅ 已修复
8. **无 CI/CD**: 无 GitHub Actions、linting 或类型检查

## 服务器部署信息

| 属性 | 值 |
|------|-----|
| 服务器地址 | 129.226.213.40 |
| 项目路径 | /root/.openclaw/workspace/shared-album-bot |
| GitHub 仓库 | https://github.com/Weeeeei1/shared-album-bot |
| GitHub token | <YOUR_GITHUB_TOKEN>

### 部署步骤

```bash
# 1. SSH 连接到服务器
ssh root@129.226.213.40

# 2. 进入项目目录
cd /root/.openclaw/workspace/shared-album-bot

# 3. 拉取最新代码
git pull origin main

# 4. 安装依赖
pip install -r requirements.txt

# 5. 重启服务（Docker 模式）
docker-compose down && docker-compose up -d

# 或者 Systemd 模式
sudo systemctl restart shared-album-bot

# 6. 查看日志验证
docker-compose logs -f
# 或
sudo journalctl -u shared-album-bot -f
```

### 回滚步骤

```bash
# 查看最近提交
git log --oneline -5

# 回滚到上一个稳定版本
git revert HEAD
git push origin main

# 重启服务
docker-compose down && docker-compose up -d
# 或
sudo systemctl restart shared-album-bot
```

### 环境变量配置

服务器上需要在 `.env` 文件或系统环境中设置：

```bash
BOT_TOKEN=<your_bot_token>
PUBLIC_CHANNEL_ID=<your_channel_id>
PRIVATE_GROUP_ID=<your_group_id>
ADMIN_USER_ID=<your_admin_id>
DATABASE_FILE=shared_album_bot.db
LOG_FILE=bot.log
LOG_LEVEL=INFO
```
