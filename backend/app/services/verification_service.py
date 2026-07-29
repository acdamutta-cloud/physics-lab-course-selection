import random

from app.cache.keys import sms_code_key, sms_limit_key
from app.core.config.settings import get_settings
from app.db.redis_client import get_redis_client
from app.services.sms_service import get_sms_provider

settings = get_settings()


def _generate_code() -> str:
    return f"{random.randint(0, 999999):06d}"


async def send_verification_code(phone: str) -> bool:
    """发送验证码。频率限制触发时返回 False。"""
    redis = get_redis_client()

    # 频率限制检查
    limit_exists = await redis.exists(sms_limit_key(phone))
    if limit_exists:
        return False

    code = _generate_code()

    # 存验证码 (TTL 5 分钟)
    await redis.setex(sms_code_key(phone), settings.sms_code_ttl_seconds, code)

    # 设频率限制 (60 秒内不可重复发送)
    await redis.setex(sms_limit_key(phone), settings.sms_send_interval_seconds, "1")

    # 调用 SMS 提供者发送
    provider = get_sms_provider()
    await provider.send(phone, code)

    return True


async def verify_code(phone: str, code: str) -> bool:
    """验证验证码。成功则删除 Redis 中的码。"""
    redis = get_redis_client()
    stored = await redis.get(sms_code_key(phone))
    if stored is None:
        return False
    if stored != code:
        return False
    # 验证成功，删除已使用的验证码
    await redis.delete(sms_code_key(phone))
    return True
