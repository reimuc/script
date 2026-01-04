"""Steam清单获取工具 - 高性能异步版本"""

import asyncio
import logging
import subprocess
import sys
import winreg
from argparse import ArgumentParser, Namespace
from pathlib import Path

from colorama import Fore, init
from colorlog import ColoredFormatter

from api_client import APIClient
from constant import (
    LOG_FORMAT,
    Steam,
    VERSION,
)
from file_processor import FileProcessor
from repository_manager import RepositoryManager
from steam_app_manager import SteamAppManager

# Initialize colorama
init(autoreset=True)


def show_banner():
    """显示应用欢迎banner"""
    print(
        rf"""
         ('-. .-.   ('-.  _  .-')   .-') _      ('-.     
        ( OO )  / _(  OO)( \( -O ) (  OO) )    ( OO ).-. 
        ,--. ,--.(,------.,------. /     '._   / . --. / 
        |  | |  | |  .---'|   /`. '|'--...__)  | \-.  \  
        |   .|  | |  |    |  /  | |'--.  .--'.-'-'  |  | 
        |       |(|  '--. |  |_.' |   |  |    \| |_.'  | 
        |  .-.  | |  `---.|  .  '.'   |  |     |  .-.  | 
        |  | |  | |  `---.|  |\  \    |  |     |  | |  | 
        `--' `--' `------'`--' '--'   `--'     `--' `--' 

        🚀 Steam清单获取工具 v{VERSION}
        💨 高性能异步版本
        """
    )


def init_logger(debug: bool = False) -> logging.Logger:
    """初始化日志系统"""
    logger = logging.getLogger(__name__)

    # 移除已有的处理器
    logger.handlers.clear()

    handler = logging.StreamHandler()
    formatter = ColoredFormatter(LOG_FORMAT)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    return logger


def init_command_args() -> Namespace:
    """初始化命令行参数"""
    parser = ArgumentParser(description="🚀 Steam 清单文件获取工具 v" + VERSION)
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s v{VERSION}")
    parser.add_argument("-a", "--appid", help="🎮 Steam 应用ID或名称")
    parser.add_argument("-k", "--key", help="🔑 GitHub API 访问密钥")
    parser.add_argument("-r", "--repo", help="📁 自定义 GitHub 仓库名称")
    parser.add_argument("-f", "--fixed", action="store_true", help="📌 启用固定清单模式")
    parser.add_argument("-d", "--debug", action="store_true", help="🔍 调试模式")
    return parser.parse_args()


def verify_steam_path() -> Path | None:
    """验证Steam安装路径"""
    try:
        hkey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, Steam.REG_PATH)
        steam_path = Path(winreg.QueryValueEx(hkey, Steam.REG_KEY)[0])

        if (steam_path / "steam.exe").exists():
            return steam_path

        return None
    except (FileNotFoundError, OSError):
        return None


async def main():
    """主程序入口"""
    show_banner()

    # 初始化
    args = init_command_args()
    logger = init_logger(args.debug)

    # 验证Steam路径
    steam_path = verify_steam_path()
    if not steam_path:
        logger.error("❌ 未找到Steam安装路径")
        return

    logger.info(f"🎮 已定位Steam安装路径: {steam_path}")

    # 创建异步客户端和管理器
    async with APIClient(logger) as api_client:
        try:
            # 初始化各个管理器
            file_processor = FileProcessor(logger)
            steam_app_manager = SteamAppManager(logger, api_client)
            repo_manager = RepositoryManager(logger, api_client, file_processor)

            # 检查API速率限制
            if not await repo_manager.check_rate_limit():
                logger.error("❌ API请求次数已达上限，请稍后再试")
                return

            # 获取应用ID
            app_query = args.appid or input(f"{Fore.CYAN}请输入游戏名称或ID: {Fore.RESET}")
            app_id = await steam_app_manager.search_app(app_query)

            if not app_id:
                logger.error("❌ 无法获取应用ID")
                return

            app_id_str = str(app_id)

            # 构建仓库列表
            custom_repos = [args.repo] if args.repo else None

            # 查找仓库
            repo = await repo_manager.find_repository(app_id_str, custom_repos)
            if not repo:
                logger.error(f"❌ 未找到包含应用 {app_id_str} 的仓库")
                return

            # 获取应用详情
            await steam_app_manager.fetch_app_details(app_id_str)

            # 获取文件列表
            files = await repo_manager.fetch_repository_files(repo, app_id_str)
            if not files:
                logger.error("❌ 无法获取仓库文件")
                return

            # 并发处理所有文件
            logger.info("⏳ 正在处理仓库文件...")
            success = await repo_manager.process_files(
                repo, app_id_str, files, steam_path
            )

            if not success:
                logger.warning("⚠️ 部分文件处理失败")

            # 保存配置
            save_success = await file_processor.save_lua_config(
                app_id_str,
                steam_app_manager.app_name,
                steam_path,
                args.fixed,
            )

            if save_success:
                logger.info(f"✅ 操作完成！应用: {steam_app_manager.app_name or app_id_str}")
            else:
                logger.error("❌ 保存配置失败")

            # 处理DLC
            if steam_app_manager.dlc_ids:
                logger.info(f"🎯 检测到 {len(steam_app_manager.dlc_ids)} 个DLC，正在处理...")

                # 创建新的处理实例处理DLC
                for dlc_id in steam_app_manager.dlc_ids:
                    dlc_id_str = str(dlc_id)
                    dlc_repo = await repo_manager.find_repository(dlc_id_str)

                    if dlc_repo:
                        dlc_files = await repo_manager.fetch_repository_files(dlc_repo, dlc_id_str)
                        if dlc_files:
                            # 为DLC创建新的文件处理器
                            dlc_processor = FileProcessor(logger)

                            # 保存DLC配置
                            await dlc_processor.save_lua_config(
                                dlc_id_str,
                                None,
                                steam_path,
                                args.fixed,
                            )
                            logger.info(f"✅ DLC {dlc_id_str} 处理完成")

        except KeyboardInterrupt:
            logger.warning("⚠️ 操作已被用户中断")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ 发生异常: {str(e)}")
            if args.debug:
                import traceback
                traceback.print_exc()
            sys.exit(1)

    # 完成提示
    if not args.appid:
        try:
            subprocess.call("pause", shell=True)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
