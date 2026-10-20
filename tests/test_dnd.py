"""끌어다 놓기: 분류(classify_dropped), 칸 배정(drop_assignments), Windows WM_DROPFILES 통합."""
import ctypes
import os
import shutil
import sys
import tempfile
import time
import unittest

from orcad2kicad.pipeline import classify_dropped
from orcad2kicad.gui import drop_assignments

# test_gui 는 참조 샘플에 의존해 공개 트리에서 빠지므로 여기서 따로 판별한다(같은 방식).
try:
    import tkinter as tk
except Exception:                            # tkinter 미설치
    tk = None


def tk_available():
    """실제로 Tk 루트를 만들 수 있는가(디스플레이 유무). 결과를 캐시한다."""
    if tk is None:
        return False
    if not hasattr(tk_available, 'ok'):
        try:
            root = tk.Tk()
            root.withdraw()
            root.destroy()
            tk_available.ok = True
        except Exception:
            tk_available.ok = False
    return tk_available.ok


NETLIST_HEAD = b'*PADS2000*  \n*REMARK*\n'
BOARD_HEAD = b'!PADS-POWERPCB-V9.5-MILS! DESIGN DATABASE ASCII FILE 1.0\n'


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix='o2k_dnd_')
        self.addCleanup(shutil.rmtree, self.d, True)

    def mk(self, rel, data=b'x'):
        p = os.path.normpath(os.path.join(self.d, rel))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as fh:
            fh.write(data)
        return p


class ClassifyDroppedTest(_Tmp):
    def test_folder_is_non_recursive_and_mode_edif_wins(self):
        edf = self.mk('A.EDF')
        dsn = self.mk('A.DSN')
        brd = self.mk('A-4L.asc', BOARD_HEAD)
        self.mk('A.opj')
        self.mk('A.png')
        self.mk('KICAD/old.kicad_pro')
        self.mk('KICAD/old.kicad_pcb')
        r = classify_dropped([self.d])
        self.assertEqual(r['mode'], 'edif')
        self.assertEqual((r['edf'], r['dsn'], r['board']), (edf, dsn, brd))
        self.assertIsNone(r['netlist'])
        self.assertIsNone(r['kicad_project'])
        self.assertEqual(len(r['ignored']), 2)
        self.assertTrue(any('EDIF mode' in n for n in r['notes']))
        self.assertTrue(any(n.startswith('ignored 2 files') for n in r['notes']))

    def test_mixed_files_and_folder(self):
        pro = self.mk('sub/P.kicad_pro')
        nl = self.mk('N.asc', NETLIST_HEAD)
        r = classify_dropped([os.path.join(self.d, 'sub'), nl])
        self.assertEqual((r['mode'], r['kicad_project'], r['netlist']), ('kicad_project', pro, nl))

    def test_dsn_only_mode(self):
        dsn = self.mk('X.dsn')
        self.assertEqual(classify_dropped([dsn])['mode'], 'dsn')

    def test_multiple_boards_prefer_name_match(self):
        edf = self.mk('PROJ.edf')
        self.mk('a_other.asc', BOARD_HEAD)
        want = self.mk('PROJ-4L.asc', BOARD_HEAD)
        r = classify_dropped([self.d])
        self.assertEqual(r['board'], want)
        self.assertEqual(r['edf'], edf)
        self.assertTrue(any(n.startswith('board: using PROJ-4L.asc') for n in r['notes']))

    def test_unknown_asc_ignored(self):
        p = self.mk('weird.asc', b'hello world')
        r = classify_dropped([p])
        self.assertEqual(r['ignored'], [p])
        self.assertIsNone(r['netlist'])
        self.assertIsNone(r['board'])
        self.assertIsNone(r['mode'])
        self.assertTrue(any('unknown .asc' in n for n in r['notes']))

    def test_ipc_and_kicad_pcb(self):
        ipc = self.mk('n.IPC')
        pcb = self.mk('b.kicad_pcb')
        r = classify_dropped([ipc, pcb])
        self.assertEqual((r['netlist'], r['board'], r['mode']), (ipc, pcb, None))

    def test_missing_path_noted(self):
        r = classify_dropped([os.path.join(self.d, 'nope.edf')])
        self.assertIsNone(r['edf'])
        self.assertTrue(any(n.startswith('not found') for n in r['notes']))


