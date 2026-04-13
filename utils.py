"""
工具模块 - 任务管理、限流、重试
"""

import asyncio
import logging
import time
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)


class TaskManager:
    """后台任务管理器 - 跟踪所有异步任务并处理异常"""

    def __init__(self):
        self._tasks: set = set()
        self._shutdown = False

    def spawn(self, coro, name: str = None):
        """创建并跟踪一个后台任务"""
        if self._shutdown:
            logger.warning(f"TaskManager 已关闭，拒绝创建任务: {name}")
            return None

        task = asyncio.create_task(coro)
        task_name = name or f"task_{id(task)}"
        self._tasks.add(task)

        # 任务完成时自动从集合中移除
        task.add_done_callback(self._tasks.discard)

        # 记录未捕获的异常
        task.add_done_callback(self._handle_exception)

        logger.debug(f"创建任务: {task_name}")
        return task

    def _handle_exception(self, task: asyncio.Task):
        """处理任务中的未捕获异常"""
        if task.done() and not task.cancelled():
            try:
                exception = task.exception()
                if exception:
                    logger.error(
                        f"后台任务异常: {exception}",
                        exc_info=(type(exception), exception, exception.__traceback__),
                    )
            except asyncio.InvalidStateError:
                pass

    async def shutdown(self, timeout: float = 10.0):
        """优雅关闭 - 等待所有任务完成"""
        self._shutdown = True

        if not self._tasks:
            logger.info("没有待完成的任务")
            return

        logger.info(f"等待 {len(self._tasks)} 个任务完成...")

        # 取消所有待完成的任务
        for task in self._tasks:
            task.cancel()

        # 等待所有任务完成或超时
        try:
            await asyncio.wait_for(
                asyncio.shield(asyncio.gather(*self._tasks, return_exceptions=True)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"等待超时，{len(self._tasks)} 个任务可能未完成")
        except Exception as e:
            logger.error(f"关闭时出错: {e}")

        logger.info("任务管理器已关闭")


class RateLimiter:
    """基于滑动窗口的内存限流器"""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60):
        """
        Args:
            max_requests: 时间窗口内允许的最大请求数
            window_seconds: 时间窗口大小（秒）
        """
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._requests: dict = {}  # user_id -> [timestamp, ...]
        self._lock = asyncio.Lock()

    async def is_allowed(self, user_id: int) -> bool:
        """检查用户是否在限制内，返回 True 表示允许，False 表示超限"""
        async with self._lock:
            now = time.time()
            cutoff = now - self._window_seconds

            # 获取或初始化用户的请求记录
            if user_id not in self._requests:
                self._requests[user_id] = []

            # 清理过期的请求记录
            self._requests[user_id] = [
                ts for ts in self._requests[user_id] if ts > cutoff
            ]

            # 检查是否超限
            if len(self._requests[user_id]) >= self._max_requests:
                return False

            # 记录新请求
            self._requests[user_id].append(now)
            return True

    async def get_remaining(self, user_id: int) -> int:
        """获取用户剩余可用请求数"""
        async with self._lock:
            now = time.time()
            cutoff = now - self._window_seconds

            if user_id not in self._requests:
                return self._max_requests

            # 清理并计数
            valid_requests = [ts for ts in self._requests[user_id] if ts > cutoff]
            self._requests[user_id] = valid_requests

            return max(0, self._max_requests - len(valid_requests))

    async def reset(self, user_id: int = None):
        """重置限流记录"""
        async with self._lock:
            if user_id is None:
                self._requests.clear()
            elif user_id in self._requests:
                del self._requests[user_id]


def async_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
):
    """
    异步重试装饰器

    Args:
        max_attempts: 最大尝试次数
        delay: 初始延迟（秒）
        backoff: 退避倍数
        exceptions: 需要重试的异常类型元组
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            last_exception = None

            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"{func.__name__} 失败 (尝试 {attempt + 1}/{max_attempts}): {e}, "
                            f"{current_delay:.1f}秒后重试..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            f"{func.__name__} 最终失败 ({max_attempts} 次尝试): {e}"
                        )

            raise last_exception

        return wrapper

    return decorator


# 全局实例
task_manager = TaskManager()
rate_limiter = RateLimiter(max_requests=30, window_seconds=60)
