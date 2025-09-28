"""
AstrBot LLM定时触发插件
支持定时向指定用户/群组发送消息并触发LLM回复
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime
import croniter
import time

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
from astrbot.api.message_components import Plain, MessageChain


@register("llm_trigger", "Assistant", "LLM定时触发插件", "1.0.0")
class LLMTriggerPlugin(Star):
    """LLM定时触发插件"""
    
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        
        # 初始化配置
        self.config = config or {}
        
        # 获取配置参数
        self.scheduler_check_interval = getattr(self.config, "scheduler_check_interval", 30)
        self.platform_group_provider_map = getattr(self.config, "platform_group_provider_map", [])
        self.platform_friend_provider_map = getattr(self.config, "platform_friend_provider_map", [])
        self.admin_user_id = getattr(self.config, "admin_user_id", "admin")
        self.notification_on_failure = getattr(self.config, "notification_on_failure", True)
        self.notification_on_success = getattr(self.config, "notification_on_success", False)
        
        # 解析配置映射
        self.trigger_configs = []
        self._parse_configs()
        
        # 启动定时检查任务
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())
        
        logger.info(f"LLM定时触发插件已初始化，配置了 {len(self.trigger_configs)} 个触发器")
    
    async def terminate(self):
        """插件终止时的清理工作"""
        try:
            if hasattr(self, '_scheduler_task') and not self._scheduler_task.done():
                self._scheduler_task.cancel()
                try:
                    await self._scheduler_task
                except asyncio.CancelledError:
                    pass
            logger.info("LLM定时触发插件已安全卸载")
        except Exception as e:
            logger.error(f"插件终止时出错: {e}")
    
    def _parse_configs(self):
        """解析配置映射"""
        self.trigger_configs = []
        
        # 解析群组配置
        for config_str in self.platform_group_provider_map:
            try:
                parts = config_str.split("::")
                if len(parts) >= 5:
                    platform_name, group_id, provider_name, cron_expr = parts[0], parts[1], parts[2], parts[3]
                    message_content = "::".join(parts[4:])  # 支持消息内容包含::
                    
                    # 验证CRON表达式
                    try:
                        cron = croniter.croniter(cron_expr, datetime.now())
                        next_time = cron.get_next(datetime)
                        
                        self.trigger_configs.append({
                            "type": "group",
                            "platform": platform_name,
                            "target_id": group_id,
                            "provider": provider_name,
                            "cron_expression": cron_expr,
                            "cron": croniter.croniter(cron_expr, datetime.now()),
                            "next_run": next_time,
                            "last_run": None,
                            "message": message_content
                        })
                        
                        logger.info(f"已添加群组触发器: {platform_name}:{group_id}, CRON: {cron_expr}, 下次执行: {next_time}")
                        
                    except Exception as e:
                        logger.error(f"群组配置中CRON表达式无效: {cron_expr}, 错误: {e}")
                        
                else:
                    logger.warning(f"群组配置格式错误: {config_str}")
            except Exception as e:
                logger.error(f"解析群组配置失败: {config_str}, 错误: {e}")
        
        # 解析私聊配置
        for config_str in self.platform_friend_provider_map:
            try:
                parts = config_str.split("::")
                if len(parts) >= 5:
                    platform_name, user_id, provider_name, cron_expr = parts[0], parts[1], parts[2], parts[3]
                    message_content = "::".join(parts[4:])  # 支持消息内容包含::
                    
                    # 验证CRON表达式
                    try:
                        cron = croniter.croniter(cron_expr, datetime.now())
                        next_time = cron.get_next(datetime)
                        
                        self.trigger_configs.append({
                            "type": "private",
                            "platform": platform_name,
                            "target_id": user_id,
                            "provider": provider_name,
                            "cron_expression": cron_expr,
                            "cron": croniter.croniter(cron_expr, datetime.now()),
                            "next_run": next_time,
                            "last_run": None,
                            "message": message_content
                        })
                        
                        logger.info(f"已添加私聊触发器: {platform_name}:{user_id}, CRON: {cron_expr}, 下次执行: {next_time}")
                        
                    except Exception as e:
                        logger.error(f"私聊配置中CRON表达式无效: {cron_expr}, 错误: {e}")
                        
                else:
                    logger.warning(f"私聊配置格式错误: {config_str}")
            except Exception as e:
                logger.error(f"解析私聊配置失败: {config_str}, 错误: {e}")
    
    async def _scheduler_loop(self):
        """定时检查循环"""
        while True:
            try:
                await asyncio.sleep(self.scheduler_check_interval)
                await self._check_and_execute_triggers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器循环出错: {e}")
                await asyncio.sleep(self.scheduler_check_interval)
    
    async def _check_and_execute_triggers(self):
        """检查并执行触发器"""
        current_time = datetime.now()
        
        for config in self.trigger_configs:
            try:
                # 检查是否到了执行时间
                if current_time >= config["next_run"]:
                    logger.info(f"执行定时任务: {config['platform']}:{config['target_id']} - {config['cron_expression']}")
                    
                    # 执行这个触发器
                    await self._execute_trigger(config)
                    
                    # 更新下次执行时间
                    config["last_run"] = current_time
                    config["cron"] = croniter.croniter(config["cron_expression"], current_time)
                    config["next_run"] = config["cron"].get_next(datetime)
                    
                    logger.info(f"定时任务执行完成，下次执行: {config['next_run']}")
                    
                    if self.notification_on_success:
                        await self._send_notification(f"✅ 定时LLM触发任务执行成功: {config['platform']}:{config['target_id']}", "success")
                        
            except Exception as e:
                logger.error(f"执行定时任务失败: {e}")
                if self.notification_on_failure:
                    await self._send_notification(f"❌ 定时LLM触发任务执行失败: {e}", "error")
    
    async def _execute_trigger(self, config: dict):
        """执行单个触发器"""
        try:
            platform_name = config["platform"]
            target_id = config["target_id"]
            provider_name = config["provider"]
            message_content = config["message"]
            message_type = config["type"]
            
            # 构造unified_msg_origin
            if message_type == "group":
                unified_msg_origin = f"{platform_name}:group_message:{target_id}"
            else:
                unified_msg_origin = f"{platform_name}:private_message:{target_id}"
            
            # 获取指定的LLM提供商
            provider = self.context.get_provider_by_id(provider_name)
            if not provider:
                logger.warning(f"未找到LLM提供商: {provider_name}")
                return
            
            # 调用LLM获取回复
            llm_response = await provider.text_chat(
                prompt=message_content,
                context=[],  # 可以根据需要添加上下文
                system_prompt="你是一个有用的AI助手。"
            )
            
            if llm_response and llm_response.result_chain:
                # 发送LLM回复
                await self.context.send_message(unified_msg_origin, llm_response.result_chain)
                logger.info(f"成功发送LLM回复到 {platform_name}:{target_id}")
            else:
                logger.warning(f"LLM提供商 {provider_name} 没有返回有效回复")
                    
        except Exception as e:
            logger.error(f"执行触发器失败: {e}")
    
    async def _send_notification(self, message: str, level: str = "info"):
        """发送通知给管理员"""
        try:
            if self.admin_user_id == "admin":
                logger.info(f"通知: {message}")
                return
                
            # 这里可以实现向管理员发送通知的逻辑
            # 需要根据实际的管理员配置来发送消息
            logger.info(f"向管理员 {self.admin_user_id} 发送通知: {message}")
            
        except Exception as e:
            logger.error(f"发送通知失败: {e}")
    
    @filter.command("llm_trigger")
    async def llm_trigger_command(self, event: AstrMessageEvent):
        """LLM触发器管理命令"""
        trigger_info = []
        for config in self.trigger_configs:
            trigger_info.append(f"- {config['platform']}:{config['target_id']} ({config['cron_expression']}) -> {config['next_run']}")
        
        yield event.plain_result(
            f"📝 LLM定时触发插件状态\n\n"
            f"📊 统计信息:\n"
            f"- 触发器配置: {len(self.trigger_configs)} 个\n"
            f"- 检查间隔: {self.scheduler_check_interval} 秒\n\n"
            f"⏰ 触发器列表:\n" +
            ("\n".join(trigger_info) if trigger_info else "暂无配置的触发器")
        )
    
    @filter.command("llm_trigger_test")
    async def test_trigger(self, event: AstrMessageEvent):
        """测试触发器（仅管理员）"""
        sender_id = event.get_sender_id()
        if sender_id != self.admin_user_id:
            yield event.plain_result("❌ 只有管理员可以执行测试")
            return
            
        yield event.plain_result("🔄 开始测试所有触发器...")
        
        success_count = 0
        total_count = len(self.trigger_configs)
        
        for config in self.trigger_configs:
            try:
                await self._execute_trigger(config)
                success_count += 1
            except Exception as e:
                logger.error(f"测试触发器失败: {e}")
        
        yield event.plain_result(
            f"✅ 触发器测试完成\n"
            f"成功: {success_count}/{total_count}"
        )