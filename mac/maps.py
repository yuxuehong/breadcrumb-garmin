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
from offline_pack import PackError, make_plan, project, tile_path, write_json

ATTRIBUTION = "Map data © OpenStreetMap contributors, SRTM | Map style © OpenTopoMap (CC BY-SA 3.0)"
TILE_URL = "https://a.tile.opentopomap.org/{z}/{x}/{y}.png"
USER_AGENT = "BreadcrumbTrail/0.2 (https://github.com/yuxuehong/breadcrumb-garmin)"
MAX_TILES = 256  # Small race corridors only; never bulk-download a province.
APP_ID = "9fdc9173456e49068ea76a0b21a73c25"
DEVICES = {"fr255": "Forerunner 255", "fr255m": "Forerunner 255 Music",
           "fr255s": "Forerunner 255S", "fr255sm": "Forerunner 255S Music"}


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
    draw = ImageDraw.Draw(image)
    factor = 256 * 2**z
    for segment in plan["route"]["segments"]:
        points = [(project(p)[0]*factor-x*256, project(p)[1]*factor-y*256) for p in segment]
        # Clip through Pillow; draw segments separately, never bridge GPX gaps.
        if len(points) > 1:
            draw.line(points, fill="white", width=7, joint="curve")
            draw.line(points, fill=(0, 65, 210), width=3, joint="curve")
    start = plan["route"]["segments"][0][0]
    sx, sy = project(start)
    sx, sy = sx*factor-x*256, sy*factor-y*256
    draw.ellipse((sx-5, sy-5, sx+5, sy+5), fill=(0, 150, 50), outline="white", width=2)
    return image


def generate_resources(plan, cache, output, cancel, progress):
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
    ox, oy = min(t[1] for t in tiles), min(t[2] for t in tiles)
    start = project(plan["route"]["segments"][0][0])
    entries, xml = [], ['<resources>', '<bitmap id="LauncherIcon" filename="icon.png" scaleX="40" scaleY="40"/>',
                        '<string id="AppName">Breadcrumb Trail</string>']
    records = []
    for i, tile in enumerate(tiles):
        check_cancel(cancel)
        image = tile_image(plan, cache, tile)
        path = resources / ("tile_%d.png" % i)
        image.save(path)
        xml.append('<bitmap id="Tile%d" filename="tile_%d.png" dithering="none" compress="true"/>' % (i, i))
        entries.append("%d => Rez.Drawables.Tile%d" % ((tile[1]-ox)*65536+tile[2]-oy, i))
        records.append({"tile": tile, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        progress(i+1, len(tiles), "准备手表地图")
    xml.append('</resources>')
    (resources / "resources.xml").write_text('\n'.join(xml), encoding="utf-8")
    source = '''import Toybox.Lang;
module OfflineMap {
    const ZOOM = %d;
    const ORIGIN_X = %d;
    const ORIGIN_Y = %d;
    const START_X = %.8fd;
    const START_Y = %.8fd;
    function index() { return {%s}; }
}
''' % (z, ox, oy, (start[0]*2**z-ox)*256, (start[1]*2**z-oy)*256, ',\n'.join(entries))
    (output / "source/MapIndex.mc").write_text(source, encoding="utf-8")
    (output / "manifest.xml").write_text('''<?xml version="1.0"?>
<iq:manifest version="3" xmlns:iq="http://www.garmin.com/xml/connectiq">
<iq:application id="%s" type="datafield" name="@Strings.AppName" entry="TrailApp" launcherIcon="@Drawables.LauncherIcon" minApiLevel="4.0.0">
<iq:products>%s</iq:products><iq:permissions><iq:uses-permission id="Positioning"/></iq:permissions>
<iq:languages><iq:language>eng</iq:language></iq:languages><iq:barrels/>
</iq:application></iq:manifest>
''' % (APP_ID, ''.join('<iq:product id="%s"/>' % d for d in DEVICES)), encoding="utf-8")
    (output / "monkey.jungle").write_text('project.manifest = manifest.xml\nbase.sourcePath = source\nbase.resourcePath = resources\n', encoding="utf-8")
    write_json(output / "map-manifest.json", {"attribution": ATTRIBUTION, "tiles": records,
               "route_sha256": hashlib.sha256(json.dumps(plan["route"], sort_keys=True).encode()).hexdigest(),
               "zoom": z, "device_capacity_verified": False})
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
    manifest = ET.parse(Path(folder)/'manifest.xml')
    products = manifest.getroot().find('.//{%s}products'%namespace)
    for product in list(products): products.remove(product)
    ET.SubElement(products,'{%s}product'%namespace,{'id':device})
    manifest.write(Path(folder)/'manifest.xml',encoding='utf-8',xml_declaration=True)
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
    output = Path(folder) / 'BCTRAIL.prg'
    command = [str(sdk/'bin/monkeyc'), '-f', 'monkey.jungle', '-d', device, '-o', str(output),
               '-y', str(key), '-r', '-w']
    with (Path(folder)/'compile.log').open('w') as log:
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
        raise PackError("手表程序编译失败；详细信息已保存到 compile.log。")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    write_json(Path(folder)/'program.json', {"device":device, "sha256":digest, "bytes":output.stat().st_size,
               "app_id":APP_ID, "sdk":sdk.name, "hardware_verified":False})
    return output
