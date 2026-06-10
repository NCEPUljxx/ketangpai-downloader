import base64
from ktp_core._pyaes import AESModeOfOperationCBC


def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


class AESCipher:
    def __init__(self, key: str, iv: str):
        key_bytes = key.encode("utf-8")
        iv_bytes = iv.encode("utf-8")
        if len(key_bytes) != 16:
            raise ValueError("密钥必须为16字节长度")
        if len(iv_bytes) != 16:
            raise ValueError("IV必须为16字节长度")
        self.key = key_bytes
        self.iv = iv_bytes

    def encrypt(self, plaintext: str) -> str:
        plain_bytes = plaintext.encode("utf-8")
        padded = _pkcs7_pad(plain_bytes, 16)
        aes = AESModeOfOperationCBC(self.key, iv=self.iv)
        cipher_bytes = b""
        for i in range(0, len(padded), 16):
            cipher_bytes += aes.encrypt(padded[i:i+16])
        return base64.b64encode(cipher_bytes).decode("utf-8")


def encrypt_password(plaintext: str) -> str:
    encryptor = AESCipher("ktp4567890123456", "ktp4567890123456")
    return encryptor.encrypt(plaintext)
