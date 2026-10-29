"""Detect a supported Garmin, and install only the verified app built for it."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from maps import ROOT, DEVICES, APP_ID, PROGRAM_VERSION, PackError


def bridge_path():
    output = ROOT / 'build/mtp-bridge'
    source = ROOT / 'mac/mtp_bridge.c'
    if not output.exists() or output.stat().st_mtime < source.stat().st_mtime:
        output.parent.mkdir(exist_ok=True)
        prefix = next((Path(p) for p in ('/opt/homebrew','/usr/local') if (Path(p)/'include/libmtp.h').exists()), None)
        if not prefix:
            raise PackError('USB 传输组件未安装，请运行 Mac 初始化程序。')
        result = subprocess.run(['cc','-O2','-Wall','-Wextra',str(source),'-I'+str(prefix/'include'),
                                 '-L'+str(prefix/'lib'),'-lmtp','-o',str(output)],capture_output=True,text=True)
        if result.returncode:
            raise PackError('无法构建 USB 组件：'+result.stderr[-1000:])
    return output


def mtp(*args, timeout=120):
    try:
        result = subprocess.run([str(bridge_path()), *map(str,args)], capture_output=True, text=True, timeout=timeout)
        value = json.loads(result.stdout)
    except subprocess.TimeoutExpired as exc:
        raise PackError('USB 连接超时；请检查连接。如果安装已开始，请保留本次备份。') from exc
    except (ValueError,OSError) as exc:
        raise PackError('USB 组件未正常返回，请重新连接手表。') from exc
    if not value.get('ok'):
        raise PackError(value.get('error','无法连接手表。'))
    return value


def mounted_devices(volumes=Path('/Volumes')):
    devices = []
    for root in volumes.iterdir():
        garmin = next((root/n for n in ('GARMIN','Garmin') if (root/n/'GarminDevice.xml').is_file()),None)
        if not garmin:
            continue
        try:
            raw = (garmin/'GarminDevice.xml').read_bytes()
            if len(raw)>2*1024*1024 or b'<!DOCTYPE' in raw.upper():
                continue
            xml = ET.fromstring(raw)
            description = next(e.text for e in xml.iter() if e.tag.endswith('}Description') or e.tag=='Description')
            target = next((d for d,n in DEVICES.items() if n.casefold()==description.casefold()),None)
            apps = next((garmin/n for n in ('APPS','Apps') if (garmin/n).is_dir()),None)
            if target and apps:
                devices.append({'ok':True,'transport':'volume','device':target,'name':description,
                                'apps':str(apps),'free_bytes':shutil.disk_usage(root).free,
                                'installed':(apps/'BCTRAIL.PRG').exists()})
        except (OSError,ET.ParseError,StopIteration,AttributeError):
            continue
    return devices


def detect():
    devices = mounted_devices()
    if len(devices)>1:
        raise PackError('请只连接一只 Garmin 手表。')
    return devices[0] if devices else mtp('status',timeout=30)


def validate_program(folder, device):
    folder = Path(folder)
    metadata = json.loads((folder/'program.json').read_text())
    path = folder/'BCTRAIL.prg'
    if (metadata.get('device') != device or metadata.get('app_id') != APP_ID
            or metadata.get('program_version') != PROGRAM_VERSION
            or metadata.get('bytes') != path.stat().st_size
            or metadata.get('sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
        raise PackError('程序校验未通过或手表型号不一致，请重新生成。')
    return path


def install(folder, backups):
    device = detect()
    program = validate_program(folder, device['device'])
    backup = Path(backups)/time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True,exist_ok=False)
    if device['transport']=='mtp':
        result = mtp('install',program,device['device'],backup/'previous.prg',backup/'readback.prg',timeout=180)
    else:
        apps = Path(device['apps'])
        if shutil.disk_usage(apps).free < program.stat().st_size+1024*1024:
            raise PackError('手表剩余空间不足，旧地图已保留。')
        target = apps/'BCTRAIL.PRG'
        if target.is_symlink():
            raise PackError('手表应用目录异常，已停止安装。')
        old = target.exists()
        if old:
            shutil.copy2(target,backup/'previous.prg')
        temporary = apps/'BCTRAIL.TMP'
        if temporary.exists():
            raise PackError('上次安装的临时文件仍存在，请先检查手表。')
        try:
            with temporary.open('xb') as file:
                file.write(program.read_bytes());file.flush();os.fsync(file.fileno())
            if temporary.read_bytes()!=program.read_bytes():
                raise PackError('复制校验失败，旧地图已保留。')
            temporary.replace(target)
            if target.read_bytes()!=program.read_bytes():
                if old: shutil.copy2(backup/'previous.prg',target)
                raise PackError('安装后校验失败，已尝试恢复旧地图。')
        finally:
            temporary.unlink(missing_ok=True)
        result = {'ok':True,'status':'copied_verified','replaced':old,'hardware_verified':False}
    result['backup'] = str(backup)
    return result
