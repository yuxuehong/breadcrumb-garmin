# Mac 离线地图开发

本 Fork 基于 pauljohnston2025/breadcrumb-garmin，保留原项目署名和
CC BY-NC-SA 4.0 许可。新增内容目前是开发原型，不是已经能安装到 255 的成品。

## 已确认的产品方向

- 使用 OpenStreetMap 基础数据，加上高程/等高线数据，沿用成熟地形图样式。
- Mac 可以管理省、市、县的数据；手表优先安装赛道及周边区域。
- 日常流程：拖入 GPX → 预览并调整范围 → 自动准备并检查 → USB 安装。
- 界面显示覆盖范围、缺失路段、进度、替换的赛事和实际可用状态。
- 容量不足时说明原因并提供选择；不静默删除地图或降低精度。
- 保留原始 GPX。缺少海拔标为未知，轨迹段之间的缺口不连接成假路线。
- 图源、压缩、缓存等技术参数放到高级设置，不要求用户了解开发工具。
- 不把素材包、模拟器通过或复制文件成功误报为已通过手表实测。

## 第一阶段：可运行的本地准备工具

依赖 Python 3.9+ 标准库；不需要 Android、网络账号或第三方 Python 包。

```sh
python3 tools/offline_pack.py plan path/to/race.gpx \
  --buffer-m 300 --zoom 14 --output build/race-plan.json

python3 tools/offline_pack.py pack build/race-plan.json \
  --tiles-dir path/to/xyz-png \
  --attribution '请填入实际图源要求的署名与许可信息' \
  --output build/race-assets

python3 tools/offline_pack.py verify build/race-assets
python3 -m unittest discover -s tests -v
```

`xyz-png` 的布局是 `层级/x/y.png`。仅接收已经合法取得的本地切片，
不下载 OSM 官网的标准切片，不生成或伪造地图。使用带等高线的图像时，
等高线会随图像保留；工具本身不会从 OSM 原始数据计算等高线。

路线原始点、海拔和分段完整保存在计划文件中。计算覆盖范围时检查整个线段，
而不是仅取 GPX 点所在的图块。以 Web Mercator 方形缓冲近似走廊，保守多取少量
地图；不声称是精确的地表距离缓冲。首版不支持跨日期变更线的路线。

打包前检查缺图、PNG 结构/CRC、图块尺寸和素材大小；构建使用临时目录，
失败不发布半成品，不覆盖现有包。输出清单记录图源署名和 SHA-256，
校验时重新计算赛道所需切片，检查缺图、文件变化和计划变化。
PNG 结构验证不等于完整像素解码或手表渲染验证。校验值用于意外损坏检测，
不是数字签名，也不认证第三方提供的地图来源。

默认 256 张、4 MiB **只是开发预算，绝不是 FR255 的已测容量**。
素材包不是 `.PRG`，不能复制进手表直接导航。目录中的个人路线和下载地图
不应提交到 GitHub，请放到被忽略的 `build/` 中。

## 后续验证顺序

1. 配置官方 Garmin Connect IQ SDK、Java 和 FR255 设备定义，验证原版构建。
2. 用少量真实地形切片做应用资源实验，记录 PRG 大小、加载和图形内存。
3. 接入 Breadcrumb 的路线和地图读取，生成独立应用标识，避免覆盖商店版。
4. 在 255 上验证：USB 安装、拔线、无手机、重启后仍显示 GPX 和等高线。
5. 测量实际容量和耗电，再定默认范围、细节与地图数量。
6. 增加 Mac 界面、受支持的地图获取方式、下载恢复、明确的覆盖预览和赛事包管理。

省级数据裁剪、地图渲染/下载、Garmin 资源生成、编译、USB/MTP 安装和图形界面
都还未实现。本阶段先把可重复测试的覆盖与完整性检查做稳。

## 参考

- 上游：https://github.com/pauljohnston2025/breadcrumb-garmin
- Garmin 资源：https://developer.garmin.com/connect-iq/articles/core-topics/Resources.html
- OSM 数据：https://download.geofabrik.de/asia/china/zhejiang.html
- OSM 标准切片使用政策：https://operations.osmfoundation.org/policies/tiles/
