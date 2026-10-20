#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
===============================================================================
 创建桌面快捷方式（带自定义图标）
===============================================================================

.bat 在资源管理器里只能显示系统默认图标——这是 Windows 的固定行为，改不了。
要让它在桌面上是自定义图标，唯一正规做法是建一个 .lnk，把 IconLocation
指向我们的 .ico。

本脚本做的事：
  1. 把图标安装到 %LOCALAPPDATA%\\CodexToolFix\\ （不放在脚本目录）
  2. 找到真实桌面目录（不一定是 %USERPROFILE%\\Desktop，桌面可能被重定向）
  3. 在桌面创建/覆盖 "Codex 工具修复.lnk"，IconLocation 指向安装后的图标
  4. 四步验收：
     a. 回读 .lnk 属性，确认目标 / 起始位置 / 图标路径写对了
     b. SHGetFileInfoW 拿到 Windows 真正分配给这个 .lnk 的 HICON，
        再用 GetIconInfo + GetDIBits 取像素，与 .ico 里同尺寸那一帧逐像素比对
        —— 这一步才能证明"系统真的用了我们的图标"，而不是静默回退到默认
     c. 确认目标 .bat 存在，快捷方式不是断链

为什么图标要单独安装到 %LOCALAPPDATA%：
  如果脚本目录位于带「低完整性标签(Low Mandatory Level)」的目录树里
  （很多沙箱、容器、受限工作区会这么做），那么在该目录里生成的文件都会
  继承这个标签。文件本身仍然可读、.ico 结构也完全正确，但 Windows shell
  的图标提取管道遇到 Low 标签文件会失败，于是快捷方式静默回退成通用的
  「白纸」图标。安装到 %LOCALAPPDATA% 可以拿到干净标签，避开这个坑。

复现：python _make_shortcut.py
===============================================================================
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
BAT_NAME = "双击运行-修复Codex工具.bat"
ICO_SRC = os.path.join(HERE, "assets", "codex-tool-fix.ico")
LNK_NAME = "Codex 工具修复.lnk"
DESC = "一键修复 Codex Desktop 模型拿不到文件/终端工具的问题"

# 图标安装位置。绝不能放在脚本目录里——见文件头的说明：
# 若脚本目录带低完整性标签，图标会被 shell 的图标提取管道拒绝，快捷方式退成白纸。
ICON_DIR = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
    "CodexToolFix",
)
ICO = os.path.join(ICON_DIR, "codex-tool-fix.ico")


# ----------------------------------------------------------------------------
# 控制台 UTF-8（否则打印 PowerShell 的错误文本会抛 UnicodeEncodeError）
# ----------------------------------------------------------------------------

def ensure_utf8_console():
    if os.name != "nt":
        return
    try:
        k32 = ctypes.windll.kernel32
        k32.SetConsoleOutputCP(65001)
        k32.SetConsoleCP(65001)
    except Exception:  # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------------
# 真实桌面路径
# ----------------------------------------------------------------------------

def desktop_path():
    """优先用 Shell 的 Desktop 已知文件夹（会跟随用户重定向）。"""
    probe = os.path.join(os.environ.get("TEMP", "."), "_desk_path.txt")
    ps = ("$p = [Environment]::GetFolderPath('Desktop');"
          "[IO.File]::WriteAllText($env:OUT, $p, (New-Object Text.UTF8Encoding($false)))")
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, env=dict(os.environ, OUT=probe),
                       timeout=90)
        with open(probe, "r", encoding="utf-8-sig") as fh:
            p = fh.read().strip()
        os.remove(probe)
        if p and os.path.isdir(p):
            return p
    except Exception:  # noqa: BLE001
        pass

    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "Desktop")
        val = os.path.expandvars(val)
        if os.path.isdir(val):
            return val
    except Exception:  # noqa: BLE001
        pass

    return os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")


# ----------------------------------------------------------------------------
# 创建 / 回读 .lnk
# ----------------------------------------------------------------------------

def create_lnk(lnk_path, target, icon, workdir, desc):
    ps = r"""
$ErrorActionPreference = 'Stop'
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK)
$s.TargetPath       = $env:TGT
$s.WorkingDirectory = $env:WRK
$s.IconLocation     = $env:ICO + ',0'
$s.Description      = $env:DSC
$s.WindowStyle      = 1
$s.Save()
"""
    env = dict(os.environ, LNK=lnk_path, TGT=target,
               WRK=workdir, ICO=icon, DSC=desc)
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, env=env, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode("utf-8", "replace")[:800])
    return os.path.isfile(lnk_path)


