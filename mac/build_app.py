"""Build a local Mac application. SDK, keys and personal maps are not bundled."""
import plistlib
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
app = ROOT/'build/Breadcrumb Trail.app'
contents=app/'Contents'
for name in ('MacOS','Resources'): (contents/name).mkdir(parents=True,exist_ok=True)
(contents/'Resources/project-root.txt').write_text(str(ROOT))
with (contents/'Info.plist').open('wb') as file:
    plistlib.dump({'CFBundleName':'Breadcrumb Trail','CFBundleDisplayName':'Breadcrumb Trail',
                  'CFBundleIdentifier':'org.breadcrumb.trail.desktop','CFBundleVersion':'2',
                  'CFBundleShortVersionString':'0.2.0','CFBundleExecutable':'Breadcrumb',
                  'CFBundlePackageType':'APPL','NSHighResolutionCapable':True,
                  'NSAppTransportSecurity':{'NSAllowsLocalNetworking':True}},file)
subprocess.run(['swiftc',str(ROOT/'mac/Launcher.swift'),'-O','-framework','Cocoa','-framework','WebKit',
                '-module-cache-path',str(ROOT/'build/swift-cache'),'-o',str(contents/'MacOS/Breadcrumb')],check=True)
subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
print(app)