class DropAssignmentsTest(unittest.TestCase):
    def cls(self, **kw):
        base = dict(edf=None, dsn=None, kicad_project=None, netlist=None, board=None,
                    mode=None, ignored=[], notes=[])
        base.update(kw)
        return base

    def cur(self, **kw):
        base = dict(edf='', dsn='', kicad_project='', netlist='', board='', outdir='', project='')
        base.update(kw)
        return base

    def test_primary_clears_missing_netlist_board(self):
        a = os.path.abspath('D:/x/new/A.EDF')
        out = drop_assignments(self.cls(edf=a, mode='edif'),
                               self.cur(edf=os.path.abspath('D:/x/old/A.EDF'), netlist='old.asc',
                                        board='old_b.asc', outdir='o', project='p'))
        self.assertEqual(out['netlist'], '')
        self.assertEqual(out['board'], '')
        self.assertEqual((out['outdir'], out['project']), ('', ''))
        self.assertEqual(out['input_mode'], 'edif')
        keys = list(out)
        self.assertLess(keys.index('netlist'), keys.index('edf'))   # 보조 칸이 주 입력보다 먼저
        self.assertLess(keys.index('outdir'), keys.index('edf'))

    def test_same_folder_keeps_outdir_and_project(self):
        a = os.path.abspath('D:/x/same/A.EDF')
        b = os.path.abspath('D:/x/same/B.asc')
        out = drop_assignments(self.cls(edf=a, board=b, mode='edif'),
                               self.cur(edf=os.path.abspath('D:/x/same/OLD.EDF'), outdir='o', project='p'))
        self.assertNotIn('outdir', out)
        self.assertNotIn('project', out)
        self.assertEqual(out['board'], os.path.normpath(b))
        self.assertEqual(out['netlist'], '')

    def test_edif_and_dsn_both_filled(self):
        a = os.path.abspath('D:/x/A.EDF')
        d = os.path.abspath('D:/x/A.DSN')
        out = drop_assignments(self.cls(edf=a, dsn=d, mode='edif'), self.cur())
        self.assertEqual((out['edf'], out['dsn'], out['input_mode']), (a, d, 'edif'))

    def test_board_only_drop(self):
        b = os.path.abspath('D:/x/B.asc')
        self.assertEqual(drop_assignments(self.cls(board=b), self.cur(edf='e', netlist='n')),
                         {'board': b})

    def test_nothing_usable(self):
        self.assertEqual(drop_assignments(self.cls(), self.cur()), {})


@unittest.skipUnless(sys.platform == 'win32', 'Windows only')
@unittest.skipUnless(tk_available(), 'no Tk display')
class WmDropFilesTest(unittest.TestCase):
    def test_real_wm_dropfiles(self):
        from ctypes import wintypes
        from orcad2kicad.gui_dnd import enable_file_drop, find_toplevel_hwnd, WM_DROPFILES
        root = tk.Tk()
        try:
            got = []
            self.assertTrue(enable_file_drop(root, got.append))
            hwnd = find_toplevel_hwnd(root)
            self.assertTrue(hwnd)
            paths = [r'C:\temp\한글 폴더\a.EDF', r'C:\temp\b.asc']
            blob = ('\0'.join(paths) + '\0\0').encode('utf-16-le')
            # DROPFILES: pFiles(DWORD)=20, pt(8), fNC(4), fWide(4)
            header = (20).to_bytes(4, 'little') + bytes(8) + bytes(4) + (1).to_bytes(4, 'little')
            data = header + blob
            k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
            k32.GlobalAlloc.restype = ctypes.c_void_p
            k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
            k32.GlobalLock.restype = ctypes.c_void_p
            k32.GlobalLock.argtypes = [ctypes.c_void_p]
            k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
            h = k32.GlobalAlloc(0x0042, len(data))     # GHND
            p = k32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            k32.GlobalUnlock(h)
            u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
            self.assertTrue(u32.PostMessageW(hwnd, WM_DROPFILES, h, 0))
            for _ in range(50):
                root.update()
                time.sleep(0.02)
                if got:
                    break
            self.assertEqual(got, [paths])
        finally:
            root.destroy()


@unittest.skipUnless(tk_available(), 'no Tk display')
class AppDropTest(_Tmp):
    def test_on_files_dropped_fills_fields(self):
        from orcad2kicad import gui
        os.environ['O2K_SETTINGS'] = os.path.join(self.d, 'settings.json')
        self.addCleanup(os.environ.pop, 'O2K_SETTINGS', None)
        edf = self.mk('P/A.EDF')
        brd = self.mk('P/A-4L.asc', BOARD_HEAD)
        app = gui.App()
        app.withdraw()
        try:
            app.var_netlist.set('stale.asc')
            app.on_files_dropped([os.path.dirname(edf)])
            self.assertEqual(app.var_edf.get(), edf)
            self.assertEqual(app.var_board.get(), brd)
            self.assertEqual(app.var_netlist.get(), '')
            self.assertEqual(app.var_input_mode.get(), 'edif')
            self.assertTrue(app.var_outdir.get().endswith('A_kicad'))
            app.running = True
            app.on_files_dropped([os.path.dirname(edf)])        # 실행 중에는 무시(예외 없음)
        finally:
            app.running = False
            app.destroy()


if __name__ == '__main__':
    unittest.main()
