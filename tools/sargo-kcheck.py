#!/usr/bin/env python3
"""Syntax-check xnu sources locally with the CI compile flags.

usage: kcheck.py CI_LOG BUILDTREE TEMPLATE_SRC FILE [FILE...]
TEMPLATE_SRC: a source path (relative to xnu/) whose CI compile command is reused
FILE: local files to check (absolute paths); each is compiled with -fsyntax-only
"""
import re, shlex, subprocess, sys

CI_DXB = '/Users/runner/work/ios-sargo/ios-sargo/dxb'
log, bt, template = sys.argv[1], sys.argv[2], sys.argv[3]
files = sys.argv[4:]
cmd = None
for line in open(log, errors='replace'):
    if '/clang' in line and ' -c ' in line and line.rstrip().endswith('/' + template):
        m = re.search(r'(\S+/clang(\+\+)?) (.*)', line.rstrip('\n'))
        cmd = m.group(3)
        break
if cmd is None:
    sys.exit('no compile command for ' + template)
args = shlex.split(cmd)
out = []
skip = False
for a in args:
    if skip:
        skip = False
        continue
    if a in ('-MF', '-MT', '-o', '-arch'):
        skip = True
        continue
    if a in ('-c', '-g', '-MD', '-MMD') or a.endswith('/' + template):
        continue
    a = a.replace(CI_DXB, bt)
    a = re.sub(r'/Applications/Xcode[^ ]*?\.sdk/+System/Library/Frameworks/Kernel\.framework/Versions/A/PrivateHeaders/platform',
               bt + '/sdk-kernel-platform', a)
    out.append(a)
rc = 0
for f in files:
    full = ['xcrun', 'clang', '-arch', 'arm64', '-fsyntax-only', '-Wno-everything'] + out + [f]
    r = subprocess.run(full, capture_output=True, text=True,
                       cwd=bt + '/build/xnu.obj/DEVELOPMENT_ARM64_SWEET/' + template.split('/')[0] + '/DEVELOPMENT')
    errs = [l for l in r.stderr.splitlines() if ' error:' in l or 'fatal error' in l]
    print(f'{f}: {"OK" if r.returncode == 0 else "FAILED"} ({len(errs)} errors)')
    for l in errs[:20]:
        print('   ', l)
    rc |= r.returncode
sys.exit(rc)
