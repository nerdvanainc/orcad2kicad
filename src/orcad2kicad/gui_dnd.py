"""Windows 탐색기 파일/폴더 끌어다 놓기(WM_DROPFILES) — ctypes 만 쓴다(외부 패키지 없음).

`enable_file_drop(tk_root, callback)` 가 최상위 창의 윈도 프로시저를 서브클래싱해 WM_DROPFILES 를
가로채고, 떨어진 경로 목록을 `callback(paths)` 로 넘긴다(Tcl 호출은 wndproc 안에서 하면 GIL 충돌로 죽으므로 목록만 쌓고 Tk `after` 폴링이 wndproc
밖에서 callback 호출). Windows 가 아니거나 어느 단계든 실패하면 False 를 돌려줄 뿐 예외를 내지 않는다 —
그때는 기존 [찾아보기] 버튼만 쓰면 된다.
"""
from __future__ import annotations
import sys

WM_DROPFILES = 0x0233
WM_COPYDATA = 0x004A
WM_COPYGLOBALDATA = 0x0049
GWLP_WNDPROC = -4
GA_ROOT = 2
MSGFLT_ALLOW = 1


def find_toplevel_hwnd(tk_root):
    """Tk 루트의 실제 최상위 창 HWND(없으면 0). winfo_id() 는 안쪽 클라이언트 창이라 루트를 따라 올라간다."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    inner = tk_root.winfo_id()
    return user32.GetAncestor(inner, GA_ROOT) or 0


def _query_dropped(hdrop):
    """HDROP -> 경로 목록(항상 DragFinish 로 해제한다)."""
    import ctypes
    from ctypes import wintypes
    shell32 = ctypes.windll.shell32
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    shell32.DragFinish.argtypes = [wintypes.HANDLE]
    shell32.DragFinish.restype = None
    paths = []
    try:
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        for i in range(count):
            n = shell32.DragQueryFileW(hdrop, i, None, 0)
            buf = ctypes.create_unicode_buffer(n + 1)
            shell32.DragQueryFileW(hdrop, i, buf, n + 1)
            paths.append(buf.value)
    finally:
        shell32.DragFinish(hdrop)
    return paths


def enable_file_drop(tk_root, callback):
    """최상위 창에 파일 드롭을 켠다. 성공하면 True, 비-Windows·실패면 False(예외 없음)."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        shell32 = ctypes.windll.shell32
        LRESULT, WPARAM, LPARAM = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)

        tk_root.update_idletasks()                     # HWND 가 만들어져 있게 한다
        hwnd = find_toplevel_hwnd(tk_root)
        if not hwnd:
            return False

        is64 = ctypes.sizeof(ctypes.c_void_p) == 8
        set_long = user32.SetWindowLongPtrW if is64 else user32.SetWindowLongW
        set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_long.restype = ctypes.c_void_p
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        user32.CallWindowProcW.restype = LRESULT
        shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
        shell32.DragAcceptFiles.restype = None

        state = {'old': None}
        pending = []

        def drain():
            """wndproc 밖(Tk after)에서 쌓인 드롭을 callback 으로 넘긴다."""
            try:
                while pending:
                    callback(pending.pop(0))
            except Exception:                   # 폴링은 계속 돌아야 하므로 삼키되 흔적은 남긴다
                import traceback
                traceback.print_exc()
            try:
                tk_root.after(50, drain)
            except Exception:
                pass                            # 창이 닫힘

        def wndproc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                try:
                    paths = _query_dropped(wparam)
                except Exception:
                    paths = []
                if paths:
                    pending.append(paths)       # Tcl 호출은 wndproc 안에서 하지 않는다(GIL 충돌)
                return 0
            return user32.CallWindowProcW(state['old'], h, msg, wparam, lparam)

        proc = WNDPROC(wndproc)
        old = set_long(hwnd, GWLP_WNDPROC, ctypes.cast(proc, ctypes.c_void_p))
        if not old:
            return False
        state['old'] = old
        # GC 되면 크래시하므로 루트에 강한 참조를 붙여 둔다
        tk_root._o2k_dnd_proc = proc
        tk_root._o2k_dnd_hwnd = hwnd

        shell32.DragAcceptFiles(hwnd, True)
        tk_root.after(50, drain)
        try:                                            # 관리자 권한 실행 시에도 드롭이 오게
            flt = user32.ChangeWindowMessageFilterEx
            flt.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, ctypes.c_void_p]
            flt.restype = wintypes.BOOL
            for m in (WM_DROPFILES, WM_COPYDATA, WM_COPYGLOBALDATA):
                flt(hwnd, m, MSGFLT_ALLOW, None)
        except Exception:
            pass
        return True
    except Exception:
        return False
