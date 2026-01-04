"""异步API客户端模块 - 高性能网络请求处理"""

import asyncio
import logging
from typing import Any, Dict, Optional

import aiohttp
from cachetools import TTLCache
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from constant import (
    ASYNC_TIMEOUT,
    CACHE_MAX_SIZE,
    CACHE_TTL,
    CONNECTOR_LIMIT,
    CONNECTOR_LIMIT_PER_HOST,
    HTTP_HEADERS,
    RETRY_TIMES,
    RETRY_INTERVAL,
)


class APIClient:
    """异步API客户端，支持连接池、缓存和智能重试"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.session: Optional[aiohttp.ClientSession] = None
        self.cache: TTLCache = TTLCache(maxsize=CACHE_MAX_SIZE, ttl=CACHE_TTL)
        self.request_count = 0
        self.cache_hits = 0

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    async def initialize(self):
        """初始化会话和连接池"""
        connector = aiohttp.TCPConnector(
            limit=CONNECTOR_LIMIT,
            limit_per_host=CONNECTOR_LIMIT_PER_HOST,
            ttl_dns_cache=300,
        )
        timeout = aiohttp.ClientTimeout(total=ASYNC_TIMEOUT)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=HTTP_HEADERS,
        )
        self.logger.debug("✨ 异步API客户端已初始化")

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.logger.debug("✨ 异步API客户端已关闭")
        self.logger.info(
            f"📊 API统计 - 请求总数: {self.request_count}, "
            f"📊 缓存命中: {self.cache_hits}, "
            f"📊 命中率: {self.cache_hits / max(self.request_count, 1) * 100:.1f}%"
        )

    async def request(
            self,
            method: str,
            url: str,
            **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """发送HTTP请求，支持缓存和重试

        Args:
            method: HTTP方法 (GET, POST等)
            url: 请求URL
            **kwargs: 其他请求参数

        Returns:
            响应JSON或None
        """
        self.request_count += 1

        # 检查缓存（仅GET请求）
        if method.upper() == "GET" and url in self.cache:
            self.cache_hits += 1
            self.logger.debug(f"💾 缓存命中: {url}")
            return self.cache[url]

        if not self.session:
            await self.initialize()

        try:
            async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
                    stop=stop_after_attempt(RETRY_TIMES),
                    wait=wait_exponential(multiplier=RETRY_INTERVAL, min=1, max=10),
                    reraise=True,
            ):
                with attempt:
                    async with self.session.request(method, url, **kwargs) as response:
                        if response.status == 200:
                            data = await response.json()

                            # 缓存成功响应
                            if method.upper() == "GET":
                                self.cache[url] = data

                            return data
                        elif response.status == 429:  # Rate limit
                            reset_time = response.headers.get("X-RateLimit-Reset")
                            self.logger.warning(f"⏱️ API速率限制，重置时间: {reset_time}")
                            raise aiohttp.ClientError("Rate limited")
                        else:
                            self.logger.warning(
                                f"⚠️ 请求失败 [{response.status}]: {url}"
                            )
                            raise aiohttp.ClientError(f"HTTP {response.status}")

        except RetryError as e:
            self.logger.error(f"❌ 请求失败（已重试{RETRY_TIMES}次）: {url}")
            return None
        except Exception as e:
            self.logger.error(f"❌ 请求异常: {str(e)}")
            return None

    async def get(self, url: str, **kwargs) -> Optional[Dict[str, Any]]:
        """发送GET请求"""
        return await self.request("GET", url, **kwargs)

    async def raw_get(self, url: str) -> Optional[bytes]:
        """获取原始二进制内容（用于下载文件）"""
        self.request_count += 1

        if not self.session:
            await self.initialize()

        try:
            async for attempt in AsyncRetrying(
                    retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
                    stop=stop_after_attempt(RETRY_TIMES),
                    wait=wait_exponential(multiplier=RETRY_INTERVAL, min=1, max=10),
                    reraise=True,
            ):
                with attempt:
                    async with self.session.get(url) as response:
                        if response.status == 200:
                            return await response.read()
                        else:
                            raise aiohttp.ClientError(f"HTTP {response.status}")

        except RetryError:
            self.logger.error(f"❌ 下载失败（已重试{RETRY_TIMES}次）: {url}")
            return None
        except Exception as e:
            self.logger.error(f"❌ 下载异常: {str(e)}")
            return None

    async def batch_get(self, urls: list[str], semaphore: Optional[asyncio.Semaphore] = None) -> Dict[
        str, Optional[Dict]]:
        """批量GET请求，支持并发控制

        Args:
            urls: URL列表
            semaphore: 信号量，用于限制并发数

        Returns:
            {url: response} 字典
        """
        if semaphore is None:
            from constant import MAX_WORKERS
            semaphore = asyncio.Semaphore(MAX_WORKERS)

        async def fetch_with_semaphore(url):
            async with semaphore:
                return url, await self.get(url)

        tasks = [fetch_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        return {url: response for url, response in results}

    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
        self.logger.debug("🗑️ 缓存已清空")
