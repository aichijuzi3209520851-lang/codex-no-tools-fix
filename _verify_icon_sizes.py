# -*- coding: utf-8 -*-
"""用 Explorer 同一套 API 取快捷方式全部尺寸的图标，逐个判定是否回退成白纸。

为什么需要它：`SHGetFileInfoW` 只验证 16/32px 两档，而图标在 48/64/96/128/256px
走的是另一条缩放路径。快捷方式曾因为承载图标的目录带低完整性标签而整条退成
白纸，那种情况下 API 层"能取到图标"依然成立，只有比对像素颜色才能发现。

用法：python _verify_icon_sizes.py ["D:\桌面文件\某个.lnk"]
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys

ole32 = ctypes.windll.ole32
shell32 = ctypes.windll.shell32
gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32


# --- 关键：必须先绑定 argtypes，否则 64 位句柄溢出 ---
gdi32.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
gdi32.GetObjectW.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wt.UINT]
gdi32.GetDIBits.restype = ctypes.c_int
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteObject.restype = wt.BOOL


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wt.LONG), ("cy", wt.LONG)]


class BITMAP(ctypes.Structure):
    _fields_ = [("bmType", wt.LONG), ("bmWidth", wt.LONG), ("bmHeight", wt.LONG),
                ("bmWidthBytes", wt.LONG), ("bmPlanes", wt.WORD),
                ("bmBitsPixel", wt.WORD), ("bmBits", ctypes.c_void_p)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


CLSID_ShellItem = GUID(0x43826D1E, 0xE718, 0x42EE,
                       (ctypes.c_ubyte * 8)(0xBC, 0x55, 0xA1, 0xE2,
                                            0x61, 0xC3, 0x7B, 0xFE))
IID_ImageFactory = GUID(0xBCC18B79, 0xBA16, 0x442F,
                        (ctypes.c_ubyte * 8)(0x80, 0xC4, 0x8A, 0x59,
                                             0xC3, 0x0C, 0x46, 0x3B))
SIIGBF_ICONONLY = 0x4

shell32.SHCreateItemFromParsingName.argtypes = [
    wt.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
shell32.SHCreateItemFromParsingName.restype = ctypes.c_long


def shell_image(path, size):
    """返回 (w, h, pixels_bgra)；失败返回 (None, err)。"""
    pitem = ctypes.c_void_p()
    hr = shell32.SHCreateItemFromParsingName(wt.LPCWSTR(path), None,
                                             ctypes.byref(CLSID_ShellItem),
                                             ctypes.byref(pitem))
    if hr != 0:
        return None, "SHCreateItemFromParsingName hr=0x%08X" % hr
    pfactory = ctypes.c_void_p()
    hr = ctypes.cast(pitem, ctypes.POINTER(ctypes.c_void_p))[0]
    # vtable: 0=QI 1=AddRef 2=Release 3=BindToHandler 4=GetParent
    # 5=GetDisplayName 6=GetAttributes 7=Compare
    vti = ctypes.cast(pitem, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    fn = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                            ctypes.POINTER(GUID),
                            ctypes.POINTER(ctypes.c_void_p))(vti[0][0])
    hr = fn(pitem, ctypes.byref(IID_ImageFactory), ctypes.byref(pfactory))
    if hr != 0:
        return None, "QueryInterface(ImageFactory) hr=0x%08X" % hr
    vtf = ctypes.cast(pfactory, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    # IShellItemImageFactory: 0=QI 1=AddRef 2=Release 3=GetImage
    getimage = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, SIZE,
                                 ctypes.c_int, ctypes.POINTER(wt.HBITMAP))(
        vtf[0][3])
    hbm = wt.HBITMAP()
    hr = getimage(pfactory, SIZE(size, size), SIIGBF_ICONONLY, ctypes.byref(hbm))
    if hr != 0 or not hbm:
        return None, "GetImage hr=0x%08X" % hr
    bm = BITMAP()
    if not gdi32.GetObjectW(hbm, ctypes.sizeof(bm), ctypes.byref(bm)):
        return None, "GetObjectW failed"
    w, h = bm.bmWidth, bm.bmHeight
    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = w
    bi.biHeight = -h          # 负数 = top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0      # BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    hdc = user32.GetDC(None)
    n = gdi32.GetDIBits(hdc, hbm, 0, h, buf, ctypes.byref(bi), 0)
    user32.ReleaseDC(None, hdc)
    gdi32.DeleteObject(hbm)
    if n == 0:
        return None, "GetDIBits returned 0"
    return (w, h, bytearray(buf.raw)), ""


def stats(w, h, px):
    """统计近白占比、平均亮度、平均 alpha（预乘 BGRA）。"""
    white = lum_sum = a_sum = 0.0
    n = w * h
    for i in range(0, n * 4, 4):
        b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        lum = 0.114 * b + 0.587 * g + 0.299 * r
        lum_sum += lum
        a_sum += a
        if r >= 235 and g >= 235 and b >= 235 and a >= 200:
            white += 1
    return white / n, lum_sum / n, a_sum / n / 255.0


def desktop_path():
    """真实桌面目录（会被重定向，例如 D:\桌面文件）。"""
    buf = ctypes.create_unicode_buffer(1024)
    # CSIDL_DESKTOPDIRECTORY = 0x0010
    if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Desktop")


def main():
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        path = os.path.join(desktop_path(), "Codex 工具修复.lnk")
    ole32.CoInitialize(None)
    print("\n--- %s" % path)
    for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 256):
        res, err = shell_image(path, size)
        if res is None:
            print("  %4dpx  失败  %s" % (size, err))
            continue
        w, h, px = res
        wr, lum, alpha = stats(w, h, px)
        verdict = "白纸 <<<" if wr > 0.35 else "深色图标 OK"
        print("  %4dpx  实际=%dx%d  white=%.3f  lum=%5.1f  alpha=%.3f  %s"
              % (size, w, h, wr, lum, alpha, verdict))
    ole32.CoUninitialize()


if __name__ == "__main__":
    main()
