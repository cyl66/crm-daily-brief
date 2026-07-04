"""
CRM日报平台 - 企微推送模块
通过Webhook向企微群机器人推送日报Markdown卡片消息
"""
import json
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class Pusher:
    def __init__(self, config_path: str = "config/config.json"):
        with open(config_path) as f:
            self.config = json.load(f)
        self.webhook_url = self.config["wecom"]["webhook_url"]
        self.push_enabled = self.config["wecom"]["push_enabled"]
        self.push_days = self.config["wecom"]["push_days"]

    def should_push_today(self) -> bool:
        """判断今天是否应该推送"""
        if not self.push_enabled:
            return False
        if not self.webhook_url:
            logger.warning("未配置企微Webhook URL，跳过推送")
            return False
        today_weekday = datetime.now().isoweekday()
        return today_weekday in self.push_days

    def push(self, message: str) -> bool:
        """
        推送Markdown消息到企微群
        返回是否推送成功
        """
        if not self.should_push_today():
            logger.info("今日不是推送日或推送已禁用")
            return False

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "content": message
            }
        }

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10
            )
            result = resp.json()

            if result.get("errcode") == 0:
                logger.info("企微推送成功")
                return True
            else:
                logger.error(f"企微推送失败: {result}")
                return self._retry_push(payload)

        except Exception as e:
            logger.error(f"企微推送异常: {e}")
            return self._retry_push(payload)

    def _retry_push(self, payload: dict) -> bool:
        """推送失败重试1次"""
        import time
        logger.info("5秒后重试推送...")
        time.sleep(5)
        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            result = resp.json()
            success = result.get("errcode") == 0
            if success:
                logger.info("重试推送成功")
            else:
                logger.error(f"重试推送失败: {result}")
            return success
        except Exception as e:
            logger.error(f"重试推送异常: {e}")
            return False

    def test_webhook(self) -> dict:
        """测试Webhook是否可用"""
        test_msg = {
            "msgtype": "text",
            "text": {
                "content": "✅ CRM日报平台Webhook测试成功！推送配置正常。"
            }
        }
        try:
            resp = requests.post(self.webhook_url, json=test_msg, timeout=10)
            return resp.json()
        except Exception as e:
            return {"errcode": -1, "errmsg": str(e)}
