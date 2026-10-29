"""Detect a supported Garmin, and install only the verified app built for it."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from maps import ROOT, DEVICES, APP_ID, PREVIEW_APP_ID, PROGRAM_VERSION, PackError

PROGRAM_FILES = ('BCPREVW.PRG', 'BCTRAIL.PRG')


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
    try:
        metadata = json.loads((folder/'program.json').read_text())
        if (not isinstance(metadata, dict) or metadata.get('device') != device
                or metadata.get('app_id') != APP_ID or metadata.get('program_version') != PROGRAM_VERSION):
            raise ValueError('metadata')
        return validated_file(folder/'BCTRAIL.prg', metadata)
    except (OSError, ValueError, TypeError):
        raise PackError('程序校验未通过或手表型号不一致，请重新生成。')


def validated_file(path, metadata):
    if path.is_symlink() or not path.is_file():
        raise ValueError('program file')
    data = path.read_bytes()
    if (not 64 <= len(data) <= 16*1024*1024 or metadata.get('bytes') != len(data)
            or metadata.get('sha256') != hashlib.sha256(data).hexdigest()):
        raise ValueError('program integrity')
    return path


def validate_bundle(folder, device):
    """Validate both apps before starting either USB copy, including fixed names."""
    folder = Path(folder)
    program = validate_program(folder, device)
    try:
        metadata = json.loads((folder/'program.json').read_text())
        preview = metadata.get('preview')
        if (not isinstance(preview, dict) or preview.get('filename') != 'BCPREVW.prg'
                or preview.get('app_id') != PREVIEW_APP_ID):
            raise ValueError('preview metadata')
        viewer = validated_file(folder/'BCPREVW.prg', preview)
        if metadata.get('total_bytes') != program.stat().st_size+viewer.stat().st_size:
            raise ValueError('bundle size')
        return [('BCPREVW.PRG', viewer, 'preview-'), ('BCTRAIL.PRG', program, '')]
    except (OSError, ValueError, TypeError):
        raise PackError('赛道预览程序缺失或校验未通过，请重新生成手表地图。')


def volume_preflight(apps, programs):
    if apps.is_symlink() or not apps.is_dir():
        raise PackError('手表应用目录异常，已停止安装。')
    if shutil.disk_usage(apps).free < sum(path.stat().st_size for _,path,_ in programs)+1024*1024:
        raise PackError('手表剩余空间不足，旧地图已保留。')
    for filename,_,_ in programs:
        target = apps/filename
        temporary = target.with_suffix('.TMP')
        if filename not in PROGRAM_FILES or target.is_symlink() or (target.exists() and not target.is_file()):
            raise PackError('手表应用目录异常，已停止安装。')
        if temporary.exists() or temporary.is_symlink():
            raise PackError('上次安装的临时文件仍存在，请先检查手表。')


def install_volume_file(apps, filename, program, previous, readback):
    if filename not in PROGRAM_FILES:
        raise PackError('不支持的手表程序文件名。')
    target = apps/filename
    temporary = target.with_suffix('.TMP')
    expected = program.read_bytes()
    old = target.exists()
    if old:
        shutil.copy2(target, previous)
        if previous.read_bytes() != target.read_bytes():
            raise PackError('旧程序备份不完整，已停止安装。')
    created = replaced = False
    try:
        with temporary.open('xb') as file:
            created = True
            file.write(expected);file.flush();os.fsync(file.fileno())
        if temporary.read_bytes() != expected:
            raise PackError('复制校验失败，旧程序已保留。')
        temporary.replace(target)
        replaced = True
        shutil.copyfile(target, readback)
        if readback.read_bytes() != expected:
            raise PackError('安装后的回读校验失败。')
    except (OSError, PackError) as exc:
        if replaced:
            try:
                if old:
                    shutil.copy2(previous, target)
                    if target.read_bytes() != previous.read_bytes():
                        raise OSError('restore mismatch')
                else:
                    target.unlink()
            except OSError:
                raise PackError('安装失败且自动恢复未完成。请保留 Mac 上的备份。') from exc
        raise
    finally:
        if created:
            temporary.unlink(missing_ok=True)
    return {'ok':True,'status':'copied_verified','replaced':old,'hardware_verified':False}


def install(folder, backups):
    device = detect()
    programs = validate_bundle(folder, device['device'])
    total = sum(path.stat().st_size for _,path,_ in programs)
    if device.get('free_bytes') is not None and device['free_bytes'] < total+1024*1024:
        raise PackError('手表剩余空间不足，旧地图已保留。')
    if device['transport'] != 'mtp':
        volume_preflight(Path(device['apps']), programs)
    backup = Path(backups)/(time.strftime('%Y%m%d-%H%M%S')+'-%06d'%(time.time_ns()%1000000))
    backup.mkdir(parents=True,exist_ok=False)
    results = {}
    for filename,program,prefix in programs:
        try:
            previous,readback = backup/(prefix+'previous.prg'),backup/(prefix+'readback.prg')
            if device['transport']=='mtp':
                result = mtp('install',program,device['device'],previous,readback,filename,timeout=180)
            else:
                result = install_volume_file(Path(device['apps']),filename,program,previous,readback)
            if result.get('status') != 'copied_verified':
                raise PackError('USB 组件未确认回读校验成功。')
            results[filename] = result
        except (OSError, PackError) as exc:
            report = {'ok':False,'status':'partial' if results else 'failed',
                      'completed':list(results),'failed':filename,'error':str(exc),'backup':str(backup)}
            (backup/'installation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            label = '赛道预览' if filename=='BCPREVW.PRG' else '运动地图'
            message = ('赛道预览已写入，但运动地图安装未完成。' if results else label+'安装未完成。')
            raise PackError(message+str(exc)+' 请保留安装备份；重新连接后重试。') from exc
    report = {'ok':True,'status':'copied_verified','replaced':any(r.get('replaced') for r in results.values()),
              'hardware_verified':False,'programs':results,'backup':str(backup)}
    (backup/'installation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return report
