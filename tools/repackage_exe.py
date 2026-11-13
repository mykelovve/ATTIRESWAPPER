"""Build a new portable EXE from the previous sealed release EXE plus git source.

Usage: py -3.14 -I tools/repackage_exe.py <repo> <source_commit> <previous_exe> <out_exe> [previous_tag]
Only the application modules that changed between previous_tag (default v0.4.0) and <source_commit> are
replaced, both as bundled helper .py files and as compiled PYZ modules. Every
other archive entry (runtime, renderer, add-on, entry script) is copied as is.
"""
import hashlib
import json
import marshal
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pyi_archive import Package, Pyz  # noqa: E402

repo, commit, v04, out = sys.argv[1:5]
base_tag = sys.argv[5] if len(sys.argv) > 5 else 'v0.4.0'


def git_bytes(ref, path):
    return subprocess.check_output(['git', '-C', repo, 'show', '%s:%s' % (ref, path)])


changed = subprocess.check_output(['git', '-C', repo, 'diff', '--name-only', base_tag, commit, '--', 'Source']).decode().split()
modules = sorted(Path(p).stem for p in changed if p.endswith('.py'))
print('changed modules:', modules)

pkg = Package(v04)
original = {e[0]: (e[3], e[4]) for e in pkg.entries}
pyz = Pyz(pkg.content('PYZ.pyz'))
replacements = {}
for module in modules:
    source = git_bytes(commit, 'Source/%s.py' % module)
    helper = 'assets\\helpers\\%s.py' % module
    if helper not in original:
        # A new helper run only by Blender (for example model_check): add it
        # next to the other helpers. It is not imported by the app itself.
        last = max(i for i, e in enumerate(pkg.entries) if e[0].startswith('assets\\helpers\\'))
        pkg.entries.insert(last + 1, [helper, 1, b'x', zlib.compress(source, 9), len(source)])
        print('added new helper:', helper)
        continue
    pkg.replace(helper, source, level=9)
    if module not in pyz.meta:
        raise SystemExit('Module %s is not in PYZ' % module)
    code = compile(source, module + '.py', 'exec', dont_inherit=True, optimize=0)
    replacements[module] = zlib.compress(marshal.dumps(code), 9)
pkg.replace('PYZ.pyz', pyz.build(replacements), level=9)
built = pkg.build()
Path(out).write_bytes(built)

# ---- Read back and verify.
check = Package(out)
pyz2 = Pyz(check.content('PYZ.pyz'))
problems = []
for name, flag, typ, raw, ulen in check.entries:
    if name == 'PYZ.pyz' or name in ('assets\\helpers\\%s.py' % m for m in modules):
        continue
    if name not in original:
        problems.append('unexpected new entry: ' + name)
        continue
    if original[name] != (raw, ulen):
        problems.append('unexpected change: ' + name)
for module in modules:
    source = git_bytes(commit, 'Source/%s.py' % module)
    if check.content('assets\\helpers\\%s.py' % module) != source:
        problems.append('helper mismatch: ' + module)
    if module in pyz.meta and pyz2.code(module) != compile(source, module + '.py', 'exec', dont_inherit=True, optimize=0):
        problems.append('PYZ code mismatch: ' + module)
for name in pyz.order:
    if name not in modules and pyz2.blob(name) != pyz.blob(name):
        problems.append('PYZ module changed: ' + name)
if pyz2.order != pyz.order:
    problems.append('PYZ order changed')
# Every bundled helper must equal its git source at the release commit.
for name, *_ in check.entries:
    if name.startswith('assets\\helpers\\') and name.endswith('.py'):
        module = name.rsplit('\\', 1)[1]
        if check.content(name) != git_bytes(commit, 'Source/' + module):
            problems.append('bundled helper differs from release source: ' + module)
digest = hashlib.sha256(built).hexdigest()
print(json.dumps({'output': out, 'bytes': len(built), 'sha256': digest, 'replaced_modules': modules,
                  'entries': len(check.entries), 'pyz_modules': len(pyz2.order), 'problems': problems}, indent=2))
if problems:
    raise SystemExit(1)
