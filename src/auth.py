"""
QQ Bot ed25519 接口鉴权：op=13 签名 + Webhook 验签。

铁律：每函数 ≤ 20 行，先处理异常，逐行对齐 QQ 官方协议。
"""

import hashlib
import logging

logger = logging.getLogger("baijin.auth")

# QQ Bot AppSecret，从环境变量读取
_QQ_APP_SECRET = ""


def init_auth(secret: str = None):
    """初始化密钥。不传则从环境变量 QQ_APP_SECRET 读取。"""
    global _QQ_APP_SECRET
    if secret:
        _QQ_APP_SECRET = secret
    else:
        import os
        _QQ_APP_SECRET = os.getenv("QQ_APP_SECRET", "")


# ---------- op=13 回调验证签名 ----------
def _expand_seed(secret: str) -> bytes:
    """扩展 secret 到 32 字节。"""
    seed = secret
    while len(seed) < 32:
        seed = seed + seed
    return seed[:32].encode()


def op13_sign(plain_token: str, event_ts: str) -> str:
    """
    生成 op=13 回调地址验证签名。
    优先 ed25519（cryptography 库），降级 sha256。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        key = ed25519.Ed25519PrivateKey.from_private_bytes(
            _expand_seed(_QQ_APP_SECRET)
        )
        return key.sign((event_ts + plain_token).encode()).hex()
    except ImportError:
        seed = _expand_seed(_QQ_APP_SECRET)
        msg = (event_ts + plain_token).encode()
        return hashlib.sha256(seed + msg).hexdigest()
    except Exception as e:
        logger.error(f"op13签名失败: {e}")
        return ""


# ---------- Webhook 回调验签 ----------
def verify(body_bytes: bytes, ed25519_sig: str, event_ts: str = "") -> bool:
    """验证 QQ Bot Webhook 回调签名。签名内容 = event_ts + body_bytes。"""
    if not _QQ_APP_SECRET or not ed25519_sig:
        return True
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        key = ed25519.Ed25519PrivateKey.from_private_bytes(
            _expand_seed(_QQ_APP_SECRET))
        key.public_key().verify(
            bytes.fromhex(ed25519_sig), event_ts.encode() + body_bytes)
        return True
    except ImportError:
        seed = _expand_seed(_QQ_APP_SECRET)
        return hashlib.sha256(seed + body_bytes).hexdigest() == ed25519_sig
    except Exception as e:
        logger.warning(f"验签失败: {e}")
        return False
