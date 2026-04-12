"""
数据库模块测试
"""

import os
import tempfile
import pytest
from database import Database


@pytest.fixture
def db_file():
    """创建临时数据库文件"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    # 清理
    if os.path.exists(path):
        os.remove(path)
    if os.path.exists(path + "-journal"):
        os.remove(path + "-journal")


@pytest.fixture
def db(db_file):
    """创建数据库实例"""
    return Database(db_file)


class TestUserOperations:
    """用户操作测试"""

    def test_add_user(self, db):
        """测试添加用户"""
        db.add_user(123, "testuser", "Test", "User")
        user = db.get_user(123)
        assert user is not None
        assert user["user_id"] == 123
        assert user["username"] == "testuser"
        assert user["first_name"] == "Test"
        assert user["last_name"] == "User"

    def test_get_nonexistent_user(self, db):
        """测试获取不存在的用户"""
        user = db.get_user(999)
        assert user is None


class TestAlbumOperations:
    """相册操作测试"""

    def test_create_album(self, db):
        """测试创建相册"""
        album_id = db.create_album(123, "测试相册")
        assert album_id is not None
        album = db.get_album(album_id)
        assert album is not None
        assert album["name"] == "测试相册"
        assert album["owner_id"] == 123

    def test_get_user_albums(self, db):
        """测试获取用户相册列表"""
        db.create_album(123, "相册1")
        db.create_album(123, "相册2")
        albums = db.get_user_albums(123)
        assert len(albums) == 2

    def test_rename_album(self, db):
        """测试重命名相册"""
        album_id = db.create_album(123, "原名")
        db.rename_album(album_id, "新名")
        album = db.get_album(album_id)
        assert album["name"] == "新名"

    def test_delete_album(self, db):
        """测试删除相册"""
        album_id = db.create_album(123, "待删除")
        db.delete_album(album_id)
        album = db.get_album(album_id)
        assert album is None

    def test_get_default_album(self, db):
        """测试获取/创建默认相册"""
        album_id = db.get_default_album(123)
        assert album_id is not None
        # 再次获取应返回相同ID
        album_id2 = db.get_default_album(123)
        assert album_id == album_id2


class TestMediaOperations:
    """媒体操作测试"""

    def test_add_media(self, db):
        """测试添加媒体"""
        album_id = db.create_album(123, "测试相册")
        media_id = db.add_media(
            album_id=album_id,
            user_id=123,
            file_id="test_file_id",
            file_type="photo",
            caption="测试图片",
        )
        assert media_id is not None
        media = db.get_media_by_id(media_id)
        assert media is not None
        assert media["file_id"] == "test_file_id"
        assert media["caption"] == "测试图片"

    def test_get_album_media(self, db):
        """测试获取相册媒体列表"""
        album_id = db.create_album(123, "测试相册")
        db.add_media(album_id, 123, "file1", "photo")
        db.add_media(album_id, 123, "file2", "video")
        media_list = db.get_album_media(album_id)
        assert len(media_list) == 2

    def test_is_file_exists(self, db):
        """测试检查文件是否已存在"""
        album_id = db.create_album(123, "测试相册")
        db.add_media(album_id, 123, "existing_file", "photo")
        assert db.is_file_exists(album_id, "existing_file") is True
        assert db.is_file_exists(album_id, "nonexistent_file") is False


class TestBlacklistOperations:
    """黑名单操作测试"""

    def test_add_to_blacklist(self, db):
        """测试添加用户到黑名单"""
        album_id = db.create_album(123, "测试相册")
        db.add_to_blacklist(album_id, 456, "测试拉黑")

        assert db.is_blacklisted(album_id, 456) is True
        assert db.is_blacklisted(album_id, 789) is False

    def test_remove_from_blacklist(self, db):
        """测试从黑名单移除"""
        album_id = db.create_album(123, "测试相册")
        db.add_to_blacklist(album_id, 456)
        db.remove_from_blacklist(album_id, 456)

        assert db.is_blacklisted(album_id, 456) is False


class TestStats:
    """统计功能测试"""

    def test_get_stats(self, db):
        """测试获取统计信息"""
        db.add_user(123, "user1", "U", "1")
        db.add_user(456, "user2", "U", "2")
        db.create_album(123, "相册1")
        album_id = db.create_album(123, "相册2")
        db.add_media(album_id, 123, "file1", "photo")

        stats = db.get_stats()
        assert stats["total_users"] == 2
        assert stats["total_albums"] == 2
        assert stats["total_media"] == 1
        assert stats["total_accesses"] == 0


class TestShareToken:
    """分享令牌测试"""

    def test_generate_share_token(self, db):
        """测试生成分享令牌"""
        album_id = db.create_album(123, "测试相册")
        token1 = db.generate_share_token(album_id)
        assert token1 is not None
        assert len(token1) == 8

    def test_get_album_by_token(self, db):
        """测试通过令牌获取相册"""
        album_id = db.create_album(123, "测试相册")
        token = db.generate_share_token(album_id)

        album = db.get_album_by_token(token)
        assert album is not None
        assert album["album_id"] == album_id

    def test_get_album_by_invalid_token(self, db):
        """测试无效令牌"""
        album = db.get_album_by_token("invalid")
        assert album is None
