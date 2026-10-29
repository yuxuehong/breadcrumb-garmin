> **Breadcrumb Trail / Mac 离线赛道地图** — [中文使用说明](docs/mac-offline.md)
>
> This fork adds a Mac desktop workflow: import GPX, download OpenTopoMap coverage
> around the race at every supported zoom level, preview contours and the route, compile an offline FR255 data field
> plus a standalone Race Preview app, and install both over USB with read-back verification.
> Open Race Preview from the watch activity/app list to zoom and pan before a race,
> independently of GPS. Hiking and trail-running data pages retain UP/DOWN navigation.
> The offline variant is in `watch-offline/`; the desktop app is in `mac/`.
> Original upstream code and attribution are preserved below.

## Mac 离线版：赛前浏览与按键

在 Mac 导入 GPX → 下载并预览地图 → 生成手表地图 → USB 安装。
地图使用 OpenStreetMap 路网、SRTM 高程和 OpenTopoMap 样式；每个缩放级别都提前下载真实底图。
“路线周边”是额外保留的范围，程序还会自动补足至少半个屏幕的余量，准备包含整条 GPX 的完整矩形区域。
手表离线读取已安装地图，不会临时联网补图；此功能面向一场比赛附近，不提供整省下载。

拔掉 USB，返回表盘，按右上 **START → 赛道预览**（英文 **Race Preview**）。
首次可在运动／应用列表底部的“添加”中查找，无需 GPS 或开始运动。
启动时显示地图署名 5 秒，按 START 可提前进入地图；之后画面只保留地图和路线，按键说明放在这里。

| 地图页按键 | 作用 |
| --- | --- |
| UP / DOWN | 默认放大 / 缩小；在移动模式中向北 / 南或西 / 东移动 |
| START | 依次切换“缩放 → 南北移动 → 东西移动” |
| 长按 UP | 直接选择“缩放地图”“南北移动”“东西移动”，或“查看全程”“回到起点”“地图来源” |
| BACK | 返回上一级；在地图页退出应用 |

“查看全程”会将路线放进圆形屏幕内并留出边距。移动到已下载范围的边缘会停止，保持整个视口有底图。
“标准”为 14 级，“详细”为 15 级：若此前安装的是标准地图，选详细重新生成即可多放大一档。
最大倍率取决于已下载地图的细节级别，不继续进行数字放大。
所有缩放级别合计最多 256 张切片；范围较大时可选标准、减少周边范围，或将长赛道分段准备。

运动中使用 **Breadcrumb Trail** 单字段全屏地图页；UP / DOWN 仍可切换到心率、距离、时间等数据页。
正式记录中若 GPS 走出地图范围，会保留真实位置并显示越界提示。
旧比赛需在 Mac 重新生成并安装：已有缓存会复用，缺少的范围和缩放级别会补充下载。

目前用户已在真实 255 上确认 v5 缩到最小、移动到边缘均铺满屏幕，且没有常驻提示文字；室外跟随、长距离运行和续航仍待验证。
完整安装步骤、地图来源与开发说明见[中文使用说明](docs/mac-offline.md)。

---

A garmin watch datafield that shows a breadcrumb trail. For watches that do not support breadcrumb navigation out of the box.

Donations are always welcome, but not required: https://www.paypal.com/paypalme/pauljohnston2025

