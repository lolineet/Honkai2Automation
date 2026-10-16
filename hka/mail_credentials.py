"""Keep saved SMTP credentials bound to the current Windows account."""
import base64
import ctypes
import os
from ctypes import wintypes


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if os.name != 'nt':
        raise ValueError('保存 SMTP 授权码需要 Windows；其他系统可使用密码环境变量')

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise ValueError('无法读取本机 SMTP 授权码，请重新填写并保存' if decrypt else '无法加密保存 SMTP 授权码')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        free = ctypes.WinDLL('kernel32').LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(target.data)


def protect_secret(secret: str) -> str:
    return base64.b64encode(_crypt(secret.encode('utf-8'), decrypt=False)).decode('ascii')


def unprotect_secret(value: str) -> str:
    try:
        return _crypt(base64.b64decode(value, validate=True), decrypt=True).decode('utf-8')
    except (ValueError, UnicodeError) as exc:
        raise ValueError('无法读取本机 SMTP 授权码，请重新填写并保存') from exc
