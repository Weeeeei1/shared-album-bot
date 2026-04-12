# 共享相册 Telegram 机器人

## 功能特性

- **双通道机制**: 公开频道展示 + 私有群组隐形备份
- **相册管理**: 创建、重命名、删除相册
- **权限控制**: 人数限制、时长限制、黑名单
- **访问日志**: 查看谁访问过相册，支持封禁
- **媒体处理**: 支持图片、视频、文件、语音、文字

## 安装

1. 安装依赖:
```bash
pip install -r requirements.txt
```

2. 配置 `config.py` 文件

3. 运行:
```bash
python bot.py
```

## 系统服务部署

```bash
sudo cp shared-album-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable shared-album-bot
sudo systemctl start shared-album-bot
```

## 命令列表

- `/start` - 开始使用
- `/help` - 帮助信息
- `/create_album <名称>` - 创建相册
- `/my_albums` - 我的相册
- `/share_album <相册ID>` - 生成分享链接
- `/album_access <相册ID>` - 查看访问日志
- `/stats` - 统计信息

## 管理员命令

- `/admin_stats` - 全局统计
- `/admin_user <用户ID>` - 查看用户上传历史
- `/admin_topics` - 查看所有话题