Information on all the settings can be found in [Settings](settings.md)  
note: Map support is disabled by default, but can be turned on in app settings, this is because map tile loading is memory intensive and may cause crashes on some devices. You must set `Tile Cache Size` if using maps to avoid crashes.    
Companion app can be found at [Companion App](https://github.com/pauljohnston2025/breadcrumb-mobile.git)  
[Companion App Releases](https://github.com/pauljohnston2025/breadcrumb-mobile/releases/latest)

---

There are several different apps/datafields on the connect-iq store all with similar breadcrumb functionality 

Each one has its own repository mirror (git push --mirror https://github.com/pauljohnston2025/XXX.git).  
I could use monkey barrels to share common code, but barrels have a memory overhead, and I only expect 1 of these apps/datafields to be installed at a time.  
I also expect the merge conflicts will be easier to deal with rather than a whole heap of (:excludeAnnotations)  
Doing it this way also means each repo has 0 dependents and is fully stand-alone.   
There are multiple Datafield app types, so that users can install 2 alongside eachother if they want. eg. have both the BreadcrumbDataField and LWBreadcrumbDataField enabled at the same time so that if BreadcrumbDataField crashes from OOM or some high usage map task we can still navigate the planned route using LWBreadcrumbDataField.  If a user has 2 installed it would be good practice to disable alerts on one of the datafields, or you will get 2 independent alert for each 'off track' etc.  

The original project is https://github.com/pauljohnston2025/breadcrumb-garmin it contains the main datafield with all features on supported watches.

note: Some older devices will not support all of the features (eg. routes/ device settings) This is a garmin limitation as those devices (<3.2.0 api) do not support phone app messages for datafields.

The current mirrors are: 

* [BreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin) 
  * [Garmin Store](https://apps.garmin.com/apps/99d7ebcc-3586-4dbd-8684-08b5ce4ddd80)
  * Type - DataField
  * Full breadcrumb trail with map tile support
* [BreadcrumbApp](https://github.com/pauljohnston2025/breadcrumb-garmin-app)
  * [Upstream mirror - BreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin)
  * [Garmin Store](https://apps.garmin.com/apps/40e128d2-db98-41d1-b5a9-624a725e6e68)
  * Type - App
  * An app instead of a datafield
  * Full breadcrumb trail with map tile support
  * Adds more control
    * Support for non-touch screen devices, as it can handle button press events
    * Touch screens can drag the map around to pan
  * Supports more features on more devices (the app has larger memory limits than a datafield)
* [BreadcrumbAppGlance](https://github.com/pauljohnston2025/breadcrumb-garmin-app-glance)
  * [Upstream mirror - BreadcrumbApp](https://github.com/pauljohnston2025/breadcrumb-garmin-app)
  * [Garmin Store](https://apps.garmin.com/apps/be3ca3aa-edef-4408-81d8-fe56011fafa6)
  * Type - App
  * Same as BreadcrumbApp but removed activity recording (this is so it can be opened from a glance view whilst recording using a garmin native activity)
* [LWBreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin-light-weight)
  * [Upstream mirror - BreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin)
  * [Garmin Store](https://apps.garmin.com/apps/3506adc5-7098-42ff-8c3f-601de1184aa5)
  * Type - DataField
  * Full breadcrumb trail (no map tile support)
* [ULBreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin-ultra-light)
  * [Upstream mirror - LWBreadcrumbDataField](https://github.com/pauljohnston2025/breadcrumb-garmin-light-weight)
  * [Garmin Store](https://apps.garmin.com/apps/fc5a0149-347a-4c39-aaf2-735eddd7b7bd)
  * Type - DataField
  * Limited breadcrumb trail (no map support, no alerts)
  * This is the lightest weight datafield and is supported on more devices, it is restricted to 1 route and 1 track and alot of customisation is missing

The companion app supports all of the watch apps, but the watch app must be selected in the companion app settings.


---

# Bug Reports

To aid in the fastest resolution, please include.

- Some screenshots of the issue, and possibly a recording
- A reproduction case of exactly how to reproduce the issue
- What you expected to happen
- The settings that you had enabled/disabled (a full screenshot of all the settings is best)

Please ensure any images/recordings do not contain any identifying information, such as your current location.

If the watch app encounters a crash (connect iq symbol displayed), you should also include the crash report. This can be obtained by:

* Connect the watch to a computer
* Open the contents of the watch and navigate to  `<watch>\Internal Storage\GARMIN\APPS\LOGS`
* Copy any log files, usually it is called CIQ_LOG.LOG, but may be called CIQ_LOG.BAK

You can also manually add a text file `BreadcrumbDataField.TXT` to the log directory (before the crash), and any app logs will be printed there. Please also include this log file.

---

# Development

Must port forward both adb and the tile server for the simulator to be able to fetch tiles from the companion app

* adb forward tcp:8080 tcp:8080
* adb forward tcp:7381 tcp:7381

To merge in the upstream do

```
cd path/to/mirrored/repo  eg. breadcrumb-garmin-light-weight
git remote add old-repo https://github.com/pauljohnston2025/breadcrumb-garmin.git
git fetch old-repo --no-tags
git merge old-repo/master
```

---

# Garmin Iq Store Content

Can be used without the companion app, but will only show current track.
Use the companion app to add a route that you can follow.

Intended for use with round watches, but will work on others (might not look the best though).  
Some watches/devices with touch support will be able switch between elevation and track view during activity.

Target User: Hikers, backpackers, cyclists, trail runners, and outdoor enthusiasts seeking a flexible navigation tool for their Garmin watches. Especially valuable for users with Garmin devices that do not have built-in map support. Suitable for both on- and off-grid exploration, with customizable maps and route following capabilities.

Key Features:

Breadcrumb Trail Navigation: Displays a route as a breadcrumb trail overlaid on a map, allowing users to easily follow the intended path. Brings map-based navigation to Garmin devices that do not have native map support.  
Map Tile Loading (Online): Supports any tile server that uses EPSG:3857 image tiles.  
Map Tile Loading (Offline): Loads pre-cached tile data from the companion app.  
Off-Track Alerts (needs to be enbaled in garmins setting menu, see below): Notifies the user when they deviate from the planned route.  
Elevation Overview: Shows an elevation profile of the route, allowing users to anticipate upcoming climbs and descents.  
Routing (companion app required): Users can import routes from Google Maps or GPX files using the companion app.  
Customizable Settings: Fully customizable via the watch or Connect IQ settings. No companion app required for basic functionality.  
Breadcrumb-Only Mode: (Optional) A simplified display mode showing only the breadcrumb trail, without the underlying map tiles, for increased battery life on devices with limited screen resolution or memory.

Companion app:
The companion app is available on my github: https://github.com/pauljohnston2025/breadcrumb-mobile.git  
While all settings can be configured directly on the watch or through Connect IQ settings, the companion app unlocks powerful features such as offline map support via Bluetooth transfer and route loading. Currently, the companion app is only available on Android, but contributions from iOS developers are highly welcomed to expand platform support and bring these functionalities to a wider audience.

This is a datafield, not a full fledged app, it runs in the context of native activity.  
The datafield is expected to be used to cover the full available area of a round watchface.    
It will still work with non-round devices or partial layouts, but the full feature set of the ui will not be possible due to the limited space.  

To add datafield to a native app:

- Open the app (eg. running), you do not have to start the activity, just open it.
- Long press to open settings (or use the touchscreen to press settings)
- Navigate to Data Screens
- Select screen
- Choose layout - recommended full screen layout
- Edit data fields - choose the 'BreadCrumbDataField' from the 'ConnectIQ Fields' menu

For the venu range: https://support.garmin.com/en-AU/?faq=gyywAozBuAAGlvfzvR9VZ8&identifier=707572&searchQuery=data%20field&tab=topics  
A more thorough explanation for a different app can be found at: https://support.garmin.com/en-AU/?faq=3HkHX1wT6U7TeNB7YHfiT7&identifier=707572&searchQuery=data%20field&tab=topics

---

# Known Issues

Some screens appear to have an offset applied to the [dc](https://developer.garmin.com/connect-iq/api-docs/Toybox/Graphics/Dc.html), that I cannot find a way to correct. Sometimes this offset occurs on my own physical device, and other times there is no offset. If anyone knows how to solve this, please let me know. I believe this is an issue with garmins datafield obscurity, since you can have the datafield take up only part of the screen. The problem still occurs when the datafield takes up the whole screen though (I think garmin reserves certain areas of the screen for their own ui components). Note this clipping may also be noticed on different parts of the screen if buffered map rotations are enabled, since everything drawn to the dc is offset.

Simplest reproduction example, it appears the device context for drawing is already offset (but only sometimes).

```
function onUpdate(dc as Dc) as Void {
    dc.setColor(Graphics.COLOR_RED, Graphics.COLOR_RED);
    dc.clear();
}
```

The white areas in the bellow images show the issue.

![](images/screenoffsetvenu2.png)
![](images/screenoffsetvenu3.png)
![](images/screenoffsetvenu3s.png)

---

# Map Tiles

Powered by Esri: https://www.esri.com  
OpenStreetMap: https://openstreetmap.org/copyright  
OpenTopoMap: https://opentopomap.org/about  
Google: https://cloud.google.com/maps-platform/terms https://policies.google.com/privacy  
Carto: https://carto.com/attribution  
Stadia: &copy; <a href="https://stadiamaps.com/" target="_blank">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/" target="_blank">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>  
Mapy: https://mapy.com/ https://api.mapy.com/copyright

---

# Licencing

Attribution-NonCommercial-ShareAlike 4.0 International: https://creativecommons.org/licenses/by-nc-sa/4.0/  

---
