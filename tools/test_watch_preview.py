#!/usr/bin/env python3
"""Run real Monkey C camera tests against an already generated map in the SDK.

Start the official Connect IQ simulator first. A timeout is a failed test run,
not a successful validation. This never installs anything on a physical watch.
"""
import argparse
import os
import re
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mac'))
from maps import find_sdk, java_env


def run_bounded(command, folder, timeout, check_exit=True):
    process = subprocess.Popen(command,cwd=folder,env=java_env(),stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT,text=True,start_new_session=True)
    try:
        output,_ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGTERM)
        output,_ = process.communicate(timeout=5)
        print(output)
        raise SystemExit('Simulator test timed out; results are NOT verified.')
    print(output)
    if process.returncode and check_exit:
        raise SystemExit(process.returncode)
    return output


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('map_folder',type=Path)
    parser.add_argument('--viewer',action='store_true',help='Run with the standalone Race Preview entry point')
    parser.add_argument('--key',type=Path,default=ROOT/'build/desktop-data/private/developer.der')
    args=parser.parse_args()
    folder=args.map_folder.resolve()
    sdk=find_sdk()
    jungle=(folder/('preview.jungle' if args.viewer else 'monkey.jungle')).read_text().replace('base.sourcePath = source',
        'base.sourcePath = source;'+os.path.relpath(ROOT/'watch-offline/tests',folder))
    (folder/'preview-tests.jungle').write_text(jungle)
    run_bounded([str(sdk/'bin/monkeyc'),'-f','preview-tests.jungle','-d','fr255','-y',str(args.key),
                 '-o','PREVIEW_TEST.prg','-t'],folder,45)
    # SDK 9.3's shell may exit 1 even when its test summary reports success.
    # Require the actual nonempty test summary, never an exit code alone.
    result=run_bounded([str(sdk/'bin/monkeydo'),'PREVIEW_TEST.prg','fr255','-t'],folder,45,check_exit=False)
    if not re.search(r'PASSED \(passed=[1-9][0-9]*, failed=0, errors=0\)',result) or 'FAILED (' in result:
        raise SystemExit('No successful SDK test summary; inspect the output above.')
