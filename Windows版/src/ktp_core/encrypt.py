from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import base64


class AESCipher:
    def __init__(self, key: str, iv: str, padding_style: str = "pkcs7"):
        self.block_size = AES.block_size
        self.key = self._process_key(key)
        self.iv = self._process_iv(iv)
        self.style = padding_style

    @staticmethod
    def _process_key(key_str: str) -> bytes:
        key_bytes = key_str.encode("utf-8")
        if len(key_bytes) != 16:
            raise ValueError("密钥必须为16字节长度")
        return key_bytes

    @staticmethod
    def _process_iv(iv_str: str) -> bytes:
        iv_bytes = iv_str.encode("utf-8")
        if len(iv_bytes) != 16:
            raise ValueError("IV必须为16字节长度")
        return iv_bytes

    def encrypt(self, plaintext: str) -> str:
        plain_bytes = plaintext.encode("utf-8")
        padded_data = pad(plain_bytes, self.block_size, self.style)
        cipher = AES.new(self.key, AES.MODE_CBC, iv=self.iv)
        cipher_bytes = cipher.encrypt(padded_data)
        return base64.b64encode(cipher_bytes).decode("utf-8")


def encrypt_password(plaintext: str) -> str:
    encryptor = AESCipher("ktp4567890123456", "ktp4567890123456", "pkcs7")
    return encryptor.encrypt(plaintext)
