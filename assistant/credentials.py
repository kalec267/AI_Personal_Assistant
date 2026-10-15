"""API key storage encrypted for the current Windows user with DPAPI."""
import ctypes
import os
import uuid
from ctypes import wintypes
from pathlib import Path

from .core import UserError


def _crypt(data, decrypt=False):
    if os.name != "nt":
        raise UserError("키 암호화 저장은 Windows에서 지원합니다. 다른 OS에서는 환경 변수를 사용하세요.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output))
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(source), "Luna API key", None, None, None, 1, ctypes.byref(output))
    if not ok:
        raise UserError("API 키 암호화 저장소에 접근할 수 없습니다. 같은 Windows 사용자로 실행해 주세요.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class Credentials:
    def __init__(self, folder, provider="ollama"):
        if provider not in {"ollama", "openai"}:
            raise ValueError("Unknown credential provider")
        self.path = Path(folder) / f"{provider}-key.dpapi"
        self.env_names = (f"LUNA_{provider.upper()}_API_KEY", f"{provider.upper()}_API_KEY")
        self._session = ""

    def get(self):
        if self._session:
            return self._session
        key = next((os.environ[name] for name in self.env_names if os.environ.get(name)), "")
        if key:
            return key
        if self.path.exists():
            try:
                return _crypt(self.path.read_bytes(), decrypt=True).decode("utf-8")
            except (OSError, UnicodeError):
                raise UserError("저장된 API 키를 읽지 못했어요. AI 설정에서 다시 등록해 주세요.") from None
        return ""

    def status(self):
        if self._session:
            return "현재 세션에 등록됨"
        if any(os.environ.get(name) for name in self.env_names):
            return "환경 변수에 등록됨"
        return "이 Windows 사용자에 암호화 저장됨" if self.path.exists() else "등록된 키 없음"

    def set(self, key, persist=True):
        if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(c in key for c in "\r\n\0"):
            raise UserError("올바른 API 키를 입력해 주세요.")
        key = key.strip()
        if persist:
            encrypted = _crypt(key.encode("utf-8"))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f"key-{uuid.uuid4().hex}.tmp")
            try:
                with open(temporary, "xb") as output:
                    output.write(encrypted)
                temporary.replace(self.path)
            finally:
                temporary.unlink(missing_ok=True)
        self._session = key

    def delete(self):
        self._session = ""
        self.path.unlink(missing_ok=True)
