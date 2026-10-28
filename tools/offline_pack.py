#!/usr/bin/env python3
"""Local GPX corridor planning and verified raster packages (Python 3.9+).

This preparatory format is NOT a Garmin-installable map or PRG. It deliberately
does not fetch tiles: use a source licensed for offline use, rendered as XYZ PNG.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
import zlib

SCHEMA = "breadcrumb-offline-plan-v1"
MAX_LAT = 85.05112878
EARTH_RADIUS = 6378137.0
WORLD = 2 * math.pi * EARTH_RADIUS


class PackError(ValueError):
    pass


class GpxTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise PackError("不支持包含 DTD 或实体定义的 GPX。")


def children(node, name):
    return [c for c in node if c.tag.rsplit("}", 1)[-1] == name]


def read_gpx(path):
    raw = Path(path).read_bytes()
    if len(raw) > 20 * 1024 * 1024:
        raise PackError("GPX 文件过大，请先按比赛路线导出。")
    # ElementTree has no external entity loader. Reject internal entity expansion.
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise PackError("不支持包含 DTD 或实体定义的 GPX。")
    try:
        root = ET.fromstring(raw, parser=ET.XMLParser(target=GpxTreeBuilder()))
    except ET.ParseError as exc:
        raise PackError("GPX 格式不正确。") from exc
    if root.tag.rsplit("}", 1)[-1] != "gpx":
        raise PackError("请选择 GPX 文件。")
    segments, names = [], []
    # Tracks take precedence when an export contains both track and route copies.
    tracks = children(root, "trk")
    containers = tracks or children(root, "rte")
    for container in containers:
        names.extend(n.text for n in children(container, "name") if n.text)
        groups = children(container, "trkseg") if tracks else [container]
        for group in groups:
            points = []
            for point in children(group, "trkpt" if tracks else "rtept"):
                try:
                    lat, lon = float(point.attrib["lat"]), float(point.attrib["lon"])
                    heights = children(point, "ele")
                    elevation = float(heights[0].text) if heights else None
                except (KeyError, TypeError, ValueError) as exc:
                    raise PackError("GPX 中存在无效坐标或海拔。") from exc
                if not (math.isfinite(lat) and math.isfinite(lon)
                        and -MAX_LAT < lat < MAX_LAT and -180 <= lon <= 180):
                    raise PackError("路线包含超出地图范围的坐标。")
                if elevation is not None and not math.isfinite(elevation):
                    raise PackError("GPX 中存在无效海拔。")
                if points and abs(lon - points[-1][1]) > 180:
                    raise PackError("首版暂不支持跨越国际日期变更线的路线。")
                points.append([lat, lon, elevation])
            if points:
                segments.append(points)
    if sum(map(len, segments)) < 2:
        raise PackError("GPX 至少需要两个路线点。")
    return {"name": names[0] if names else Path(path).stem, "segments": segments}


def project(point):
    lat, lon = point[:2]
    return ((lon + 180) / 360,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2)


def intersects(a, b, left, top, right, bottom):
    """Slab intersection of a complete line segment and a closed rectangle."""
    low, high = 0.0, 1.0
    for start, end, minimum, maximum in ((a[0], b[0], left, right),
                                       (a[1], b[1], top, bottom)):
        delta = end - start
        if delta == 0:
            if not minimum <= start <= maximum:
                return False
        else:
            entry, leave = sorted(((minimum - start) / delta, (maximum - start) / delta))
            low, high = max(low, entry), min(high, leave)
            if low > high:
                return False
    return True


def corridor_tiles(route, zooms, buffer_m, max_tiles):
    if not math.isfinite(buffer_m) or not 0 <= buffer_m <= 10000:
        raise PackError("周边范围应在 0 至 10000 米之间。")
    if not zooms or any(type(z) is not int or not 0 <= z <= 18 for z in zooms):
        raise PackError("地图层级应为 0 至 18 的整数。")
    if type(max_tiles) is not int or not 1 <= max_tiles <= 100000:
        raise PackError("地图数量预算应为 1 至 100000。")
    result, checks = set(), 0
    for zoom in sorted(set(zooms)):
        n = 2 ** zoom
        for segment in route["segments"]:
            pairs = zip(segment, segment[1:]) if len(segment) > 1 else [(segment[0], segment[0])]
            for first, last in pairs:
                a, b = project(first), project(last)
                # Conservative square buffer in Mercator; never bridge GPX gaps.
                edge_lat = min(MAX_LAT, max(abs(first[0]), abs(last[0]))
                               + math.degrees(buffer_m / EARTH_RADIUS))
                radius = buffer_m / (WORLD * math.cos(math.radians(edge_lat)))
                xmin = math.floor((min(a[0], b[0]) - radius) * n)
                xmax = math.floor((max(a[0], b[0]) + radius) * n)
                ymin = max(0, math.floor((min(a[1], b[1]) - radius) * n))
                ymax = min(n - 1, math.floor((max(a[1], b[1]) + radius) * n))
                checks += (xmax - xmin + 1) * (ymax - ymin + 1)
                if checks > 2000000:
                    raise PackError("路线范围或点数过大，请分段准备或降低地图层级。")
                for x in range(xmin, xmax + 1):
                    for y in range(ymin, ymax + 1):
                        if intersects(a, b, x / n - radius, y / n - radius,
                                      (x + 1) / n + radius, (y + 1) / n + radius):
                            result.add((zoom, x % n, y))
                            if len(result) > max_tiles:
                                raise PackError("超出本次地图数量预算，请缩小范围或降低地图层级；没有删减地图。")
    return [list(t) for t in sorted(result)]


def make_plan(gpx, zooms, buffer_m=300, max_tiles=256):
    route = read_gpx(gpx)
    return {"schema": SCHEMA, "route": route, "buffer_m": buffer_m,
            "zooms": sorted(set(zooms)), "max_tiles": max_tiles,
            "tiles": corridor_tiles(route, zooms, buffer_m, max_tiles),
            "device_capacity_verified": False}


def load_plan(path):
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        if plan["schema"] != SCHEMA:
            raise PackError("地图计划版本不支持。")
        route = plan["route"]
        if not isinstance(route["name"], str) or not route["segments"]:
            raise PackError("地图计划缺少路线。")
        for segment in route["segments"]:
            if not segment:
                raise PackError("地图计划包含空路线段。")
            for i, point in enumerate(segment):
                lat, lon, elevation = point
                if (not math.isfinite(lat) or not math.isfinite(lon)
                        or not -MAX_LAT < lat < MAX_LAT or not -180 <= lon <= 180
                        or (elevation is not None and not math.isfinite(elevation))):
                    raise PackError("地图计划包含无效坐标。")
                if i and abs(lon - segment[i - 1][1]) > 180:
                    raise PackError("地图计划包含不支持的跨日期变更线路段。")
        expected = corridor_tiles(route, plan["zooms"], plan["buffer_m"], plan["max_tiles"])
        if expected != plan["tiles"]:
            raise PackError("地图清单与路线覆盖范围不一致，请重新生成计划。")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError("地图计划格式不正确。") from exc
    return plan


def tile_path(tile):
    z, x, y = tile
    return Path(str(z), str(x), str(y) + ".png")


def checked_png(path):
    """Structural/CRC validation; image decoding is a later SDK build check."""
    if path.stat().st_size > 4 * 1024 * 1024:
        raise PackError("单张地图过大：" + str(path))
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PackError("地图不是 PNG，可能下载到了错误页面：" + str(path))
    pos, types = 8, []
    while pos + 12 <= len(data):
        length = struct.unpack_from(">I", data, pos)[0]
        end = pos + 12 + length
        if end > len(data):
            break
        kind, payload = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        crc = struct.unpack_from(">I", data, pos + 8 + length)[0]
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise PackError("地图文件损坏：" + str(path))
        if not types:
            if kind != b"IHDR" or length != 13:
                break
            width, height = struct.unpack_from(">II", payload)
            if width != height or width not in (64, 128, 256, 512):
                raise PackError("地图切片必须为 64、128、256 或 512 像素的正方形。")
        types.append(kind)
        pos = end
        if kind == b"IEND":
            if length or pos != len(data) or b"IDAT" not in types:
                break
            return data, width
    raise PackError("地图 PNG 不完整：" + str(path))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_package(plan, tiles_dir, output, attribution, max_bytes):
    output, tiles_dir = Path(output), Path(tiles_dir)
    if output.exists():
        raise PackError("输出目录已存在，请使用新的目录，以保留上次地图。")
    if not attribution.strip():
        raise PackError("请填写地图来源和署名信息。")
    if not math.isfinite(max_bytes) or max_bytes <= 0:
        raise PackError("离线包大小预算必须大于零。")
    missing = [str(tile_path(t)) for t in plan["tiles"] if not (tiles_dir / tile_path(t)).is_file()]
    if missing:
        raise PackError("缺少 %d 张地图，未生成离线包。示例：%s" % (len(missing), ", ".join(missing[:5])))
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".breadcrumb-", dir=output.parent))
    try:
        records, sizes, total = [], set(), 0
        for tile in plan["tiles"]:
            relative = tile_path(tile)
            data, width = checked_png(tiles_dir / relative)
            total += len(data)
            if total > max_bytes:
                raise PackError("地图超过本次大小预算，未生成离线包；没有删减地图。")
            sizes.add(width)
            destination = staging / "tiles" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            records.append({"tile": tile, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        if len(sizes) != 1:
            raise PackError("地图像素尺寸不一致，请使用同一套地图切片。")
        write_json(staging / "plan.json", plan)
        write_json(staging / "manifest.json", {
            "schema": "breadcrumb-offline-package-v1", "attribution": attribution,
            "plan_sha256": hashlib.sha256((staging / "plan.json").read_bytes()).hexdigest(),
            "tiles": records, "tile_pixels": sizes.pop(), "tile_bytes": total,
            "installable_on_watch": False,
            "status": "prepared-assets-not-a-garmin-prg"})
        if output.exists():
            raise PackError("输出目录已存在，未覆盖之前的地图。")
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return total


def verify_package(folder):
    folder = Path(folder)
    plan = load_plan(folder / "plan.json")
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise PackError("离线包清单格式不正确。")
    if (manifest.get("schema") != "breadcrumb-offline-package-v1"
            or manifest.get("plan_sha256") != hashlib.sha256((folder / "plan.json").read_bytes()).hexdigest()):
        raise PackError("路线计划校验失败。")
    records = manifest.get("tiles", [])
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise PackError("地图清单格式不正确。")
    if [r.get("tile") for r in records] != plan["tiles"]:
        raise PackError("地图清单不完整或顺序不正确。")
    total = 0
    for record in records:
        path = folder / "tiles" / tile_path(record["tile"])
        data, width = checked_png(path)
        total += len(data)
        if (hashlib.sha256(data).hexdigest() != record.get("sha256")
                or len(data) != record.get("bytes") or width != manifest.get("tile_pixels")):
            raise PackError("地图内容校验失败：" + str(path))
    if total != manifest.get("tile_bytes"):
        raise PackError("地图总大小校验失败。")
    return len(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Breadcrumb 本地地图准备工具（尚不能安装到手表）")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="根据 GPX 计算完整的赛道周边地图清单")
    plan.add_argument("gpx", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    plan.add_argument("--zoom", type=int, action="append", default=None)
    plan.add_argument("--buffer-m", type=float, default=300)
    plan.add_argument("--max-tiles", type=int, default=256, help="开发预算，不代表 255 实测容量")
    pack = commands.add_parser("pack", help="从本地 XYZ PNG 地图生成经过检查的离线素材包")
    pack.add_argument("plan", type=Path)
    pack.add_argument("--tiles-dir", type=Path, required=True)
    pack.add_argument("--output", type=Path, required=True)
    pack.add_argument("--attribution", required=True)
    pack.add_argument("--max-mib", type=float, default=4, help="开发预算，不代表 255 实测容量")
    verify = commands.add_parser("verify", help="检查路线、地图清单和文件完整性")
    verify.add_argument("package", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            result = make_plan(args.gpx, args.zoom or [14], args.buffer_m, args.max_tiles)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation protects earlier plans and linked files.
            with args.output.open("x", encoding="utf-8") as file:
                json.dump(result, file, ensure_ascii=False, indent=2)
                file.write("\n")
            print("计划已生成：需要 %d 张地图。尚未下载、编译或安装。" % len(result["tiles"]))
        elif args.command == "pack":
            size = build_package(load_plan(args.plan), args.tiles_dir, args.output,
                                 args.attribution, args.max_mib * 1024 * 1024)
            print("素材包已生成：%.2f MiB。尚未编译成手表应用。" % (size / 1024 / 1024))
        else:
            count = verify_package(args.package)
            print("完整性检查通过：%d 张地图。此结果不代表已通过 255 实机验证。" % count)
        return 0
    except FileExistsError:
        print("无法完成：输出文件已存在，请使用新名称，以保留之前的地图计划。", file=sys.stderr)
        return 1
    except (PackError, OSError, ValueError, KeyError, TypeError) as exc:
        print("无法完成：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
