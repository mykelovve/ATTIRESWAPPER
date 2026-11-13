"""Validate the sealed portable package on a disposable Windows runner.

The binary/source binding is checked before execution. Model assets and a game
installation are not required. Reports describe runtime/UI checks, not gameplay.
"""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
import zipfile

from pyi_archive import Package, Pyz


def git(*args):
    return subprocess.check_output(['git', *args])


def check_tk(exe, folder, version):
    """Exercise current source with the exact bundled original theme/assets."""
    import tkinter as tk
    package = Package(exe)
    pyz = Pyz(package.content('PYZ.pyz'))
    assets = folder / 'ui-assets'
    for name in ('gmf_runtime\\thelovvers_logo.png', 'gmf_runtime\\thelovvers_icon.ico'):
        path = assets.joinpath(*name.split('\\'))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(package.content(name))
    os.environ['LOCALAPPDATA'] = str(folder / 'preferences')
    sys._MEIPASS = str(assets)
    sys.path.insert(0, str(Path('Source').resolve()))
    theme = types.ModuleType('gmf_theme')
    theme.__file__ = '<unchanged release theme>'
    sys.modules['gmf_theme'] = theme
    exec(pyz.code('gmf_theme'), theme.__dict__)
    import attire_swapper_gui as gui
    import app_updater
    checks, opened = [], []
    app_updater.check_release = lambda current: checks.append(current)
    gui.webbrowser.open = lambda *args, **kwargs: opened.append(args)
    window = tk.Tk()
    window.withdraw()
    try:
        app = gui.AttireSwapperApp(window)
        assert app_updater.version_tuple(gui.APP_VERSION) == app_updater.version_tuple(version)
        assert app.cancel_button.instate(['disabled'])
        def settle():
            deadline = time.monotonic() + 10
            while app.update_busy and time.monotonic() < deadline:
                window.update()
                time.sleep(.02)
            window.update()
            assert not app.update_busy
        app.update_button.invoke()
        settle()
        app.nav_items[-1]._click(None)
        settle()
        app.task_kind = 'swap'
        app._set_busy(True, 'Windows UI validation')
        assert not app.cancel_button.instate(['disabled'])
        app.update_button.invoke()
        settle()
        assert app.busy
        app.cancel_button.invoke()
        window.update()
        assert app.cancel_requested.is_set()
        assert checks == [gui.APP_VERSION] * 3 and not opened
        return {'passed': True, 'version': version, 'internal_update_checks': len(checks),
                'browser_windows_opened': len(opened), 'cancel_widget_checked': True,
                'model_busy_state_preserved': True,
                'scope': 'Source UI with exact bundled theme; real Windows process tests run separately.'}
    finally:
        window.destroy()


def main():
    assert os.name == 'nt', 'This release gate requires a Windows runner'
    assert sys.version_info[:2] == (3, 14), 'Match the bundled Python version'
    tag, ref = os.environ['RELEASE_TAG'], os.environ['ARTIFACT_REF']
    assert re.fullmatch(r'v\d+\.\d+\.\d+', tag)
    assert ref == 'release-input/' + tag
    git('fetch', '--depth=1', 'origin', ref)
    artifact = git('rev-parse', 'FETCH_HEAD').decode().strip()
    metadata = json.loads(git('show', artifact + ':package/release.json'))
    assert metadata['tag'] == tag
    commit = metadata['source_commit']
    assert re.fullmatch(r'[0-9a-f]{40}', commit)
    git('fetch', '--depth=1', 'origin', commit)
    version = tag[1:]
    short = version[:-2] if version.endswith('.0') else version
    expected = 'Mykelovve_Attire_Swapper_v' + short + '_Portable.zip'
    assert metadata['zip_name'] == expected
    raw = git('show', artifact + ':package/' + expected)
    assert len(raw) == metadata['zip_bytes'] <= 100 * 1024**2
    assert hashlib.sha256(raw).hexdigest() == metadata['zip_sha256']
    folder = Path(tempfile.mkdtemp(prefix='attire-windows-check-'))
    archive = folder / expected
    archive.write_bytes(raw)
    exe = folder / ('Mykelovve_Attire_Swapper_v' + short + '.exe')
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        expected_sources = {'Source/' + name for name in
                            git('ls-tree', '--name-only', commit + ':Source').decode().splitlines()
                            if name.endswith('.py')}
        assert set(metadata['source_sha256']) == expected_sources
        for name, digest in metadata['source_sha256'].items():
            assert re.fullmatch(r'Source/[A-Za-z0-9_]+\.py', name)
            data = zipped.read(name)
            assert hashlib.sha256(data).hexdigest() == digest
            assert data == git('show', commit + ':' + name)
            assert data == Path(name).read_bytes(), 'Workflow must check matching source'
        assert [n for n in zipped.namelist() if n.lower().endswith('.exe')] == [exe.name]
        exe.write_bytes(zipped.read(exe.name))
    package = Package(exe)
    pyz = Pyz(package.content('PYZ.pyz'))
    for name in metadata['source_sha256']:
        module = Path(name).stem
        source = git('show', commit + ':' + name)
        assert package.content('assets\\helpers\\' + module + '.py') == source
        if module in pyz.meta:
            assert pyz.code(module) == compile(source, module + '.py', 'exec',
                                               dont_inherit=True, optimize=0)
    reports = Path('windows-validation')
    reports.mkdir(exist_ok=True)
    report = reports / 'frozen-self-test.json'
    subprocess.run([str(exe), '--self-test', '--report', str(report.resolve())],
                   check=True, timeout=180)
    frozen = json.loads(report.read_text(encoding='utf-8'))
    sys.path.insert(0, str(Path('Source').resolve()))
    from app_updater import version_tuple
    assert frozen['success'] and version_tuple(frozen['version']) == version_tuple(version)
    ui = check_tk(exe, folder, version)
    (reports / 'tk-ui.json').write_text(json.dumps(ui, indent=2), encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests',
                    '-p', 'test_gui_workflow.py', '-v'], check=True, timeout=120)
    result = {'passed': True, 'tag': tag, 'source_commit': commit,
              'zip_sha256': metadata['zip_sha256'], 'frozen_runtime': True,
              'tk_ui': ui, 'windows_workflow_regressions': True,
              'gameplay_and_live_cloth_tested': False}
    (reports / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
