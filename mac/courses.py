"""Validate local course files and convert FIT course geometry to GPX.

This module does not contact the watch or a network service. Native Garmin
courses are FIT files; their Record messages, not sparse Course Point cues,
contain the route. The FIT decoder is the MIT-licensed fitdecode package.
"""
import io
import math
from pathlib import Path
import struct
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from offline_pack import MAX_LAT, PackError, read_gpx

MAX_COURSE_BYTES = 20 * 1024 * 1024
MAX_COURSE_POINTS = 100_000
MAX_FIT_FRAMES = 500_000
GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"


def _validate_gpx(data):
    # Reuse the map planner's XML, coordinate, elevation and segment checks.
    # The random local directory is removed on success and on all failures.
    with tempfile.TemporaryDirectory(prefix="breadcrumb-course-") as folder:
        path = Path(folder) / "course.gpx"
        path.write_bytes(data)
        route = read_gpx(path)
    if sum(len(segment) for segment in route["segments"]) > MAX_COURSE_POINTS:
        raise PackError("路线点过多，请按比赛分段导出。")


def _course_name(value, filename):
    if not isinstance(value, str) or not value.strip():
        value = Path(filename.replace("\\", "/")).stem
    # Strip control and invisible formatting characters before XML encoding.
    value = "".join(c for c in value if unicodedata.category(c) not in ("Cc", "Cf", "Cs")
                    and ord(c) not in (0xfffe, 0xffff))
    return " ".join(value.split())[:120] or "手表路线"


def _coordinate(raw, latitude):
    if type(raw) is not int:
        raise PackError("FIT 路线中存在无效坐标。")
    value = raw * (180.0 / (1 << 31))
    valid = -MAX_LAT < value < MAX_LAT if latitude else -180 <= value <= 180
    if not valid:
        raise PackError("路线包含超出地图范围的坐标。")
    return value


def _fit_to_gpx(data, filename):
    if len(data) < 14 or data[8:12] != b".FIT" or data[0] not in (12, 14):
        raise PackError("FIT 文件头不正确，请重新导出路线。")
    header_size = data[0]
    body_size = struct.unpack_from("<I", data, 4)[0]
    if header_size + body_size + 2 != len(data):
        raise PackError("FIT 文件不完整或包含多份数据，请重新导出单条路线。")
    if data[1] >> 4 not in (1, 2):
        raise PackError("暂不支持此 FIT 文件版本，请导出 GPX 路线。")
    try:
        import fitdecode
    except ImportError as exc:
        raise PackError("FIT 路线读取组件未安装，请重新运行 Mac 初始化程序。") from exc

    root = ET.Element("gpx", {"xmlns": GPX_NAMESPACE, "version": "1.1",
                              "creator": "Breadcrumb Trail"})
    track = ET.SubElement(root, "trk")
    name = ET.SubElement(track, "name")
    name.text = _course_name(None, filename)
    segment = None
    points = 0
    file_ids = 0
    courses = 0
    try:
        with fitdecode.FitReader(io.BytesIO(data), processor=None,
                                 check_crc=fitdecode.CrcCheck.RAISE,
                                 error_handling=fitdecode.ErrorHandling.RAISE) as reader:
            for frame_count, frame in enumerate(reader, 1):
                if frame_count > MAX_FIT_FRAMES:
                    raise PackError("FIT 内容过多，请按比赛分段导出。")
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "file_id":
                    file_ids += 1
                    if file_ids != 1 or frame.get_value("type", fallback=None) != "course":
                        raise PackError("请选择路线 FIT 文件，运动记录或训练文件不能作为路线导入。")
                elif file_ids != 1:
                    raise PackError("FIT 缺少路线文件标识，请重新导出路线。")
                elif frame.name == "course":
                    courses += 1
                    if courses > 1:
                        raise PackError("FIT 包含多条路线，请分别导出。")
                    name.text = _course_name(frame.get_value("name", fallback=None), filename)
                elif frame.name == "event":
                    if (frame.get_value("event", fallback=None) == "timer"
                            and frame.get_value("event_type", fallback=None) in
                            ("stop", "stop_all", "stop_disable", "stop_disable_all")):
                        segment = None
                elif frame.name == "record":
                    lat = frame.get_raw_value("position_lat", fallback=None)
                    lon = frame.get_raw_value("position_long", fallback=None)
                    # FIT invalid sentinels and absent coordinates both decode
                    # to None. A real zero coordinate must remain a route point.
                    if lat is None or lon is None:
                        segment = None
                        continue
                    lat, lon = _coordinate(lat, True), _coordinate(lon, False)
                    points += 1
                    if points > MAX_COURSE_POINTS:
                        raise PackError("路线点过多，请按比赛分段导出。")
                    if segment is None:
                        segment = ET.SubElement(track, "trkseg")
                    point = ET.SubElement(segment, "trkpt", {"lat": format(lat, ".8f"),
                                                            "lon": format(lon, ".8f")})
                    # The decoder may expand legacy altitude into a synthetic
                    # enhanced field before the actual high-resolution field.
                    elevation = next((field.value for field in frame.get_fields("enhanced_altitude")
                                      if not field.is_expanded and field.value is not None), None)
                    if elevation is None:
                        elevation = frame.get_value("altitude", fallback=None)
                    if elevation is not None:
                        if type(elevation) not in (int, float) or not math.isfinite(elevation):
                            raise PackError("FIT 路线中存在无效海拔。")
                        ET.SubElement(point, "ele").text = format(elevation, ".3f")
        if file_ids != 1 or courses != 1:
            raise PackError("FIT 缺少路线信息，请重新导出路线。")
    except (fitdecode.FitError, ValueError, TypeError, KeyError, OverflowError,
            struct.error, AssertionError) as exc:
        if isinstance(exc, PackError):
            raise
        raise PackError("FIT 校验失败或文件已损坏，请重新导出路线。") from exc
    if points < 2:
        raise PackError("路线至少需要两个有效坐标点；只有转向提示的 FIT 无法生成地图。")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_course(data: bytes, filename: str) -> bytes:
    """Return validated GPX bytes; valid GPX input is preserved byte for byte.

    Only one FIT course is accepted. CRCs, declared length and geographic
    coordinates are checked before the caller can request any map downloads.
    Missing-position records break segments instead of creating false bridges.
    """
    if not isinstance(data, bytes) or not data:
        raise PackError("路线文件为空或无法读取。")
    if len(data) > MAX_COURSE_BYTES:
        raise PackError("路线文件过大，请按比赛分段导出。")
    suffix = Path(filename).suffix.casefold()
    if suffix == ".fit":
        output = _fit_to_gpx(data, filename)
    elif suffix == ".gpx":
        output = data
    else:
        raise PackError("请选择 GPX 或路线 FIT 文件。")
    if len(output) > MAX_COURSE_BYTES:
        raise PackError("转换后的路线过大，请按比赛分段导出。")
    _validate_gpx(output)
    return output
