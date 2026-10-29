"""Detect a supported Garmin, and install only the verified app built for it."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from maps import ROOT, DEVICES, APP_ID, PREVIEW_APP_ID, PROGRAM_VERSION, PackError

PROGRAM_FILES = ('BCPREVW.PRG', 'BCTRAIL.PRG')
MAX_COURSE_BYTES = 20 * 1024 * 1024


def _course_format(name):
    if (not isinstance(name, str) or not name or '/' in name or '\\' in name
            or any(ord(c) < 32 for c in name)):
        return None
    extension = Path(name).suffix.lower().lstrip('.')
    return extension if extension in ('fit', 'gpx') else None


def _course_id(identity):
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _open_garmin(device):
    garmin = Path(device['apps']).parent
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    mount = os.open(garmin.parent, flags)
    try:
        return os.open(garmin.name, flags, dir_fd=mount)
    finally:
        os.close(mount)


def _volume_device_key(garmin):
    descriptor = os.open('GarminDevice.xml', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=garmin)
    with os.fdopen(descriptor, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 2 * 1024 * 1024:
            raise PackError('无法确认这只手表的身份，请重新连接后再读取路线。')
        raw = file.read(2 * 1024 * 1024 + 1)
    try:
        if len(raw) > 2 * 1024 * 1024 or b'<!DOCTYPE' in raw.upper():
            raise ValueError('device metadata')
        xml = ET.fromstring(raw)
        unit_id = next((child.text.strip() for child in xml
                        if child.tag.split('}')[-1].lower() in ('id', 'unitid') and child.text), '')
        if not unit_id or len(unit_id) > 1024:
            raise ValueError('device identity')
        return _course_id(['garmin-unit', unit_id])
    except (ET.ParseError, ValueError) as exc:
        raise PackError('无法确认这只手表的身份，请重新连接后再读取路线。') from exc


def _volume_courses(device):
    """List only direct course files; internal paths never leave this module."""
    garmin = Path(device['apps']).parent
    if garmin.is_symlink() or garmin.parent.is_symlink():
        raise PackError('手表路线目录异常，请重新连接手表。')
    descriptor = _open_garmin(device)
    try:
        device_key = _volume_device_key(descriptor)
    finally:
        os.close(descriptor)
    folders = [p for p in garmin.iterdir() if p.name.casefold() == 'courses']
    if not folders:
        return []
    if len(folders) != 1 or folders[0].is_symlink() or not folders[0].is_dir():
        raise PackError('手表路线目录异常，请重新连接手表。')
    folder = folders[0]
    entries = []
    for path in folder.iterdir():
        kind = _course_format(path.name)
        info = path.lstat()
        if not kind or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_COURSE_BYTES:
            continue
        identity = ['volume', device['device'], device_key, str(folder), path.name,
                    info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]
        entries.append({'id': _course_id(identity), 'name': path.name, 'bytes': info.st_size,
                        'format': kind, '_path': path, '_stat': info, '_device_key': device_key})
    return entries


def _course_listing():
    detected = detect()
    try:
        if detected['transport'] == 'volume':
            entries = _volume_courses(detected)
        else:
            listing = mtp('list-courses', timeout=30)
            if listing.get('device') != detected['device']:
                raise PackError('连接的手表已改变，请重新读取路线列表。')
            device_key = listing.get('device_key')
            if (not isinstance(device_key, str) or len(device_key) != 64
                    or any(c not in '0123456789abcdef' for c in device_key)):
                raise PackError('无法确认这只手表的身份，请重新连接后再读取路线。')
            if not isinstance(listing.get('courses'), list):
                raise PackError('手表返回了无效的路线列表，请重新连接。')
            entries = []
            for item in listing['courses']:
                if not isinstance(item, dict):
                    raise PackError('手表返回了无效的路线列表，请重新连接。')
                kind = _course_format(item.get('name'))
                size, storage_id, object_id = item.get('bytes'), item.get('storage_id'), item.get('object_id')
                if (not kind or type(size) is not int or not 0 < size <= MAX_COURSE_BYTES
                        or type(storage_id) is not int or not 0 < storage_id <= 0xffffffff
                        or type(object_id) is not int or not 0 < object_id <= 0xffffffff):
                    raise PackError('手表返回了无效的路线列表，请重新连接。')
                identity = ['mtp', detected['device'], device_key, storage_id, object_id, item['name'], size]
                entries.append({'id': _course_id(identity), 'name': item['name'], 'bytes': size,
                                'format': kind, '_storage': storage_id, '_object': object_id,
                                '_device_key': device_key})
        entries.sort(key=lambda item: (item['name'].casefold(), item['id']))
        return detected, entries
    except OSError as exc:
        raise PackError('无法读取手表路线目录，请检查 USB 连接。') from exc


def _public_course(entry):
    return {key: entry[key] for key in ('id', 'name', 'bytes', 'format')}


def list_courses():
    """Return public metadata, with opaque IDs and no device paths/object IDs."""
    detected, entries = _course_listing()
    return {'ok': True, 'device': detected['device'], 'name': detected['name'],
            'transport': detected['transport'], 'courses': [_public_course(e) for e in entries]}


def read_course(course_id):
    """Read a freshly enumerated course. Never accepts a path from the caller."""
    if (not isinstance(course_id, str) or len(course_id) != 64
            or any(c not in '0123456789abcdef' for c in course_id)):
        raise PackError('路线选择无效，请重新读取手表路线列表。')
    detected, entries = _course_listing()
    entry = next((e for e in entries if e['id'] == course_id), None)
    if entry is None:
        raise PackError('这条路线已改变或不在手表中，请重新读取路线列表。')
    try:
        if detected['transport'] == 'volume':
            path = entry['_path']
            # Open each component from the mount down without following links,
            # so a swapped GARMIN/Courses directory cannot redirect this read.
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            garmin = _open_garmin(detected)
            try:
                if _volume_device_key(garmin) != entry['_device_key']:
                    raise PackError('连接的手表已改变，请重新读取路线列表。')
                directory = os.open(path.parent.name, flags, dir_fd=garmin)
                try:
                    descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                    with os.fdopen(descriptor, 'rb') as file:
                        before = os.fstat(file.fileno())
                        expected = entry['_stat']
                        if (not stat.S_ISREG(before.st_mode) or
                                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                                (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)):
                            raise PackError('路线文件已改变，请重新读取路线列表。')
                        data = file.read(MAX_COURSE_BYTES + 1)
                        after = os.fstat(file.fileno())
                        if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                            raise PackError('路线文件已改变，请重新读取路线列表。')
                finally:
                    os.close(directory)
            finally:
                os.close(garmin)
        else:
            with tempfile.TemporaryDirectory(prefix='breadcrumb-course-') as temporary:
                output = Path(temporary) / 'course'
                mtp('read-course', entry['_storage'], entry['_object'], entry['bytes'], entry['name'],
                    output, detected['device'], entry['_device_key'], timeout=120)
                if output.is_symlink() or not output.is_file() or output.stat().st_size != entry['bytes']:
                    raise PackError('路线复制不完整，请重新连接后重试。')
                with output.open('rb') as file:
                    data = file.read(MAX_COURSE_BYTES + 1)
        if len(data) != entry['bytes'] or len(data) > MAX_COURSE_BYTES:
            raise PackError('路线复制不完整或超过 20 MB，请重新读取路线列表。')
        return {**_public_course(entry), 'data': data}
    except OSError as exc:
        raise PackError('无法读取这条路线，请重新连接手表后重试。') from exc


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
