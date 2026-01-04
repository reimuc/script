"""文件处理模块 - 高效的VDF/JSON解析和文件操作"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import vdf

from constant import Steam


class FileProcessor:
    """文件处理器，支持异步文件操作和VDF解析"""

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self.manifests: List[str] = []
        self.depots: Dict[int, Optional[str]] = {}  # {depot_id: decryption_key}

    async def parse_appinfo_vdf(self, content: bytes) -> Optional[str]:
        """异步解析 appinfo.vdf 文件

        Args:
            content: 文件内容（字节）

        Returns:
            应用名称或None
        """
        try:
            # 在线程池中运行VDF解析以避免阻塞
            loop = asyncio.get_event_loop()
            appinfo_config = await loop.run_in_executor(
                None, vdf.loads, content.decode()
            )
            appname = appinfo_config.get("common", {}).get("name", "Unknown")
            self.logger.info(f"📦 应用名称: {appname}")
            return appname
        except Exception as e:
            self.logger.error(f"⛔ 解析 appinfo.vdf 失败: {str(e)}")
            return None

    async def parse_key_vdf(self, content: bytes) -> bool:
        """异步解析 key.vdf 文件

        Args:
            content: 文件内容（字节）

        Returns:
            是否成功解析
        """
        try:
            loop = asyncio.get_event_loop()
            depot_config = await loop.run_in_executor(
                None, vdf.loads, content.decode()
            )
            depot_dict: Dict = depot_config.get("depots", {})

            for depot_id_str, depot_info in depot_dict.items():
                try:
                    depot_id = int(depot_id_str)
                    decryption_key = depot_info.get("DecryptionKey")
                    self.depots[depot_id] = decryption_key
                except (ValueError, KeyError, TypeError):
                    continue

            if self.depots:
                self.logger.info(f"🔑 已找到 {len(self.depots)} 个解密密钥")
            return True
        except Exception as e:
            self.logger.error(f"⛔ 解析 key.vdf 失败: {str(e)}")
            return False

    async def parse_config_json(self, config_data: Dict[str, Any]) -> Tuple[List[int], List[int]]:
        """解析配置JSON文件

        Args:
            config_data: 配置JSON数据

        Returns:
            (dlc_ids, package_dlc_ids) 元组
        """
        try:
            dlcs: List[int] = config_data.get("dlcs", [])
            packagedlcs: List[int] = config_data.get("packagedlcs", [])

            if dlcs:
                self.logger.info(f"🎮 检测到 {len(dlcs)} 个DLC")
                for dlc_id in dlcs:
                    self.depots[dlc_id] = None

            if packagedlcs:
                self.logger.info(f"🎯 检测到 {len(packagedlcs)} 个独立DLC")

            return dlcs, packagedlcs
        except Exception as e:
            self.logger.error(f"❌ 解析配置文件失败: {str(e)}")
            return [], []

    async def save_manifest_file(self, path: str, steam_path: Path, content: bytes) -> bool:
        """异步保存清单文件

        Args:
            path: 文件相对路径
            steam_path: Steam安装路径
            content: 文件内容

        Returns:
            是否保存成功
        """
        try:
            depot_cache = steam_path / Steam.DEPOT_CACHE
            save_path = depot_cache / path

            # 如果文件已存在，跳过
            if save_path.exists():
                self.logger.debug(f"⏭️ 清单文件已存在: {path}")
                return True

            # 创建目录
            save_path.parent.mkdir(parents=True, exist_ok=True)

            # 异步写入到临时文件
            temp_path = save_path.with_suffix('.tmp')
            async with aiofiles.open(temp_path, 'wb') as f:
                await f.write(content)

            # 原子替换
            temp_path.replace(save_path)
            self.logger.info(f"📥 清单文件已保存: {path}")
            self.manifests.append(path)
            return True

        except Exception as e:
            self.logger.error(f"❌ 保存清单文件失败 {path}: {str(e)}")
            return False

    async def save_lua_config(
            self,
            app_id: str,
            app_name: Optional[str],
            steam_path: Path,
            use_fixed_manifest: bool = False,
    ) -> bool:
        """异步保存Lua配置文件

        Args:
            app_id: 应用ID
            app_name: 应用名称
            steam_path: Steam安装路径
            use_fixed_manifest: 是否使用固定清单模式

        Returns:
            是否保存成功
        """
        try:
            # 构建Lua内容
            lua_lines = []

            if app_name:
                lua_lines.append(f"-- {app_name}")

            # 添加depot和密钥信息
            for depot_id, decryption_key in sorted(self.depots.items()):
                if decryption_key:
                    lua_lines.append(f'addappid({depot_id}, 1, "{decryption_key}")')
                else:
                    lua_lines.append(f"addappid({depot_id}, 1)")

            # 如果启用固定清单模式，添加清单ID
            if use_fixed_manifest and self.manifests:
                manifest_map = self._parse_manifest_ids()
                for depot_id, manifest_id in sorted(manifest_map.items()):
                    lua_lines.append(f'setManifestid({depot_id}, "{manifest_id}")')

            lua_content = "\n".join(lua_lines) + "\n"

            # 保存配置文件
            lua_filename = f"{app_id}.lua"
            lua_path = steam_path / Steam.PLUGIN_DIR
            lua_path.mkdir(parents=True, exist_ok=True)
            lua_filepath = lua_path / lua_filename

            temp_filepath = lua_filepath.with_suffix('.tmp')
            async with aiofiles.open(temp_filepath, 'w', encoding='utf-8') as f:
                await f.write(lua_content)

            temp_filepath.replace(lua_filepath)
            self.logger.info(f"📝 配置已保存至: {lua_filepath}")
            return True

        except Exception as e:
            self.logger.error(f"❌ 保存Lua配置失败: {str(e)}")
            return False

    def _parse_manifest_ids(self) -> Dict[int, str]:
        """从清单路径列表解析depot_id -> manifest_id映射

        例: "123456_abcdef123456.manifest" -> {123456: "abcdef123456"}
        """
        manifest_map = {}
        for manifest_path in self.manifests:
            try:
                parts = manifest_path.split("_")
                if len(parts) >= 2:
                    depot_id = int(parts[0])
                    manifest_id = parts[1].split(".")[0]
                    manifest_map[depot_id] = manifest_id
            except (ValueError, IndexError):
                continue
        return manifest_map

    def add_depot(self, depot_id: int, decryption_key: Optional[str] = None):
        """添加depot信息"""
        if depot_id not in self.depots:
            self.depots[depot_id] = decryption_key
        elif decryption_key and not self.depots[depot_id]:
            self.depots[depot_id] = decryption_key

    def get_depot_list(self) -> List[Tuple[int, Optional[str]]]:
        """获取排序后的depot列表"""
        return sorted(self.depots.items(), key=lambda x: x[0])

    def clear(self):
        """清空所有数据"""
        self.manifests.clear()
        self.depots.clear()
