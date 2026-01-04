"""Steam应用信息管理模块"""

import logging
from typing import List, Optional

from api_client import APIClient
from constant import URLs


class SteamAppManager:
    """Steam应用信息管理器"""

    def __init__(self, logger: logging.Logger, api_client: APIClient):
        self.logger = logger
        self.api_client = api_client
        self.app_id: str = ""
        self.app_name: Optional[str] = None
        self.dlc_ids: List[int] = []

    async def search_app(self, query: str) -> Optional[int]:
        """搜索Steam应用

        Args:
            query: 应用名称或ID

        Returns:
            应用ID或None
        """
        # 如果输入是纯数字，直接返回
        if query.isdigit():
            self.app_id = query
            return int(query)

        # 搜索应用
        try:
            search_url = URLs.steam_search(query)
            result = await self.api_client.get(search_url)

            if not result or "items" not in result:
                self.logger.error("❌ 未找到匹配的应用")
                return None

            items = result["items"]
            if not items:
                self.logger.error("❌ 搜索结果为空")
                return None

            # 如果只有一个结果，直接选择
            if len(items) == 1:
                app_id = items[0]["id"]
                self.app_id = str(app_id)
                self.logger.info(f"✨ 已选择应用: [{app_id}] {items[0]['name']}")
                return app_id

            # 多个结果，显示列表
            self.logger.info("🎯 检索到以下应用:")
            for idx, item in enumerate(items[:10], 1):  # 最多显示10个
                item_type = item.get("type", "unknown")
                self.logger.info(f"  {idx}. [{item['id']}] [{item_type}] {item['name']}")

            # 让用户选择
            while True:
                try:
                    choice = input("请选择应用序号 (1-10): ").strip()
                    choice_idx = int(choice) - 1
                    if 0 <= choice_idx < len(items[:10]):
                        app_id = items[choice_idx]["id"]
                        self.app_id = str(app_id)
                        self.logger.info(f"✨ 已选择应用: [{app_id}] {items[choice_idx]['name']}")
                        return app_id
                    else:
                        self.logger.warning("⚠️ 输入无效，请重新选择")
                except (ValueError, KeyboardInterrupt):
                    self.logger.warning("⚠️ 输入无效，请重新选择")

        except Exception as e:
            self.logger.error(f"❌ 搜索应用失败: {str(e)}")
            return None

    async def fetch_app_details(self, app_id: str) -> bool:
        """获取应用详情（包括名称和DLC）

        Args:
            app_id: 应用ID

        Returns:
            是否成功获取
        """
        try:
            detail_url = URLs.steam_app_details(app_id)
            result = await self.api_client.get(detail_url)

            if not result or not isinstance(result, dict):
                self.logger.warning("⚠️ 无法获取应用详情")
                return False

            app_data = result.get(app_id, {})
            if not app_data.get("success"):
                self.logger.warning(f"⚠️ 应用 {app_id} 不存在或无法访问")
                return False

            # 获取应用名称
            self.app_name = app_data.get("data", {}).get("name", "Unknown")
            self.logger.info(f"📦 应用名称: {self.app_name}")

            # 获取DLC列表
            dlc_list = app_data.get("data", {}).get("dlc", [])
            if dlc_list:
                self.dlc_ids = [int(dlc_id) for dlc_id in dlc_list if str(dlc_id).isdigit()]
                self.logger.info(f"🎮 检测到 {len(self.dlc_ids)} 个DLC: {self.dlc_ids}")

            return True

        except Exception as e:
            self.logger.error(f"❌ 获取应用详情失败: {str(e)}")
            return False

    async def batch_fetch_dlc_details(self, dlc_ids: List[int]) -> dict:
        """批量获取DLC详情

        Args:
            dlc_ids: DLC ID列表

        Returns:
            {dlc_id: app_name} 字典
        """
        if not dlc_ids:
            return {}

        # 构建多个DLC的URL
        urls = [URLs.steam_app_details(str(dlc_id)) for dlc_id in dlc_ids]

        # 批量获取
        results = await self.api_client.batch_get(urls)

        dlc_names = {}
        for url, data in results.items():
            if data:
                # 从URL中提取DLC ID
                dlc_id = url.split("appids=")[-1]
                if dlc_id.isdigit():
                    app_data = data.get(dlc_id, {})
                    if app_data.get("success"):
                        app_name = app_data.get("data", {}).get("name", "Unknown")
                        dlc_names[int(dlc_id)] = app_name

        return dlc_names

    def clear(self):
        """清空数据"""
        self.app_id = ""
        self.app_name = None
        self.dlc_ids.clear()
