# Breadcrumb Trail：Mac 离线赛道地图

基于 pauljohnston2025/breadcrumb-garmin 的非商业 Fork，保留上游署名和
CC BY-NC-SA 4.0 许可。Mac 端与 `watch-offline/` 是新增的 USB 离线版本；
上游原有的手机配套功能保留在 `source/`。

## 日常使用

1. 打开 `build/Breadcrumb Trail.app`。
2. 拖入比赛 GPX，选择路线两侧的范围（默认 300 米）与细节。
3. 点击 **下载并预览地图**。地图可拖动、缩放，蓝线为完整 GPX，绿色为起点。
4. 确认预览，点击 **生成手表地图**。连接 255，检测型号后点击 **安装到 255**。
5. 等待“文件已写入并校验”，再拔线。打开跑步或越野跑活动，长按左侧中键 UP，
   进入活动设置 → 数据页面 → 新增或编辑一个页面。
6. 先将这个页面的 **布局设为 1 个字段（全屏）**，再将唯一的数据字段设为
   Connect IQ → **Breadcrumb Trail**。不要在三格页面里重复添加地图。

安装过程会重新核对型号、程序校验值和可用空间。地图保存在应用资源中，拔线后
无需手机或网络。地图朝北，红点为 GPS 当前位置，短线为行进方向。
未获得定位时显示起点预览和 `WAIT GPS`；当前位置超出下载范围时提示 `OUTSIDE MAP`。
请使用单字段全屏页面；小页面会提示 `Use 1-field page`。
这个提示代表程序已启动，但布局空间不足；请修改页面的“布局”，而非只更换某一格的内容。
如果全屏后显示 IQ 错误图标或白屏，才需要进一步读取程序错误日志。

每次安装替换本工具的当前赛事，Mac 保留之前保存的 GPX、地图缓存和安装备份。
上游商店版使用不同应用 ID，不会被这个离线版本替换。文件名为
`GARMIN/APPS/BCTRAIL.PRG`，本工具不写入活动记录、音乐或其他应用。

## 一次性准备

- macOS、Apple 命令行开发工具、Python 3.9+（推荐 Homebrew Python 3.13）。
- 从 [Garmin 官网](https://developer.garmin.com/connect-iq/sdk/)安装 SDK Manager，
  自行接受协议并登录；只需要 **一个当前 SDK** 和实际手表的设备文件。
  此版本在 SDK 9.3.0、`fr255` 上编译通过。
- SDK Manager → Preferences：SDK Updates / Device Updates 选 **Notify Me**，
  避免自动下载全部历史版本或其他机型。
- 在项目目录的终端运行 `zsh mac/Setup.command`：安装 Pillow、Java 和 libmtp，并构建本机应用。
  应用创建在 `build/` 中。首次需要联网；后续使用已有缓存可离线重新生成。
- 如果移动项目文件夹，重新运行初始化以更新应用的项目位置。

本机应用壳已签署本地临时签名；这不是已经公证、可独立分发的通用安装包。
官方 Garmin SDK 和本地签名密钥不随项目分发。

## 地图与容量

图源为 [OpenTopoMap](https://opentopomap.org/about)，使用 OpenStreetMap 和 SRTM，
包含山路、等高线和地形阴影。保留完整图源署名与 CC BY-SA 3.0 地图许可。
使用 WGS84 / Web Mercator，不混用国内互联网地图偏移坐标。

公共图源每次最多准备 256 张切片、单线程至多每秒一次请求，下载失败保留进度，
遇到限流停止；已下载内容直接复用。不使用 OSM 官网标准切片做离线预取。
此版本用于比赛路线走廊，**不提供整省、整市批量下载**。地图数据是否包含当地山路，
取决于 OSM 与图源覆盖情况，预览可以提前检查。

“详细”使用 15 级，“标准”使用 14 级。赛道直接绘入切片，不截断 GPX 点、不连接
原始轨迹段之间的缺口。手表按需加载屏幕附近的原生位图，避免把整条路线的点数组
和全部地图同时放进运行内存。此版本不提供自动转弯提示、偏航距离计算或腕上缩放。
原有 Garmin 原生 GPX 导航可以继续使用。

255 的数据字段运行内存是 256 KiB，不等于手表总存储。
256 张是产品下载上限，不是已经验证的最大可用地图容量。
大范围、长距离和高密度地图仍需实机确认；复制校验成功不代表运行与续航已验证。

## 验证和开发

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python mac/build_app.py
.venv/bin/python mac/server.py
```

本地服务仅监听 `127.0.0.1`，每次启动生成访问令牌，并校验请求来源。
GPX、缓存、密钥、日志和备份都在被 Git 忽略的 `build/` 下，不上传至云端。
地图请求会把所需片区的切片坐标发送给图源。

截至 2026-10-29 的开发验证：29 项测试通过；上游公开示例 GPX 的 5 张真实地形切片
已完成下载、预览、官方 SDK 编译和实际 Forerunner 255 的 MTP 写入、回读字节校验。
程序约 78 KB。用户已在实机单字段全屏页面确认看到等高线与蓝色路线，
照片显示 `WAIT GPS` 起点预览。该结果只代表这份测试地图；室外 GPS 跟随、
重启保持、长距离运行和续航尚未验证。

当前 USB 组件支持正常挂载的 Garmin 卷和 MTP。MTP 被占用时关闭 Garmin Express、
OpenMTP 等程序后重试。安装中保持连接；若发生中断，旧程序备份保留在 Mac 的
`build/desktop-data/backups/`。不要删除恢复所需的备份。

上游仓库：https://github.com/pauljohnston2025/breadcrumb-garmin