def read_lnk(lnk_path):
    out = os.path.join(os.environ.get("TEMP", "."), "_lnk_read.json")
    ps = r"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK)
$o = [ordered]@{
  target  = $s.TargetPath
  workdir = $s.WorkingDirectory
  icon    = $s.IconLocation
  desc    = $s.Description
}
[IO.File]::WriteAllText($env:OUT, ($o | ConvertTo-Json -Compress),
                        (New-Object Text.UTF8Encoding($false)))
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, env=dict(os.environ, LNK=lnk_path, OUT=out),
                   timeout=120)
    with open(out, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    os.remove(out)
    return data


# ----------------------------------------------------------------------------
# 取 Windows 实际分配的图标像素
# ----------------------------------------------------------------------------

class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wt.BOOL), ("xHotspot", wt.DWORD), ("yHotspot", wt.DWORD),
                ("hbmMask", wt.HBITMAP), ("hbmColor", wt.HBITMAP)]


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


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [("hIcon", wt.HICON), ("iIcon", ctypes.c_int),
                ("dwAttributes", wt.DWORD),
                ("szDisplayName", ctypes.c_wchar * 260),
                ("szTypeName", ctypes.c_wchar * 80)]


SHGFI_ICON = 0x00000100
SHGFI_LARGEICON = 0x00000000
SHGFI_SMALLICON = 0x00000001


_GDI_READY = False


def _gdi_init():
    """显式声明 64 位下的指针签名，否则 HANDLE/HDC 会被截断成 int 溢出。"""
    global _GDI_READY
    if _GDI_READY:
        return
    u, g = ctypes.windll.user32, ctypes.windll.gdi32

    u.GetDC.argtypes = [wt.HWND]
    u.GetDC.restype = ctypes.c_void_p
    u.ReleaseDC.argtypes = [wt.HWND, ctypes.c_void_p]
    u.ReleaseDC.restype = ctypes.c_int
    u.GetIconInfo.argtypes = [wt.HICON, ctypes.POINTER(ICONINFO)]
    u.GetIconInfo.restype = wt.BOOL
    u.DestroyIcon.argtypes = [wt.HICON]
    u.DestroyIcon.restype = wt.BOOL

    g.GetObjectW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p]
    g.GetObjectW.restype = ctypes.c_int
    g.GetDIBits.argtypes = [ctypes.c_void_p, wt.HANDLE, wt.UINT, wt.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO),
                            wt.UINT]
    g.GetDIBits.restype = ctypes.c_int
    g.DeleteObject.argtypes = [wt.HANDLE]
    g.DeleteObject.restype = wt.BOOL

    ctypes.windll.shell32.SHGetFileInfoW.argtypes = [
        wt.LPCWSTR, wt.DWORD, ctypes.POINTER(SHFILEINFOW), wt.UINT, wt.UINT]
    ctypes.windll.shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    _GDI_READY = True


def _hicon_rgba(hicon):
    """HICON -> PIL RGBA。"""
    _gdi_init()
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

    ii = ICONINFO()
    if not user32.GetIconInfo(hicon, ctypes.byref(ii)):
        return None, "GetIconInfo failed"

    bm = BITMAP()
    gdi32.GetObjectW(ii.hbmColor, ctypes.sizeof(bm), ctypes.byref(bm))
    w, h = int(bm.bmWidth), int(bm.bmHeight)
    if w <= 0 or h <= 0:
        gdi32.DeleteObject(ii.hbmColor)
        gdi32.DeleteObject(ii.hbmMask)
        return None, "bad bitmap size %dx%d" % (w, h)

    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0      # BI_RGB

    buf = (ctypes.c_ubyte * (w * h * 4))()
    hdc = user32.GetDC(None)
    got = gdi32.GetDIBits(hdc, ii.hbmColor, 0, h, buf,
                          ctypes.byref(bmi), 0) if hdc else 0
    if hdc:
        user32.ReleaseDC(None, hdc)
    gdi32.DeleteObject(ii.hbmColor)
    gdi32.DeleteObject(ii.hbmMask)
    if not got:
        return None, "GetDIBits returned 0"

    arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)
    return Image.fromarray(arr[:, :, [2, 1, 0, 3]].copy(), "RGBA"), ""


