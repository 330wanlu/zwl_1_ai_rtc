"""
RTC Token 生成模块
从 Node.js token.js 移植而来
"""
import base64
import hmac
import hashlib
import random
import struct
import time
from typing import Dict, Optional


VERSION = "001"
VERSION_LENGTH = 3
APP_ID_LENGTH = 24

# 与 Node token.js 一致：模块加载时生成一次，所有 Token 实例共享
_random_int = random.randint(0, 0xFFFFFFFF)

privileges = {
    "PrivPublishStream": 0,
    "privPublishAudioStream": 1,
    "privPublishVideoStream": 2,
    "privPublishDataStream": 3,
    "PrivSubscribeStream": 4,
}


class AccessToken:
    """RTC 访问令牌"""

    def __init__(self, app_id: str, app_key: str, room_id: str, user_id: str):
        self.app_id = app_id
        self.app_key = app_key
        self.room_id = room_id
        self.user_id = user_id
        self.issued_at = int(time.time())
        self.nonce = _random_int
        self.expire_at = 0
        self.privileges: Dict[int, int] = {}
        self.signature: Optional[bytes] = None

    def add_privilege(self, privilege: int, expire_timestamp: int):
        self.privileges[privilege] = expire_timestamp
        if privilege == privileges["PrivPublishStream"]:
            self.privileges[privileges["privPublishVideoStream"]] = expire_timestamp
            self.privileges[privileges["privPublishAudioStream"]] = expire_timestamp
            self.privileges[privileges["privPublishDataStream"]] = expire_timestamp

    def expire_time(self, expire_timestamp: int):
        self.expire_at = expire_timestamp

    def _pack_msg(self) -> bytes:
        buf = bytearray()
        buf.extend(struct.pack("<I", self.nonce))
        buf.extend(struct.pack("<I", self.issued_at))
        buf.extend(struct.pack("<I", self.expire_at))
        buf.extend(self._pack_string(self.room_id))
        buf.extend(self._pack_string(self.user_id))
        buf.extend(self._pack_tree_map_uint32(self.privileges))
        return bytes(buf)

    def _pack_string(self, s: str) -> bytes:
        encoded = s.encode("utf-8")
        return struct.pack("<H", len(encoded)) + encoded

    def _pack_tree_map_uint32(self, map_data: Dict[int, int]) -> bytes:
        if not map_data:
            return struct.pack("<H", 0)

        buf = bytearray()
        buf.extend(struct.pack("<H", len(map_data)))
        for key, value in map_data.items():
            buf.extend(struct.pack("<H", key))
            buf.extend(struct.pack("<I", value))
        return bytes(buf)

    def serialize(self) -> str:
        msg = self._pack_msg()
        signature = self._encode_hmac(self.app_key, msg)
        content = self._pack_bytes(msg) + self._pack_bytes(signature)
        return VERSION + self.app_id + base64.b64encode(content).decode("utf-8")

    def _pack_bytes(self, data: bytes) -> bytes:
        return struct.pack("<H", len(data)) + data

    def _encode_hmac(self, key: str, message: bytes) -> bytes:
        return hmac.new(key.encode("utf-8"), message, hashlib.sha256).digest()


def parse_token(raw: str) -> Optional[AccessToken]:
    try:
        if len(raw) <= VERSION_LENGTH + APP_ID_LENGTH:
            return None
        if raw[:VERSION_LENGTH] != VERSION:
            return None

        token = AccessToken("", "", "", "")
        token.app_id = raw[VERSION_LENGTH:VERSION_LENGTH + APP_ID_LENGTH]

        content_buf = base64.b64decode(raw[VERSION_LENGTH + APP_ID_LENGTH:])

        pos = 0
        msg_len = struct.unpack("<H", content_buf[pos:pos + 2])[0]
        pos += 2
        msg = content_buf[pos:pos + msg_len]
        pos += msg_len
        sig_len = struct.unpack("<H", content_buf[pos:pos + 2])[0]
        pos += 2
        token.signature = content_buf[pos:pos + sig_len]

        msg_pos = 0
        token.nonce = struct.unpack("<I", msg[msg_pos:msg_pos + 4])[0]
        msg_pos += 4
        token.issued_at = struct.unpack("<I", msg[msg_pos:msg_pos + 4])[0]
        msg_pos += 4
        token.expire_at = struct.unpack("<I", msg[msg_pos:msg_pos + 4])[0]
        msg_pos += 4

        room_len = struct.unpack("<H", msg[msg_pos:msg_pos + 2])[0]
        msg_pos += 2
        token.room_id = msg[msg_pos:msg_pos + room_len].decode("utf-8")
        msg_pos += room_len

        user_len = struct.unpack("<H", msg[msg_pos:msg_pos + 2])[0]
        msg_pos += 2
        token.user_id = msg[msg_pos:msg_pos + user_len].decode("utf-8")
        msg_pos += user_len

        priv_len = struct.unpack("<H", msg[msg_pos:msg_pos + 2])[0]
        msg_pos += 2
        token.privileges = {}
        for _ in range(priv_len):
            key = struct.unpack("<H", msg[msg_pos:msg_pos + 2])[0]
            msg_pos += 2
            value = struct.unpack("<I", msg[msg_pos:msg_pos + 4])[0]
            msg_pos += 4
            token.privileges[key] = value

        return token
    except Exception as err:
        print(err)
        return None
