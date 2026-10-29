#!/usr/bin/env python3
"""Loopback-only desktop backend. No route uploads or cloud account required."""
import argparse
import base64
import hmac
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import traceback
import urllib.parse
import uuid

from maps import (ROOT, PackError, Cancelled, plan_route, download_tiles, decode_tile, tile_path,
                  generate_resources, compile_program, write_json, find_sdk, DEVICES, COVERAGE_VERSION)
from device import detect, install, validate_bundle
import device as watch_device
from courses import parse_course, MAX_COURSE_BYTES


class Desktop:
    def __init__(self, data):
        self.data = Path(data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.cache = self.data/'cache/opentopomap'
        self.projects = self.data/'projects'
        self.projects.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.cancel = threading.Event()
        self.current = None
        self.job = {'active':False,'stage':'idle','message':'导入 GPX，开始准备下一场比赛。','progress':0}
        self.device = None
        self.courses = {'status':'idle','device':'','items':[],'error':''}
        self.course_ids = {}

    def program_ready(self, folder):
        try:
            metadata = json.loads((folder/'watch/program.json').read_text())
            validate_bundle(folder/'watch',metadata.get('device'))
            return True
        except (OSError, ValueError, PackError):
            return False

    def summary(self):
        with self.lock:
            history = []
            for p in sorted(self.projects.glob('*/plan.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
                try:
                    plan = json.loads(p.read_text())
                    history.append({'id':p.parent.name,'name':plan['route']['name'],
                                    'ready':self.program_ready(p.parent)})
                except (ValueError,KeyError,OSError):
                    continue
            result = {'job':dict(self.job),'device':self.device,'history':history,
                      'courses':{**self.courses,'items':[dict(item) for item in self.courses['items']]}}
            if self.current:
                folder = self.projects/self.current
                result.update({'id':self.current,'plan':json.loads((folder/'plan.json').read_text()),
                               'ready':self.program_ready(folder)})
                if result['ready']:
                    result['program'] = json.loads((folder/'watch/program.json').read_text())
            try: result['sdk'] = find_sdk().name
            except PackError: result['sdk'] = None
            return result

    def idle(self):
        if self.job['active']:
            raise PackError('当前任务尚未结束，请等待或暂停后再操作。')

    def prepare_route(self, raw, payload, cancelled=None):
        """Stage a complete project outside history; callers publish under lock."""
        if len(raw)>MAX_COURSE_BYTES:
            raise PackError('GPX 文件过大，请按单场比赛导出。')
        folder = Path(tempfile.mkdtemp(prefix='.route-',dir=self.data))
        try:
            if cancelled: cancelled()
            (folder/'route.gpx').write_bytes(raw)
            plan = plan_route(folder/'route.gpx',int(payload.get('zoom',15)),int(payload.get('buffer',300)))
            if plan['route']['name']=='route':
                plan['route']['name'] = Path(str(payload.get('filename','比赛路线.gpx'))).stem[:100]
            write_json(folder/'plan.json',plan)
            if cancelled: cancelled()
            return folder
        except Exception:
            shutil.rmtree(folder)
            raise

    def publish_route(self, folder, kind=None):
        identity = uuid.uuid4().hex
        folder.replace(self.projects/identity)
        self.current = identity
        self.job = {'active':False,'stage':'planned','message':'路线已导入，下载等高线地图后可预览。','progress':0}
        if kind: self.job['kind'] = kind

    def import_route(self, payload):
        with self.lock:
            self.idle()
            try: raw = base64.b64decode(payload['gpx'],validate=True)
            except (KeyError,ValueError) as exc: raise PackError('请选择有效的 GPX 文件。') from exc
            folder = self.prepare_route(raw,payload)
            try:
                self.publish_route(folder)
            finally:
                if folder.exists(): shutil.rmtree(folder)
        return self.summary()

    def check_course_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('已取消读取，原来的比赛和地图已保留。')

    def scan_courses(self):
        with self.lock:
            self.idle()
            self.cancel.clear()
            # Every scan replaces the allowlist, including empty/failed scans.
            self.course_ids = {}
            self.courses = {'status':'loading','device':'','items':[],'error':''}
            self.job = {'active':True,'kind':'courses','stage':'courses',
                        'message':'正在读取手表上的路线列表…','progress':0}
            threading.Thread(target=self.run_course_scan,daemon=True).start()

    def run_course_scan(self):
        try:
            self.check_course_cancel()
            result = watch_device.list_courses()
            self.check_course_cancel()
            if not result.get('ok') or not isinstance(result.get('courses'),list):
                raise PackError('无法读取手表路线列表，请重新连接后重试。')
            items = [{key:item[key] for key in ('id','name','bytes','format')}
                     for item in result['courses']]
            with self.lock:
                self.check_course_cancel()
                self.device = {key:result[key] for key in ('device','name','transport')}
                self.course_ids = {item['id']:dict(item) for item in items}
                self.courses = {'status':'ready','device':result.get('name',''),
                                'items':items,'error':''}
                message = '请选择一条路线，读取后准备地图。' if items else '手表中没有找到可读取的路线。'
                self.job.update(active=False,stage='courses_done',message=message,progress=100)
        except Exception as exc:
            self.course_failure(exc,'courses')

    def import_course(self, payload):
        with self.lock:
            self.idle()
            identity = payload.get('id')
            if (not isinstance(identity,str) or self.courses['status']!='ready'
                    or identity not in self.course_ids):
                raise PackError('请先读取手表路线列表，再选择其中一条路线。')
            item = dict(self.course_ids[identity])
            options = {'zoom':payload.get('zoom',15),'buffer':payload.get('buffer',300),
                       'filename':item['name']}
            self.cancel.clear()
            self.job = {'active':True,'kind':'import-course','stage':'import-course',
                        'message':'正在读取所选路线，完成后可预览并准备地图…','progress':0}
            threading.Thread(target=self.run_course_import,args=(item,options),daemon=True).start()

    def run_course_import(self, item, options):
        folder = None
        try:
            self.check_course_cancel()
            result = watch_device.read_course(item['id'])
            self.check_course_cancel()
            if any(result.get(key)!=item[key] for key in ('id','name','bytes','format')):
                raise PackError('手表路线已改变，请重新读取路线列表。')
            raw = parse_course(result['data'],item['name'])
            self.check_course_cancel()
            folder = self.prepare_route(raw,options,self.check_course_cancel)
            with self.lock:
                self.check_course_cancel()
                self.publish_route(folder,'import-course')
                self.courses = {'status':'idle','device':'','items':[],'error':''}
                self.course_ids = {}
        except Exception as exc:
            self.course_failure(exc,'import-course')
        finally:
            if folder is not None and folder.exists(): shutil.rmtree(folder)

    def course_failure(self, exc, kind):
        with self.lock:
            cancelled = self.cancel.is_set() or isinstance(exc,Cancelled)
            message = ('已取消读取，原来的比赛和地图已保留。' if cancelled else
                       str(exc) if isinstance(exc,PackError) else '读取路线未完成，请重新连接手表后重试。')
            if kind=='courses':
                self.course_ids = {}
                self.courses = {'status':'idle' if cancelled else 'error','device':'',
                                'items':[],'error':'' if cancelled else message}
            self.job.update(active=False,stage='paused' if cancelled else 'error',message=message)

    def cancel_job(self):
        with self.lock:
            if self.job.get('stage')=='install':
                raise PackError('安装期间请保持连接，等待校验完成。')
            if self.job.get('active'):
                self.cancel.set()
                if self.job.get('kind') in ('courses','import-course'):
                    self.job['message'] = '正在取消；等待当前 USB 读取结束，原来的地图会保留。'

    def select(self, identity):
        with self.lock:
            self.idle()
            if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity) or not (self.projects/identity/'plan.json').is_file():
                raise PackError('找不到这场比赛。')
            self.upgrade_plan(self.projects/identity)
            self.current = identity
            message = '已打开保存的比赛。' if self.program_ready(self.projects/identity) else '已打开比赛。点击生成手表地图，补齐各级缩放的满屏底图；已有地图会直接复用。'
            self.job = {'active':False,'stage':'selected','message':message,'progress':0}

    def upgrade_plan(self, folder):
        old = json.loads((folder/'plan.json').read_text())
        if old.get('coverage_version') == COVERAGE_VERSION:
            return
        plan = plan_route(folder/'route.gpx',old['zooms'][0],old['buffer_m'])
        plan['route']['name'] = old['route']['name']
        # Replanning preserves the raw route, cached maps and prior watch build.
        write_json(folder/'plan.json',plan)

    def update_options(self,payload):
        with self.lock:
            self.idle()
            if not self.current: raise PackError('请先导入 GPX。')
            old = self.projects/self.current
            content = {'gpx':base64.b64encode((old/'route.gpx').read_bytes()).decode(),
                       'filename':json.loads((old/'plan.json').read_text())['route']['name']+'.gpx', **payload}
            return self.import_route(content)

    def start(self, action, selected_device='fr255'):
        with self.lock:
            self.idle()
            if not self.current: raise PackError('请先导入 GPX。')
            if action not in ('download','build','install'): raise PackError('不支持的操作。')
            if selected_device not in DEVICES: raise PackError('手表型号不支持。')
            self.upgrade_plan(self.projects/self.current)
            if action=='install' and not self.program_ready(self.projects/self.current):
                raise PackError('请先重新生成手表地图，以加入最新的预览功能。')
            self.cancel.clear()
            self.job = {'active':True,'stage':action,'message':'正在准备…','progress':0}
            threading.Thread(target=self.run,args=(self.current,action,selected_device),daemon=True).start()

    def progress(self, done,total,message):
        with self.lock:
            self.job.update(message=message,progress=round(done/max(1,total)*100),done=done,total=total)

    def run(self, identity, action, selected_device):
        folder = self.projects/identity
        try:
            plan = json.loads((folder/'plan.json').read_text())
            if action in ('download','build'):
                download_tiles(plan,self.cache,self.cancel,self.progress)
            if action=='build':
                # Rebuild only this generated output; raw GPX and cached maps are retained.
                watch = folder/'watch'
                if watch.exists(): shutil.rmtree(watch)
                generate_resources(plan,self.cache,watch,self.cancel,self.progress)
                self.progress(0,1,'正在生成适合手表的程序…')
                compile_program(watch,selected_device,self.data/'private',self.cancel)
            if action=='install':
                self.progress(0,1,'正在安装并回读校验，请保持 USB 连接…')
                result = install(folder/'watch',self.data/'backups')
                write_json(folder/'installation.json',result)
            message = {'download':'地图已下载。可拖动、缩放预览，确认后生成手表地图。',
                       'build':'运动地图和赛道预览已生成。连接 255，点击安装即可。',
                       'install':'两个程序均已写入并校验。拔线后从表盘按 START，打开“赛道预览”；徒步和越野跑继续使用原有地图页。'}[action]
            with self.lock: self.job.update(active=False,stage=action+'_done',message=message,progress=100)
        except Exception as exc:
            with (folder/'last-error.log').open('w') as log: traceback.print_exc(file=log)
            message = str(exc) if isinstance(exc,(PackError,OSError)) else '操作未完成，详细信息已保存在本机日志。'
            with self.lock: self.job.update(active=False,stage='paused' if isinstance(exc,Cancelled) else 'error',message=message)


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def respond(self, status, data, content_type='application/json; charset=utf-8'):
        if not isinstance(data,bytes): data=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'self'; img-src 'self' blob: data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(data)

    def authorized(self):
        expected = '127.0.0.1:%d'%self.server.server_port
        if self.headers.get('Host')!=expected:
            self.respond(403,{'error':'不允许的访问来源。'});return False
        origin = self.headers.get('Origin')
        if origin and origin!='http://'+expected:
            self.respond(403,{'error':'不允许的访问来源。'});return False
        if not hmac.compare_digest(self.headers.get('X-Trail-Token',''),self.server.token):
            self.respond(403,{'error':'请从 Mac 应用重新打开此窗口。'});return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        assets = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}
        if path in assets:
            asset = ROOT/'mac/web'/assets[path]
            return self.respond(200,asset.read_bytes(),mimetypes.guess_type(asset)[0] or 'text/plain')
        if not self.authorized(): return
        try:
            if path=='/api/state': return self.respond(200,self.server.desktop.summary())
            if path.startswith('/api/tile/'):
                _,_,_,identity,z,x,y = path.split('/')
                if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity): raise PackError('地图不存在。')
                tile = [int(z),int(x),int(y)]
                plan = json.loads((self.server.desktop.projects/identity/'plan.json').read_text())
                if tile not in plan['tiles']: raise PackError('超出本次地图范围。')
                # The desktop draws a crisp GPX overlay itself. Only watch
                # resources need the route baked into each raster tile.
                image = decode_tile((self.server.desktop.cache/tile_path(tile)).read_bytes())
                data=io.BytesIO();image.save(data,format='PNG')
                return self.respond(200,data.getvalue(),'image/png')
            self.respond(404,{'error':'未找到内容。'})
        except (ValueError,OSError,KeyError) as exc: self.respond(404,{'error':str(exc)})

    def do_POST(self):
        if not self.authorized(): return
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<=size<=28*1024*1024: raise PackError('文件过大。')
            payload=json.loads(self.rfile.read(size) or b'{}')
            if not isinstance(payload,dict):
                raise PackError('请求格式不正确，请重新选择路线后重试。')
            desktop=self.server.desktop
            if self.path=='/api/import': return self.respond(200,desktop.import_route(payload))
            if self.path=='/api/options': return self.respond(200,desktop.update_options(payload))
            if self.path=='/api/select': desktop.select(payload['id'])
            elif self.path=='/api/courses': desktop.scan_courses()
            elif self.path=='/api/import-course': desktop.import_course(payload)
            elif self.path=='/api/start': desktop.start(payload['action'],payload.get('device','fr255'))
            elif self.path=='/api/cancel': desktop.cancel_job()
            elif self.path=='/api/device':
                with desktop.lock:
                    desktop.idle()
                    desktop.device=detect()
            else: return self.respond(404,{'error':'不支持的操作。'})
            self.respond(200,desktop.summary())
        except (ValueError,OSError,KeyError,TypeError) as exc: self.respond(400,{'error':str(exc)})


def create_server(data,port=0):
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.token=secrets.token_urlsafe(32)
    server.desktop=Desktop(data)
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--data',type=Path,default=ROOT/'build/desktop-data')
    parser.add_argument('--port',type=int,default=0)
    args=parser.parse_args()
    server=create_server(args.data,args.port)
    print('http://127.0.0.1:%d/#%s'%(server.server_port,server.token),flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