def shell_icon_of(path, small=False):
    """返回 (PIL.Image, 实际尺寸描述, 错误)。"""
    info = SHFILEINFOW()
    flags = SHGFI_ICON | (SHGFI_SMALLICON if small else SHGFI_LARGEICON)
    res = ctypes.windll.shell32.SHGetFileInfoW(
        path, 0, ctypes.byref(info), ctypes.sizeof(info), flags)
    if not res or not info.hIcon:
        return None, None, "SHGetFileInfoW failed"
    try:
        img, err = _hicon_rgba(info.hIcon)
    finally:
        ctypes.windll.user32.DestroyIcon(info.hIcon)
    if img is None:
        return None, None, err
    return img, (img.width, img.height), ""


# ----------------------------------------------------------------------------
# 比对
# ----------------------------------------------------------------------------

def ico_entry(size):
    im = Image.open(ICO)
    im.size = (size, size)
    return im.convert("RGBA")


def compare(shell_img, want_img):
    """比较两张图；尺寸不同则先缩放到同尺寸。返回 (MAD, 说明)。"""
    if shell_img.size != want_img.size:
        return None, "尺寸不同 shell=%s icon=%s" % (shell_img.size, want_img.size)
    a = np.asarray(shell_img, dtype=np.float32)
    b = np.asarray(want_img, dtype=np.float32)
    diff = np.abs(a - b).mean()
    return float(diff), ""


# ----------------------------------------------------------------------------
# 完整性标签（Low Mandatory Level 会让 shell 图标提取失败，必须检查）
# ----------------------------------------------------------------------------

LABEL_SECURITY_INFORMATION = 0x00000010
SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11
SE_FILE_OBJECT = 1
INTEGRITY_NAMES = {
    0x0000: "Untrusted",
    0x1000: "Low",
    0x2000: "Medium",
    0x2100: "Medium-Plus",
    0x3000: "High",
    0x4000: "System",
}


