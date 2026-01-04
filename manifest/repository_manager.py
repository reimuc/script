"""GitHub仓库处理模块"""

import asyncio
import logging
import time
from pathlib import Path
from typing import List, Optional

from api_client import APIClient
from constant import DEFAULT_REPOS, Files, URLs
from file_processor import FileProcessor


class RepositoryManager:
    """GitHub仓库管理器"""

    def __init__(self, logger: logging.Logger, api_client: APIClient, file_processor: FileProcessor):
        self.logger = logger
        self.api_client = api_client
        self.file_processor = file_processor
        self.current_repo: Optional[str] = None
        self.rate_limit_info = {}

    async def check_rate_limit(self) -> bool:
        """检查GitHub API速率限制

        Returns:
            是否可以继续请求
        """
        try:
            result = await self.api_client.get(URLs.GITHUB_RATE_LIMIT)

            if not result or "rate" not in result:
                self.logger.warning("⚠️ 无法获取API限制信息")
                return True

            rate_info = result["rate"]
            remaining = rate_info.get("remaining", 0)
            reset = rate_info.get("reset", 0)

            self.rate_limit_info = {
                "remaining": remaining,
                "reset": reset,
                "reset_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(reset)),
            }

            self.logger.info(f"📊 GitHub API - 剩余请求: {remaining}")

            if remaining == 0:
                self.logger.error(
                    f"❌ API请求已达上限，重置时间: {self.rate_limit_info['reset_time']}"
                )
                return False

            return True

        except Exception as e:
            self.logger.error(f"⚠️ 检查速率限制异常: {str(e)}")
            return True

    async def find_repository(self, app_id: str, custom_repos: Optional[List[str]] = None) -> Optional[str]:
        """查找包含指定应用的仓库（选择最新的版本）

        Args:
            app_id: 应用ID（GitHub分支名）
            custom_repos: 自定义仓库列表

        Returns:
            仓库名称或None
        """
        repos = custom_repos or DEFAULT_REPOS.copy()

        latest_date: Optional[str] = None
        latest_repo: Optional[str] = None

        tasks = [self._check_repo_branch(repo, app_id) for repo in repos]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for repo, (has_branch, date) in zip(repos, results):
            if isinstance(has_branch, Exception):
                continue

            if has_branch and date:
                if not latest_date or date > latest_date:
                    latest_date = date
                    latest_repo = repo

        if latest_repo:
            self.current_repo = latest_repo
            self.logger.info(f"📦 选中仓库: {latest_repo}")
            return latest_repo
        else:
            self.logger.error(f"❌ 未在仓库中找到应用: {app_id}")
            return None

    async def _check_repo_branch(self, repo: str, branch: str) -> tuple[bool, Optional[str]]:
        """检查仓库中是否存在分支并获取提交时间

        Returns:
            (has_branch, commit_date)
        """
        try:
            url = URLs.github_branch(repo, branch)
            result = await self.api_client.get(url)

            if result and "commit" in result:
                date = result["commit"]["commit"]["committer"]["date"]
                self.logger.debug(f"✅ 仓库 {repo} 中找到分支 {branch}")
                return True, date
            else:
                self.logger.debug(f"❌ 仓库 {repo} 中未找到分支 {branch}")
                return False, None

        except Exception as e:
            self.logger.debug(f"⚠️ 检查仓库 {repo} 异常: {str(e)}")
            return False, None

    async def fetch_repository_files(
            self, repo: str, branch: str
    ) -> Optional[List[dict]]:
        """获取仓库分支的文件列表

        Args:
            repo: 仓库名称
            branch: 分支名称

        Returns:
            文件列表或None
        """
        try:
            # 获取分支信息
            branch_url = URLs.github_branch(repo, branch)
            branch_data = await self.api_client.get(branch_url)

            if not branch_data or "commit" not in branch_data:
                self.logger.warning(f"⚠️ 无法获取分支信息: {repo}/{branch}")
                return None

            # 获取文件树
            tree_url = branch_data["commit"]["commit"]["tree"]["url"]
            tree_data = await self.api_client.get(tree_url)

            if not tree_data or "tree" not in tree_data:
                self.logger.warning(f"⚠️ 无法获取文件树: {repo}/{branch}")
                return None

            files = tree_data["tree"]
            self.logger.info(f"📂 获取到 {len(files)} 个文件")
            return files

        except Exception as e:
            self.logger.error(f"❌ 获取文件列表失败: {str(e)}")
            return None

    async def process_files(
            self,
            repo: str,
            branch: str,
            files: List[dict],
            steam_path: Path,
            semaphore: Optional[asyncio.Semaphore] = None,
    ) -> bool:
        """并发处理文件列表

        Args:
            repo: 仓库名称
            branch: 分支名称
            files: 文件列表
            steam_path: Steam安装路径
            semaphore: 并发信号量

        Returns:
            是否全部成功处理
        """
        if semaphore is None:
            from constant import MAX_WORKERS
            semaphore = asyncio.Semaphore(MAX_WORKERS)

        async def process_file(file_info):
            async with semaphore:
                return await self._process_single_file(
                    repo, branch, file_info, steam_path
                )

        tasks = [process_file(file_info) for file_info in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 检查是否有失败
        failures = sum(1 for r in results if isinstance(r, Exception) or not r)
        if failures > 0:
            self.logger.warning(f"⚠️ {failures}/{len(files)} 个文件处理失败")

        return failures == 0

    async def _process_single_file(
            self, repo: str, branch: str, file_info: dict, steam_path: Path
    ) -> bool:
        """处理单个文件

        Returns:
            是否成功处理
        """
        try:
            file_path = file_info.get("path", "")

            # 跳过目录
            if file_info.get("type") == "tree":
                return True

            # 处理不同类型的文件
            if file_path.endswith(Files.MANIFEST_SUFFIX):
                return await self._handle_manifest(repo, branch, file_path, steam_path)
            elif file_path.endswith(".vdf"):
                return await self._handle_vdf(repo, branch, file_path)
            elif file_path == Files.CONFIG_JSON:
                return await self._handle_config(repo, branch, file_path)

            return True

        except Exception as e:
            self.logger.debug(f"⚠️ 处理文件异常: {str(e)}")
            return False

    async def _handle_manifest(self, repo: str, branch: str, path: str, steam_path: Path) -> bool:
        """处理清单文件"""
        try:
            url = URLs.github_raw(repo, branch, path)
            content = await self.api_client.raw_get(url)

            if content:
                success = await self.file_processor.save_manifest_file(path, steam_path, content)
                return success
            return False
        except Exception as e:
            self.logger.debug(f"⚠️ 处理清单文件 {path} 失败: {str(e)}")
            return False

    async def _handle_vdf(self, repo: str, branch: str, path: str) -> bool:
        """处理VDF文件"""
        try:
            url = URLs.github_raw(repo, branch, path)
            content = await self.api_client.raw_get(url)

            if not content:
                return False

            if path == Files.APPINFO_VDF:
                app_name = await self.file_processor.parse_appinfo_vdf(content)
                return app_name is not None
            elif path == Files.KEY_VDF:
                return await self.file_processor.parse_key_vdf(content)

            return True
        except Exception as e:
            self.logger.debug(f"⚠️ 处理VDF文件 {path} 失败: {str(e)}")
            return False

    async def _handle_config(self, repo: str, branch: str, path: str) -> bool:
        """处理配置JSON文件"""
        try:
            url = URLs.github_raw(repo, branch, path)
            config_data = await self.api_client.get(url)

            if config_data:
                dlcs, package_dlcs = await self.file_processor.parse_config_json(config_data)
                return True
            return False
        except Exception as e:
            self.logger.debug(f"⚠️ 处理配置文件 {path} 失败: {str(e)}")
            return False

    def clear(self):
        """清空数据"""
        self.current_repo = None
        self.rate_limit_info.clear()
