"""Small, resumable OpenTopoMap downloads and native Garmin resource generation."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from offline_pack import PackError, make_plan, project, tile_path, write_json, WORLD

ATTRIBUTION = "Map data © OpenStreetMap contributors, SRTM | Map style © OpenTopoMap (CC BY-SA 3.0)"
TILE_URL = "https://a.tile.opentopomap.org/{z}/{x}/{y}.png"
USER_AGENT = "BreadcrumbTrail/0.2 (https://github.com/yuxuehong/breadcrumb-garmin)"
MAX_TILES = 256  # All detail levels combined; never bulk-download a province.
APP_ID = "9fdc9173456e49068ea76a0b21a73c25"
PREVIEW_APP_ID = "53b0c46698f34030a7b94a1f65adb0b7"
PROGRAM_VERSION = 5
COVERAGE_VERSION = 1
WATCH_PADDING = 132  # Half of the largest 255 screen plus rounding allowance.
FIT_DIAMETER = 194  # Smallest 255S screen (218 px), with a 12 px round-edge margin.
DEVICES = {"fr255": "Forerunner 255", "fr255m": "Forerunner 255 Music",
           "fr255s": "Forerunner 255S", "fr255sm": "Forerunner 255S Music"}
WATCH_STRINGS = {
    'AppName': ('Breadcrumb Trail', 'Breadcrumb Trail'),
    'PreviewAppName': ('Race Preview', '赛道预览'),
    'OneField': ('Use 1-field page', '请设为单字段全屏'),
    'Following': ('FOLLOW GPS', '跟随定位'),
    'Outside': ('OUTSIDE MAP', '当前位置超出地图'),
    'Preview': ('ROUTE PREVIEW', '赛道预览'),
    'ZoomKeys': ('UP + / DOWN -', 'UP 放大 / DOWN 缩小'),
    'NorthSouthKeys': ('UP north / DOWN south', 'UP 向北 / DOWN 向南'),
    'WestEastKeys': ('UP west / DOWN east', 'UP 向西 / DOWN 向东'),
    'ControlKeys': ('START mode / hold UP menu', 'START 切换 / 长按UP菜单'),
    'BackToRun': ('BACK to activity', '按 BACK 返回运动'),
    'OpenSettings': ('Open Race Preview', '浏览：打开赛道预览'),
    'MapMenu': ('Map options', '地图操作'),
    'FullRoute': ('View full route', '查看全程'),
    'ToStart': ('Go to start', '回到起点'),
    'FollowAction': ('Follow GPS', '恢复跟随定位'),
    'ZoomMode': ('Zoom map', '缩放地图'),
    'NorthSouthMode': ('Move north / south', '南北移动'),
    'WestEastMode': ('Move west / east', '东西移动'),
    'MapAbout': ('Map sources', '地图来源'),
    'AttributionData': ('© OpenStreetMap contributors', '© OpenStreetMap contributors'),
    'AttributionTerrain': ('Elevation: SRTM', 'Elevation: SRTM'),
    'AttributionStyle': ('Map style © OpenTopoMap', 'Map style © OpenTopoMap'),
    'AttributionLicense': ('CC BY-SA 3.0', 'CC BY-SA 3.0'),
    'AttributionOsmUrl': ('openstreetmap.org/copyright', 'openstreetmap.org/copyright'),
    'AttributionMapUrl': ('opentopomap.org/about', 'opentopomap.org/about'),
    'AttributionLicenseUrl': ('creativecommons.org/licenses/by-sa/3.0/', 'creativecommons.org/licenses/by-sa/3.0/'),
    'AttributionContinue': ('START: view map', 'START 查看地图'),
}


class Cancelled(PackError):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled("已暂停。已下载的地图已保存，下次可继续。")


def decode_tile(data):
    if len(data) > 2 * 1024 * 1024:
        raise PackError("地图服务器返回的图像过大。")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format != "PNG" or image.size != (256, 256):
                raise PackError("地图服务器没有返回标准地图切片。")
            image.load()
            return image.convert("RGB")
    except (OSError, ValueError) as exc:
        raise PackError("地图图像不完整，请重试。") from exc


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    try:
        with temporary.open("wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def download_tiles(plan, cache, cancel, progress, opener=urllib.request.urlopen):
    """One request at a time, at most one per second, retry bounded and cancellable."""
    if len(plan["tiles"]) > MAX_TILES:
        raise PackError("单次最多准备 256 张地图，请缩小路线周边范围。")
    next_request = 0.0
    downloaded = 0
    for index, tile in enumerate(plan["tiles"]):
        check_cancel(cancel)
        destination = Path(cache) / tile_path(tile)
        if destination.exists():
            try:
                decode_tile(destination.read_bytes())
                progress(index + 1, len(plan["tiles"]), "使用已下载的地图")
                continue
            except (PackError, OSError):
                pass
        z, x, y = tile
        request = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y),
                                         headers={"User-Agent": USER_AGENT, "Accept": "image/png"})
        for attempt in range(3):
            if cancel.wait(max(0, next_request - time.monotonic())):
                check_cancel(cancel)
            next_request = time.monotonic() + 1.0
            try:
                with opener(request, timeout=25) as response:
                    if urllib.parse.urlparse(response.geturl()).scheme != "https":
                        raise PackError("地图服务器返回了不安全的下载地址。")
                    data = response.read(2 * 1024 * 1024 + 1)
                decode_tile(data)
                check_cancel(cancel)
                atomic_bytes(destination, data)
                downloaded += 1
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (403, 404):
                    raise PackError("图源暂不提供这片地图（HTTP %d），没有生成不完整地图。" % exc.code) from exc
                if exc.code == 429:
                    # Stop instead of putting more load on a public server.
                    raise PackError("图源正在限流。已保存下载进度，请稍后继续。") from exc
                if attempt == 2:
                    raise PackError("地图服务暂不可用，已保留下载进度。") from exc
                next_request = time.monotonic() + (attempt + 1) * 3
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == 2:
                    raise PackError("下载中断，请检查网络后重试；已下载的地图会继续使用。") from exc
                next_request = time.monotonic() + (attempt + 1) * 3
        progress(index + 1, len(plan["tiles"]), "下载等高线地图")
    return downloaded


def plan_route(gpx, zoom=15, buffer_m=300):
    if zoom not in (14, 15):
        raise PackError("请选择标准或详细地图。")
    if buffer_m not in (150, 300, 600, 1000):
        raise PackError("请选择列表中的路线周边范围。")
    plan = make_plan(gpx, [zoom], buffer_m, MAX_TILES)
    # GPX may have distant segments. Avoid a huge preview and integer key span.
    xs, ys = [t[1] for t in plan["tiles"]], [t[2] for t in plan["tiles"]]
    if max(xs) - min(xs) > 128 or max(ys) - min(ys) > 128:
        raise PackError("路线跨度过大，请按赛事分段导出 GPX。")
    # Each zoom gets real, complete map tiles. Shrinking a narrow corridor
    # cannot reveal geography that was never downloaded.
    points = [p for segment in plan['route']['segments'] for p in segment]
    projected = [project(p) for p in points]
    left, top = min(p[0] for p in projected), min(p[1] for p in projected)
    right, bottom = max(p[0] for p in projected), max(p[1] for p in projected)
    diameter = math.hypot(right-left, bottom-top)*256*2**zoom
    minimum = max(0, min(zoom-1, zoom+math.floor(math.log2(FIT_DIAMETER/max(1, diameter)))))
    radius = buffer_m/(WORLD*math.cos(math.radians(max(abs(p[0]) for p in points))))
    tiles, coverage = [], {}
    for level in range(minimum, zoom+1):
        n = 2**level
        padding = max(WATCH_PADDING/256/n, radius)
        bounds = [math.floor((left-padding)*n), math.floor((top-padding)*n),
                  math.floor((right+padding)*n), math.floor((bottom+padding)*n)]
        if bounds[0] < 0 or bounds[1] < 0 or bounds[2] >= n or bounds[3] >= n:
            raise PackError("路线靠近地图投影边界，暂不能生成完整屏幕底图。请缩小范围或拆分路线。")
        count = (bounds[2]-bounds[0]+1)*(bounds[3]-bounds[1]+1)
        if len(tiles)+count > MAX_TILES:
            raise PackError("铺满屏幕所需地图超过 256 张。请选择标准细节、较小周边范围，或按比赛分段导入；没有删减地图。")
        tiles.extend([level,x,y] for x in range(bounds[0],bounds[2]+1)
                     for y in range(bounds[1],bounds[3]+1))
        coverage[str(level)] = bounds
    plan.update(tiles=tiles, coverage=coverage, coverage_version=COVERAGE_VERSION,
                preview_min_zoom=minimum)
    plan["attribution"] = ATTRIBUTION
    plan["distance_m"] = route_distance(plan["route"])
    plan["point_count"] = sum(map(len, plan["route"]["segments"]))
    return plan


def route_distance(route):
    distance = 0.0
    for segment in route["segments"]:
        for a, b in zip(segment, segment[1:]):
            lat1, lat2 = math.radians(a[0]), math.radians(b[0])
            h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(math.radians(b[1]-a[1])/2)**2
            distance += 12742000 * math.asin(min(1, math.sqrt(h)))
    return distance


def tile_image(plan, cache, tile):
    """Bake the complete route into map resources: bounded watch RAM, no point truncation."""
    z, x, y = tile
    image = decode_tile((Path(cache) / tile_path(tile)).read_bytes())
    return paint_route(image, plan['route'], tile)


def paint_route(image, route, tile):
    """Keep route strokes legible at every offline preview level."""
    z, x, y = tile
    draw = ImageDraw.Draw(image)
    factor = 256 * 2**z
    for segment in route["segments"]:
        points = [(project(p)[0]*factor-x*256, project(p)[1]*factor-y*256) for p in segment]
        # Clip through Pillow; draw segments separately, never bridge GPX gaps.
        if len(points) > 1:
            draw.line(points, fill="white", width=7, joint="curve")
            draw.line(points, fill=(0, 65, 210), width=3, joint="curve")
    start = route["segments"][0][0]
    sx, sy = project(start)
    sx, sy = sx*factor-x*256, sy*factor-y*256
    draw.ellipse((sx-5, sy-5, sx+5, sy+5), fill=(0, 150, 50), outline="white", width=2)
    return image


def preview_bounds(plan):
    z = plan['zooms'][0]
    base_tiles = [t for t in plan['tiles'] if t[0] == z]
    ox = min(t[1] for t in base_tiles)
    oy = min(t[2] for t in base_tiles)
    points = [project(p) for segment in plan['route']['segments'] for p in segment]
    xs = [(p[0]*2**z-ox)*256 for p in points]
    ys = [(p[1]*2**z-oy)*256 for p in points]
    bounds = [min(xs), min(ys), max(xs), max(ys)]
    return bounds, plan['preview_min_zoom']


def generate_resources(plan, cache, output, cancel, progress):
    if plan.get('coverage_version') != COVERAGE_VERSION:
        raise PackError("请重新打开这场比赛，补齐各级缩放所需的满屏地图。")
    base, minimum = plan['zooms'][0], plan['preview_min_zoom']
    coverage = plan['coverage']
    invalid = "地图覆盖不完整，请重新导入 GPX；没有生成带空白的地图。"
    if (type(minimum) is not int or base not in (14,15) or not 0 <= minimum <= base
            or set(coverage) != {str(z) for z in range(minimum,base+1)}):
        raise PackError(invalid)
    expected = []
    for level in range(minimum,base+1):
        b = coverage[str(level)]
        if (not isinstance(b,list) or len(b)!=4 or any(type(v) is not int for v in b)
                or not 0 <= b[0] < b[2] < 2**level or not 0 <= b[1] < b[3] < 2**level):
            raise PackError(invalid)
        count = (b[2]-b[0]+1)*(b[3]-b[1]+1)
        if len(expected)+count > MAX_TILES:
            raise PackError(invalid)
        expected.extend([level,x,y] for x in range(b[0],b[2]+1) for y in range(b[1],b[3]+1))
    if expected != plan['tiles']:
        raise PackError("地图覆盖不完整，请重新导入 GPX；没有生成带空白的地图。")
    if any(not (Path(cache)/tile_path(t)).is_file() for t in expected):
        raise PackError("地图尚未下载完整，请先完成下载。")
    output = Path(output)
    if output.exists():
        raise PackError("构建目录已存在，请重新选择本次地图。")
    output.mkdir(parents=True)
    shutil.copytree(ROOT / "watch-offline" / "source", output / "source")
    resources = output / "resources"
    resources.mkdir()
    shutil.copy(ROOT / "resources/drawables/launcher_icon.png", resources / "icon.png")
    tiles = plan["tiles"]
    z = plan["zooms"][0]
    base_tiles = [t for t in tiles if t[0] == z]
    ox, oy = min(t[1] for t in base_tiles), min(t[2] for t in base_tiles)
    start = project(plan["route"]["segments"][0][0])
    xml = ['<resources>', '<bitmap id="LauncherIcon" filename="icon.png" scaleX="40" scaleY="40"/>']
    records = []
    for i, tile in enumerate(tiles):
        check_cancel(cancel)
        image = tile_image(plan, cache, tile)
        path = resources / ("tile_%d.png" % i)
        image.save(path)
        xml.append('<bitmap id="Tile%d" filename="tile_%d.png" dithering="none" compress="true"/>' % (i, i))
        records.append({"tile": tile, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        progress(i+1, len(tiles), "准备手表地图")
    bounds, minimum = preview_bounds(plan)
    levels = {level: [(t,'Tile%d'%i) for i,t in enumerate(tiles) if t[0] == level]
              for level in range(minimum,z+1)}
    xml.append('</resources>')
    (resources / "resources.xml").write_text('\n'.join(xml), encoding="utf-8")
    for locale, column in (('resources',0),('resources-zhs',1)):
        directory = output/locale
        directory.mkdir(exist_ok=True)
        (directory/'strings.xml').write_text('<resources>'+''.join(
            '<string id="%s">%s</string>'%(key,escape(labels[column]))
            for key,labels in WATCH_STRINGS.items())+'</resources>',encoding='utf-8')
    source = '''import Toybox.Lang;
module OfflineMap {
    const ZOOM = %d;
    const MIN_ZOOM = %d;
    const ORIGIN_X = %d;
    const ORIGIN_Y = %d;
    const START_X = %.8fd;
    const START_Y = %.8fd;
    const ROUTE_ID = "%s";
    const MIN_X = %.8fd;
    const MIN_Y = %.8fd;
    const MAX_X = %.8fd;
    const MAX_Y = %.8fd;
    const MAP_WIDTH = %d;
    const MAP_HEIGHT = %d;
    function index(zoom) { %s return {}; }
    function extent(zoom) { %s return [0,0,MAP_WIDTH,MAP_HEIGHT]; }
}
''' % (z, minimum, ox, oy, (start[0]*2**z-ox)*256, (start[1]*2**z-oy)*256,
       # Saved camera coordinates belong to this exact pixel grid, not just GPX.
       # Changing detail or corridor coverage must not restore an old camera.
       hashlib.sha256(json.dumps({'route':plan['route'],'tiles':tiles,'zoom':z,'coverage':COVERAGE_VERSION},sort_keys=True).encode()).hexdigest()[:20], *bounds,
       (max(t[1] for t in base_tiles)-ox+1)*256, (max(t[2] for t in base_tiles)-oy+1)*256,
       '\n'.join('if (zoom == %d) { return {%s}; }'%(level, ',\n'.join(
           '%d => Rez.Drawables.%s'%((tile[1]-ox//2**(z-level))*65536+tile[2]-oy//2**(z-level), name)
           for tile,name in items)) for level,items in sorted(levels.items())),
       '\n'.join('if (zoom == %s) { return [%s]; }'%(level,','.join(str(v) for v in (
           (b[0]*2**(z-int(level))-ox)*256,(b[1]*2**(z-int(level))-oy)*256,
           ((b[2]+1)*2**(z-int(level))-ox)*256,((b[3]+1)*2**(z-int(level))-oy)*256)))
           for level,b in plan['coverage'].items()))
    (output / "source/MapIndex.mc").write_text(source, encoding="utf-8")
    (output / "manifest.xml").write_text('''<?xml version="1.0"?>
<iq:manifest version="3" xmlns:iq="http://www.garmin.com/xml/connectiq">
<iq:application id="%s" type="datafield" name="@Strings.AppName" entry="TrailApp" launcherIcon="@Drawables.LauncherIcon" minApiLevel="4.0.0">
<iq:products>%s</iq:products><iq:permissions><iq:uses-permission id="Positioning"/></iq:permissions>
<iq:languages><iq:language>eng</iq:language><iq:language>zhs</iq:language></iq:languages><iq:barrels/>
</iq:application></iq:manifest>
''' % (APP_ID, ''.join('<iq:product id="%s"/>' % d for d in DEVICES)), encoding="utf-8")
    (output / "monkey.jungle").write_text('project.manifest = manifest.xml\nbase.sourcePath = source\nbase.resourcePath = resources\nbase.lang.zhs = resources-zhs\n', encoding="utf-8")
    # A separate app has its own visible entry and buttons before a workout.
    # It deliberately has no GPS or activity recording permission.
    preview_manifest = (output/'manifest.xml').read_text().replace(APP_ID,PREVIEW_APP_ID).replace(
        'type="datafield"','type="watch-app"').replace('name="@Strings.AppName"',
        'name="@Strings.PreviewAppName"').replace('entry="TrailApp"','entry="TrailViewerApp"').replace(
        '<iq:permissions><iq:uses-permission id="Positioning"/></iq:permissions>','<iq:permissions/>')
    (output/'preview-manifest.xml').write_text(preview_manifest,encoding='utf-8')
    (output/'preview.jungle').write_text((output/'monkey.jungle').read_text().replace(
        'project.manifest = manifest.xml','project.manifest = preview-manifest.xml'),encoding='utf-8')
    write_json(output / "map-manifest.json", {"attribution": ATTRIBUTION, "tiles": records,
               "route_sha256": hashlib.sha256(json.dumps(plan["route"], sort_keys=True).encode()).hexdigest(),
               "zoom": z, "preview_min_zoom":minimum,
               "coverage_version":COVERAGE_VERSION,"coverage":plan['coverage'],
               "preview_assets":sum(len(items) for level,items in levels.items() if level!=z),
               "route_bounds":bounds, "device_capacity_verified": False})
    return output


def find_sdk():
    sdk_home = Path.home() / "Library/Application Support/Garmin/ConnectIQ"
    candidates = []
    if os.environ.get("CIQ_SDK"):
        candidates.append(Path(os.environ["CIQ_SDK"]))
    current = sdk_home / "current-sdk.cfg"
    if current.exists():
        candidates.append(Path(current.read_text().strip()))
    candidates += sorted((sdk_home / "Sdks").glob("connectiq-sdk-mac-*"), reverse=True)
    for path in candidates:
        if (path / "bin/monkeyc").exists():
            return path
    raise PackError("请先打开 Garmin SDK Manager，下载 SDK 和 Forerunner 255 设备文件。")


def java_env():
    env = os.environ.copy()
    for home in (Path('/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home'),
                 Path('/usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home')):
        if (home / 'bin/java').exists():
            env['JAVA_HOME'] = str(home)
            env['PATH'] = str(home / 'bin') + os.pathsep + env.get('PATH', '')
            break
    return env


def compile_program(folder, device, key_dir, cancel):
    if device not in DEVICES:
        raise PackError("仅支持 Forerunner 255 系列。")
    sdk = find_sdk()
    device_file = Path.home()/"Library/Application Support/Garmin/ConnectIQ/Devices"/device/"compiler.json"
    if not device_file.exists():
        raise PackError("请在 SDK Manager 的 Devices 中只下载 %s 设备文件。" % DEVICES[device])
    # Only advertise the selected device; other device packs need not be downloaded.
    namespace = "http://www.garmin.com/xml/connectiq"
    ET.register_namespace('iq',namespace)
    for filename in ('manifest.xml','preview-manifest.xml'):
        manifest = ET.parse(Path(folder)/filename)
        products = manifest.getroot().find('.//{%s}products'%namespace)
        for product in list(products): products.remove(product)
        ET.SubElement(products,'{%s}product'%namespace,{'id':device})
        manifest.write(Path(folder)/filename,encoding='utf-8',xml_declaration=True)
    key_dir = Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)
    key = key_dir / "developer.der"
    if not key.exists():
        # Stable, local signing key. Never included in source control or app downloads.
        generated = subprocess.run(['openssl','genrsa','4096'], capture_output=True)
        result = subprocess.run(['openssl','pkcs8','-topk8','-inform','PEM','-outform','DER','-nocrypt'],
                                input=generated.stdout, capture_output=True)
        if generated.returncode or result.returncode:
            raise PackError("无法生成本机编译密钥。")
        atomic_bytes(key, result.stdout)
        key.chmod(0o600)
    (Path(folder)/'program.json').unlink(missing_ok=True)
    for jungle,filename,logname in (('monkey.jungle','BCTRAIL.prg','compile.log'),
                                   ('preview.jungle','BCPREVW.prg','preview-compile.log')):
        check_cancel(cancel)
        output = Path(folder)/filename
        command = [str(sdk/'bin/monkeyc'), '-f', jungle, '-d', device, '-o', str(output),
                   '-y', str(key), '-r', '-w']
        with (Path(folder)/logname).open('w') as log:
            process = subprocess.Popen(command, cwd=folder, env=java_env(), stdout=log, stderr=subprocess.STDOUT)
            started = time.monotonic()
            while process.poll() is None:
                if cancel.wait(0.2) or time.monotonic()-started > 180:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait()
                    check_cancel(cancel)
                    raise PackError("手表程序编译超时，请查看构建日志。")
            if process.returncode or not output.is_file():
                raise PackError("手表程序编译失败；详细信息已保存到 %s。"%logname)
    output = Path(folder)/'BCTRAIL.prg'
    preview = Path(folder)/'BCPREVW.prg'
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    write_json(Path(folder)/'program.json', {"device":device, "sha256":digest, "bytes":output.stat().st_size,
               "app_id":APP_ID, "program_version":PROGRAM_VERSION,
               "preview":{"filename":preview.name,"app_id":PREVIEW_APP_ID,"bytes":preview.stat().st_size,
                          "sha256":hashlib.sha256(preview.read_bytes()).hexdigest()},
               "total_bytes":output.stat().st_size+preview.stat().st_size,
               "sdk":sdk.name, "hardware_verified":False})
    return output