def integrity_rid(path):
    """返回文件对象完整性标签的 RID（0x1000=Low …）；无标签返回 None。"""
    adv = ctypes.windll.advapi32
    adv.GetNamedSecurityInfoW.argtypes = [
        wt.LPWSTR, ctypes.c_int, wt.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    adv.GetNamedSecurityInfoW.restype = wt.DWORD

    psd = ctypes.c_void_p()
    psacl = ctypes.c_void_p()
    rc = adv.GetNamedSecurityInfoW(path, SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION,
                                   None, None, None, ctypes.byref(psacl),
                                   ctypes.byref(psd))
    if rc != 0 or not psacl:
        return None
    try:
        # ACL: AclRevision(1) Sbz1(1) AclSize(2) AceCount(2) Sbz2(2)
        buf = ctypes.string_at(psacl.value, 8)
        ace_count = int.from_bytes(buf[4:6], "little")
        offset = 8
        for _ in range(ace_count):
            hdr = ctypes.string_at(psacl.value + offset, 4)
            ace_type = hdr[0]
            ace_size = int.from_bytes(hdr[2:4], "little")
            if ace_type == SYSTEM_MANDATORY_LABEL_ACE_TYPE:
                # ACE: type(1) flags(1) size(2) mask(4) sid...
                sid = psacl.value + offset + 8
                sub_cnt = ctypes.string_at(sid + 1, 1)[0]
                # SID: rev(1) count(1) auth(6) subauth[]
                last = ctypes.string_at(sid + 8 + (sub_cnt - 1) * 4, 4)
                return int.from_bytes(last, "little")
            if ace_size <= 0:
                break
            offset += ace_size
    finally:
        ctypes.windll.kernel32.LocalFree(psd)
    return None


def install_icon():
    """把图标复制到 %LOCALAPPDATA%，避开工作区目录树上的低完整性标签。"""
    os.makedirs(ICON_DIR, exist_ok=True)
    shutil.copy2(ICO_SRC, ICO)
    rid = integrity_rid(ICO)
    name = INTEGRITY_NAMES.get(rid, "RID=0x%04X" % rid if rid is not None else "无标签")
    return rid, name


def main():
    ensure_utf8_console()

    print("=" * 70)
    print(" 创建桌面快捷方式")
    print("=" * 70)

    ok = True
    bat = os.path.join(HERE, BAT_NAME)
    if not os.path.isfile(bat):
        print("  启动脚本 缺失 <<< %s" % bat)
        print("\nRESULT: FAILED（缺少启动脚本）")
        return 1
    print("  启动脚本 : %s" % bat)
    if not os.path.isfile(ICO_SRC):
        print("  图标源   缺失 <<< %s" % ICO_SRC)
        print("\nRESULT: FAILED（缺少图标文件）")
        return 1
    print("  图标源   : %s" % ICO_SRC)

    desk = desktop_path()
    print("  桌面目录 : %s" % desk)
    print("  目录存在 : %s" % ("是" if os.path.isdir(desk) else "否 <<<"))
    if not os.path.isdir(desk):
        print("\nRESULT: FAILED（桌面目录不存在）")
        return 1

    lnk = os.path.join(desk, LNK_NAME)

    # ---------------- 1. 安装图标 ----------------
    print("\n[1/5] 安装图标到用户目录")
    rid, name = install_icon()
    src_name = INTEGRITY_NAMES.get(integrity_rid(ICO_SRC),
                                   "RID=0x%04X" % integrity_rid(ICO_SRC)
                                   if integrity_rid(ICO_SRC) is not None else "无标签")
    print("      安装到   : %s" % ICO)
    print("      源标签   : %s" % src_name)
    print("      安装后   : %s" % name)
    if rid is not None and rid <= 0x1000:
        print("      警告 <<< 安装后的图标仍是低完整性标签，操作系统可能仍显示白纸")
        ok = False

    # ---------------- 2. 创建快捷方式 ----------------
    print("\n[2/5] 创建 %s" % LNK_NAME)
    if not create_lnk(lnk, bat, ICO, HERE, DESC):
        print("      失败 <<<")
        print("\nRESULT: FAILED")
        return 1
    print("      ok  (%d 字节)" % os.path.getsize(lnk))

    # ---------------- 3. 回读属性 ----------------
    print("\n[3/5] 回读属性")
    data = read_lnk(lnk)
    for label, got, want in (("目标", data.get("target"), bat),
                             ("起始位置", data.get("workdir"), HERE)):
        good = bool(got) and os.path.normcase(got) == os.path.normcase(want)
        ok &= good
        print("      %-8s %s" % (label, "对" if good else "错 <<< 期望 " + want))
    icon_got = (data.get("icon") or "").strip()
    icon_good = os.path.normcase(icon_got.split(",")[0]) == os.path.normcase(ICO)
    ok &= icon_good
    print("      %-8s %s" % ("图标路径", "对 -> " + icon_got if icon_good
                            else "错 <<< " + icon_got))
    if not data.get("desc"):
        ok = False
        print("      %-8s 空 <<<" % "备注")
    else:
        print("      %-8s 有" % "备注")

    # ---------------- 4. 系统实际图标像素 ----------------
    print("\n[4/5] 取 Windows 实际分配给该快捷方式的图标像素")
    for small, tag in ((False, "大图标 32px"), (True, "小图标 16px")):
        simg, ssize, err = shell_icon_of(lnk, small=small)
        if simg is None:
            print("      %-12s 取不到 <<< %s" % (tag, err))
            ok = False
            continue
        wimg = ico_entry(simg.width)
        mad, note = compare(simg, wimg)
        if mad is None:
            print("      %-12s 实际=%s  我们的=%s  %s <<<"
                  % (tag, ssize, wimg.size, note))
            ok = False
            continue
        same = mad < 12.0
        ok &= same
        print("      %-12s 实际=%dx%d  与 .ico 同尺寸帧差异 MAD=%.2f  %s"
              % (tag, simg.width, simg.height, mad,
                 "一致（系统确实用了我们的图标）" if same else "不一致 <<< 可能回退默认图标"))

    # ---------------- 5. 断链检查 ----------------
    print("\n[5/5] 断链检查")
    tgt = data.get("target") or ""
    live = os.path.isfile(tgt)
    ok &= live
    print("      目标存在 : %s" % ("是" if live else "否 <<< " + tgt))

    print("\n" + "=" * 70)
    print("RESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
