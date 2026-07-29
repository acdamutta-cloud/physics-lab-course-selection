def sms_code_key(phone: str) -> str:
    """验证码缓存键"""
    return f"sms:code:{phone}"


def sms_limit_key(phone: str) -> str:
    """发送频率限制缓存键"""
    return f"sms:limit:{phone}"
