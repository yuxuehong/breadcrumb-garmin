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
import threading
import traceback
import urllib.parse
import uuid

from maps import (ROOT, PackError, Cancelled, plan_route, download_tiles, tile_image,
                  generate_resources, compile_program, write_json, find_sdk, DEVICES)
from device import detect, install


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

    def summary(self):
        with self.lock:
            history = []
            for p in sorted(self.projects.glob('*/plan.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
                try:
                    plan = json.loads(p.read_text())
                    history.append({'id':p.parent.name,'name':plan['route']['name'],
                                    'ready':(p.parent/'watch/program.json').exists()})
                except (ValueError,KeyError,OSError):
                    continue
            result = {'job':dict(self.job),'device':self.device,'history':history}
            if self.current:
                folder = self.projects/self.current
                result.update({'id':self.current,'plan':json.loads((folder/'plan.json').read_text()),
                               'ready':(folder/'watch/program.json').exists()})
                if result['ready']:
                    result['program'] = json.loads((folder/'watch/program.json').read_text())
            try: result['sdk'] = find_sdk().name
            except PackError: result['sdk'] = None
            return result

    def idle(self):
        if self.job['active']:
            raise PackError('当前任务尚未结束，请等待或暂停后再操作。')

    def import_route(self, payload):
        with self.lock:
            self.idle()
            try: raw = base64.b64decode(payload['gpx'],validate=True)
            except (KeyError,ValueError) as exc: raise PackError('请选择有效的 GPX 文件。') from exc
            if len(raw)>20*1024*1024:
                raise PackError('GPX 文件过大，请按单场比赛导出。')
            identity = uuid.uuid4().hex
            folder = self.projects/identity
            folder.mkdir()
            try:
                (folder/'route.gpx').write_bytes(raw)
                plan = plan_route(folder/'route.gpx',int(payload.get('zoom',15)),int(payload.get('buffer',300)))
                if plan['route']['name']=='route':
                    plan['route']['name'] = Path(str(payload.get('filename','比赛路线.gpx'))).stem[:100]
                write_json(folder/'plan.json',plan)
            except Exception:
                shutil.rmtree(folder)
                raise
            self.current = identity
            self.job = {'active':False,'stage':'planned','message':'路线已导入，下载等高线地图后可预览。','progress':0}
        return self.summary()

    def select(self, identity):
        with self.lock:
            self.idle()
            if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity) or not (self.projects/identity/'plan.json').is_file():
                raise PackError('找不到这场比赛。')
            self.current = identity
            self.job = {'active':False,'stage':'selected','message':'已打开保存的比赛。','progress':0}

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
            if action=='install' and not (self.projects/self.current/'watch/program.json').exists():
                raise PackError('请先生成手表地图。')
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
                       'build':'手表地图已生成。连接 255，点击安装即可。',
                       'install':'文件已写入并校验。拔线后在跑步活动中添加 Breadcrumb Trail 数据页。'}[action]
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
                image = tile_image(plan,self.server.desktop.cache,tile)
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
            desktop=self.server.desktop
            if self.path=='/api/import': return self.respond(200,desktop.import_route(payload))
            if self.path=='/api/options': return self.respond(200,desktop.update_options(payload))
            if self.path=='/api/select': desktop.select(payload['id'])
            elif self.path=='/api/start': desktop.start(payload['action'],payload.get('device','fr255'))
            elif self.path=='/api/cancel':
                if desktop.job.get('stage')=='install': raise PackError('安装期间请保持连接，等待校验完成。')
                desktop.cancel.set()
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
