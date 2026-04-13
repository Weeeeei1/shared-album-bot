"""
数据库管理模块
"""

import sqlite3
import json
import asyncio
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager
import config


class Database:
    def __init__(self, db_file: str = config.DATABASE_FILE):
        self.db_file = db_file
        self._lock = asyncio.Lock()  # 用于异步上下文中的写锁
        self.init_db()

    @contextmanager
    def get_connection(self):
        """获取数据库连接，自动启用WAL模式和超时处理"""
        conn = sqlite3.connect(self.db_file, timeout=30.0)
        conn.row_factory = sqlite3.Row
        # 启用 WAL 模式，提升并发读性能
        conn.execute("PRAGMA journal_mode=WAL")
        # 设置同步模式为 NORMAL，平衡性能和安全
        conn.execute("PRAGMA synchronous=NORMAL")
        # 设置 busy_timeout，避免立即失败
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    async def get_connection_async(self):
        """异步获取数据库连接（在线程池中执行）"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_get_connection)

    def _sync_get_connection(self):
        """同步获取连接（供线程池使用）"""
        conn = sqlite3.connect(self.db_file, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @contextmanager
    def get_connection_with_lock(self):
        """带锁的数据库连接（用于写操作）"""
        conn = self._sync_get_connection()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_db(self):
        """初始化数据库表"""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # 用户表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # 相册表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS albums (
                    album_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    max_viewers INTEGER DEFAULT 0,
                    expiry_hours INTEGER DEFAULT 0,
                    is_public INTEGER DEFAULT 0,
                    share_token TEXT,
                    auto_delete_seconds INTEGER DEFAULT 600,
                    protect_content INTEGER DEFAULT 1,
                    FOREIGN KEY (owner_id) REFERENCES users(user_id)
                )
            """)

            # 媒体表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    media_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    caption TEXT,
                    private_message_id INTEGER,
                    public_message_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (album_id) REFERENCES albums(album_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # 访问日志表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS access_logs (
                    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER NOT NULL,
                    viewer_id INTEGER NOT NULL,
                    viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (album_id) REFERENCES albums(album_id),
                    FOREIGN KEY (viewer_id) REFERENCES users(user_id)
                )
            """)

            # 黑名单表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS blacklist (
                    entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    album_id INTEGER NOT NULL,
                    blocked_user_id INTEGER NOT NULL,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT,
                    FOREIGN KEY (album_id) REFERENCES albums(album_id),
                    FOREIGN KEY (blocked_user_id) REFERENCES users(user_id),
                    UNIQUE(album_id, blocked_user_id)
                )
            """)

            # 待审核媒体表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS pending_reviews (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    media_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    album_id INTEGER NOT NULL,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    caption TEXT,
                    private_message_id INTEGER,
                    review_message_id INTEGER,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    reviewed_by INTEGER,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (album_id) REFERENCES albums(album_id)
                )
            """)

            # 话题映射表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS topic_mappings (
                    user_id INTEGER PRIMARY KEY,
                    topic_id INTEGER NOT NULL
                )
            """)

            # 迁移：添加 share_token 字段到 albums 表（如果还不存在）
            try:
                cursor.execute("SELECT share_token FROM albums LIMIT 1")
            except sqlite3.OperationalError:
                # 字段不存在，添加它（不加 UNIQUE 约束，因为已有数据）
                cursor.execute("ALTER TABLE albums ADD COLUMN share_token TEXT")
                print("已添加 share_token 字段到 albums 表")

            # 迁移：添加 auto_delete_seconds 字段
            try:
                cursor.execute("SELECT auto_delete_seconds FROM albums LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(
                    "ALTER TABLE albums ADD COLUMN auto_delete_seconds INTEGER DEFAULT 600"
                )
                print("已添加 auto_delete_seconds 字段到 albums 表")

            # 迁移：添加 protect_content 字段
            try:
                cursor.execute("SELECT protect_content FROM albums LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(
                    "ALTER TABLE albums ADD COLUMN protect_content INTEGER DEFAULT 1"
                )
                print("已添加 protect_content 字段到 albums 表")

            # 迁移：添加 allow_download 字段
            try:
                cursor.execute("SELECT allow_download FROM albums LIMIT 1")
            except sqlite3.OperationalError:
                cursor.execute(
                    "ALTER TABLE albums ADD COLUMN allow_download INTEGER DEFAULT 0"
                )
                print("已添加 allow_download 字段到 albums 表")

            # 创建索引以提升查询性能
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_album_id ON media(album_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_access_logs_album_id ON access_logs(album_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_access_logs_viewer_id ON access_logs(viewer_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_blacklist_album_id ON blacklist(album_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_reviews_user_id ON pending_reviews(user_id)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_reviews_status ON pending_reviews(status)"
            )

    # ========== 用户操作 ==========

    def add_user(self, user_id: int, username: str, first_name: str, last_name: str):
        """添加或更新用户"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_active = CURRENT_TIMESTAMP
            """,
                (user_id, username, first_name, last_name),
            )

    def is_file_exists(self, album_id: int, file_id: str) -> bool:
        """检查文件是否已存在于相册中"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM media 
                WHERE album_id = ? AND file_id = ?
                LIMIT 1
            """,
                (album_id, file_id),
            )
            return cursor.fetchone() is not None

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """获取用户信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ========== 相册操作 ==========

    def create_album(
        self, owner_id: int, name: str, max_viewers: int = 0, expiry_hours: int = 0
    ) -> int:
        """创建相册，返回相册ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO albums (owner_id, name, max_viewers, expiry_hours)
                VALUES (?, ?, ?, ?)
            """,
                (owner_id, name, max_viewers, expiry_hours),
            )
            return cursor.lastrowid

    def get_album(self, album_id: int) -> Optional[Dict[str, Any]]:
        """获取相册信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM albums WHERE album_id = ?", (album_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_albums(self, user_id: int) -> List[Dict[str, Any]]:
        """获取用户的所有相册"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM albums WHERE owner_id = ? ORDER BY created_at DESC",
                (user_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def rename_album(self, album_id: int, new_name: str):
        """重命名相册"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE albums SET name = ? WHERE album_id = ?", (new_name, album_id)
            )

    def delete_album(self, album_id: int):
        """删除相册（仅删除数据库记录）"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM albums WHERE album_id = ?", (album_id,))

    def update_album_settings(
        self,
        album_id: int,
        max_viewers: Optional[int] = None,
        expiry_hours: Optional[int] = None,
        auto_delete_seconds: Optional[int] = None,
        protect_content: Optional[int] = None,
        allow_download: Optional[int] = None,
    ):
        """更新相册设置"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if max_viewers is not None:
                cursor.execute(
                    "UPDATE albums SET max_viewers = ? WHERE album_id = ?",
                    (max_viewers, album_id),
                )
            if expiry_hours is not None:
                cursor.execute(
                    "UPDATE albums SET expiry_hours = ? WHERE album_id = ?",
                    (expiry_hours, album_id),
                )
            if auto_delete_seconds is not None:
                cursor.execute(
                    "UPDATE albums SET auto_delete_seconds = ? WHERE album_id = ?",
                    (auto_delete_seconds, album_id),
                )
            if protect_content is not None:
                cursor.execute(
                    "UPDATE albums SET protect_content = ? WHERE album_id = ?",
                    (protect_content, album_id),
                )
            if allow_download is not None:
                cursor.execute(
                    "UPDATE albums SET allow_download = ? WHERE album_id = ?",
                    (allow_download, album_id),
                )

    def generate_share_token(self, album_id: int) -> str:
        """生成分享令牌"""
        import secrets
        import string

        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 生成8位随机字符串
            while True:
                token = "".join(
                    secrets.choice(string.ascii_lowercase + string.digits)
                    for _ in range(8)
                )
                # 检查是否已存在
                cursor.execute("SELECT 1 FROM albums WHERE share_token = ?", (token,))
                if not cursor.fetchone():
                    break
            # 保存令牌
            cursor.execute(
                "UPDATE albums SET share_token = ? WHERE album_id = ?",
                (token, album_id),
            )
            return token

    def get_album_by_token(self, token: str) -> Optional[Dict[str, Any]]:
        """通过分享令牌获取相册"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM albums WHERE share_token = ?", (token,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_default_album(self, user_id: int) -> int:
        """获取或创建用户的默认相册，返回相册ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT album_id FROM albums 
                WHERE owner_id = ? AND name = ?
            """,
                (user_id, config.DEFAULT_ALBUM_NAME),
            )
            row = cursor.fetchone()
            if row:
                return row["album_id"]

            # 创建默认相册
            cursor.execute(
                """
                INSERT INTO albums (owner_id, name)
                VALUES (?, ?)
            """,
                (user_id, config.DEFAULT_ALBUM_NAME),
            )
            return cursor.lastrowid

    # ========== 媒体操作 ==========

    def add_media(
        self,
        album_id: int,
        user_id: int,
        file_id: str,
        file_type: str,
        caption: str = "",
        private_message_id: int = None,
        public_message_id: int = None,
    ) -> int:
        """添加媒体记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO media (album_id, user_id, file_id, file_type, caption, private_message_id, public_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    album_id,
                    user_id,
                    file_id,
                    file_type,
                    caption,
                    private_message_id,
                    public_message_id,
                ),
            )
            return cursor.lastrowid

    def get_album_media(self, album_id: int) -> List[Dict[str, Any]]:
        """获取相册中的所有媒体"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM media WHERE album_id = ? ORDER BY created_at",
                (album_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_media_by_id(self, media_id: int) -> Optional[Dict[str, Any]]:
        """获取单个媒体信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM media WHERE media_id = ?", (media_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_media(self, media_id: int):
        """删除单个媒体记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM media WHERE media_id = ?", (media_id,))

    def update_public_message_id(self, media_id: int, public_message_id: int):
        """更新公开频道的消息ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE media SET public_message_id = ? WHERE media_id = ?",
                (public_message_id, media_id),
            )

    # ========== 访问日志操作 ==========

    def log_access(self, album_id: int, viewer_id: int):
        """记录访问日志"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO access_logs (album_id, viewer_id)
                VALUES (?, ?)
            """,
                (album_id, viewer_id),
            )

    def get_access_logs(self, album_id: int) -> List[Dict[str, Any]]:
        """获取相册的访问日志"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT l.*, u.username, u.first_name 
                FROM access_logs l
                JOIN users u ON l.viewer_id = u.user_id
                WHERE l.album_id = ?
                ORDER BY l.viewed_at DESC
            """,
                (album_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_unique_viewers_count(self, album_id: int) -> int:
        """获取唯一访问者数量"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT COUNT(DISTINCT viewer_id) as count 
                FROM access_logs 
                WHERE album_id = ?
            """,
                (album_id,),
            )
            return cursor.fetchone()["count"]

    def has_user_viewed(self, album_id: int, viewer_id: int) -> bool:
        """检查用户是否已访问过该相册"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM access_logs 
                WHERE album_id = ? AND viewer_id = ?
                LIMIT 1
            """,
                (album_id, viewer_id),
            )
            return cursor.fetchone() is not None

    # ========== 黑名单操作 ==========

    def add_to_blacklist(self, album_id: int, blocked_user_id: int, reason: str = ""):
        """将用户加入黑名单"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO blacklist (album_id, blocked_user_id, reason)
                VALUES (?, ?, ?)
            """,
                (album_id, blocked_user_id, reason),
            )

    def remove_from_blacklist(self, album_id: int, blocked_user_id: int):
        """将用户从黑名单移除"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                DELETE FROM blacklist 
                WHERE album_id = ? AND blocked_user_id = ?
            """,
                (album_id, blocked_user_id),
            )

    def is_blacklisted(self, album_id: int, user_id: int) -> bool:
        """检查用户是否在黑名单中"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1 FROM blacklist 
                WHERE album_id = ? AND blocked_user_id = ?
                LIMIT 1
            """,
                (album_id, user_id),
            )
            return cursor.fetchone() is not None

    def get_blacklist(self, album_id: int) -> List[Dict[str, Any]]:
        """获取相册的黑名单"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT b.*, u.username, u.first_name
                FROM blacklist b
                JOIN users u ON b.blocked_user_id = u.user_id
                WHERE b.album_id = ?
                ORDER BY b.blocked_at DESC
            """,
                (album_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ========== 审核操作 ==========

    def add_pending_review(
        self,
        media_id: int,
        user_id: int,
        album_id: int,
        file_id: str,
        file_type: str,
        caption: str,
        private_message_id: int,
    ) -> int:
        """添加待审核记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO pending_reviews 
                (media_id, user_id, album_id, file_id, file_type, caption, private_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    media_id,
                    user_id,
                    album_id,
                    file_id,
                    file_type,
                    caption,
                    private_message_id,
                ),
            )
            return cursor.lastrowid

    def get_pending_review(self, review_id: int) -> Optional[Dict[str, Any]]:
        """获取待审核记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT r.*, u.username, u.first_name, a.name as album_name
                FROM pending_reviews r
                JOIN users u ON r.user_id = u.user_id
                JOIN albums a ON r.album_id = a.album_id
                WHERE r.review_id = ?
            """,
                (review_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_review_status(self, review_id: int, status: str, reviewed_by: int):
        """更新审核状态"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE pending_reviews 
                SET status = ?, reviewed_at = CURRENT_TIMESTAMP, reviewed_by = ?
                WHERE review_id = ?
            """,
                (status, reviewed_by, review_id),
            )

    def update_review_message_id(self, review_id: int, review_message_id: int):
        """更新审核消息ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE pending_reviews 
                SET review_message_id = ?
                WHERE review_id = ?
            """,
                (review_message_id, review_id),
            )

    def get_pending_reviews_count(self) -> int:
        """获取待审核数量"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT COUNT(*) as count FROM pending_reviews WHERE status = "pending"'
            )
            return cursor.fetchone()["count"]

    def get_all_pending_reviews(self) -> List[Dict[str, Any]]:
        """获取所有待审核记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, u.username, u.first_name, a.name as album_name
                FROM pending_reviews r
                JOIN users u ON r.user_id = u.user_id
                JOIN albums a ON r.album_id = a.album_id
                WHERE r.status = 'pending'
                ORDER BY r.created_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]

    def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    # ========== 话题操作 ==========

    def get_or_create_topic(self, user_id: int, topic_id: int) -> int:
        """获取或创建用户的话题映射"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT topic_id FROM topic_mappings WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return row["topic_id"]

            cursor.execute(
                """
                INSERT INTO topic_mappings (user_id, topic_id)
                VALUES (?, ?)
            """,
                (user_id, topic_id),
            )
            return topic_id

    def get_topic_id(self, user_id: int) -> Optional[int]:
        """获取用户的话题ID"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT topic_id FROM topic_mappings WHERE user_id = ?", (user_id,)
            )
            row = cursor.fetchone()
            return row["topic_id"] if row else None

    # ========== 统计操作 ==========

    def get_stats(self) -> Dict[str, Any]:
        """获取全局统计信息"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            stats = {}

            cursor.execute("SELECT COUNT(*) as count FROM users")
            stats["total_users"] = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM albums")
            stats["total_albums"] = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM media")
            stats["total_media"] = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM access_logs")
            stats["total_accesses"] = cursor.fetchone()["count"]

            return stats

    def get_user_upload_history(self, user_id: int) -> List[Dict[str, Any]]:
        """获取用户的上传历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT m.*, a.name as album_name
                FROM media m
                JOIN albums a ON m.album_id = a.album_id
                WHERE m.user_id = ?
                ORDER BY m.created_at DESC
            """,
                (user_id,),
            )
            return [dict(row) for row in cursor.fetchall()]


# 全局数据库实例
db = Database()
