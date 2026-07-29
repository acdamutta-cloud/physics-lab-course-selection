import logging

logger = logging.getLogger(__name__)


class SmsProvider:
    """SMS 提供者抽象基类"""

    async def send(self, phone: str, code: str) -> bool:
        raise NotImplementedError


class MockSmsProvider(SmsProvider):
    """开发环境：验证码打印到日志和控制台"""

    async def send(self, phone: str, code: str) -> bool:
        msg = (
            "\n"
            "=" * 50 + "\n"
            f"  [短信验证码 Mock] 手机号: {phone}\n"
            f"  验证码: {code}\n"
            "=" * 50 + "\n"
        )
        print(msg)
        logger.info("Mock SMS: code=%s -> phone=%s", code, phone)
        return True


def get_sms_provider() -> SmsProvider:
    return MockSmsProvider()
