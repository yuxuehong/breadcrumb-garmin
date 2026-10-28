import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Activity;
import Toybox.Graphics;
import Toybox.Math;
import Toybox.Lang;

// Offline variant of Breadcrumb: the Mac bakes the complete GPX into native
// bitmap resources. Only visible bitmaps are loaded. No phone or web requests.
class TrailApp extends Application.AppBase {
    function initialize() { AppBase.initialize(); }
    function getInitialView() { return [new TrailView()]; }
}

class TrailView extends WatchUi.DataField {
    var tiles;
    var centerX;
    var centerY;
    var hasFix = false;
    var heading = null;
    var latitude = 0.0d;
    var cache = {};

    function initialize() {
        DataField.initialize();
        tiles = OfflineMap.index();
        centerX = OfflineMap.START_X;
        centerY = OfflineMap.START_Y;
    }

    function compute(info) {
        hasFix = info.currentLocation != null;
        heading = info.currentHeading;
        if (hasFix) {
            var location = info.currentLocation.toDegrees();
            latitude = location[0].toDouble();
            var radians = latitude * Math.PI / 180.0d;
            var scale = Math.pow(2, OfflineMap.ZOOM).toDouble();
            centerX = ((location[1].toDouble()+180.0d)/360.0d*scale-OfflineMap.ORIGIN_X)*256.0d;
            centerY = ((1.0d-Math.ln(Math.tan(radians)+1.0d/Math.cos(radians))/Math.PI)/2.0d*scale-OfflineMap.ORIGIN_Y)*256.0d;
        }
    }

    function onUpdate(dc) {
        var w = dc.getWidth();
        var h = dc.getHeight();
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_LT_GRAY);
        dc.clear();
        if (w < 200 || h < 200) {
            dc.drawText(w/2, h/2-15, Graphics.FONT_XTINY, "Use 1-field page", Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        var left = centerX-w/2;
        var top = centerY-h/2;
        var tx0 = Math.floor(left/256).toNumber();
        var ty0 = Math.floor(top/256).toNumber();
        var tx1 = Math.floor((left+w)/256).toNumber();
        var ty1 = Math.floor((top+h)/256).toNumber();
        // Release old tile references before loading the next screen.
        var next = {};
        for (var x = tx0; x <= tx1; x++) {
            for (var y = ty0; y <= ty1; y++) {
                var key = x*65536+y;
                if (tiles.hasKey(key)) {
                    var bitmap = cache.get(key);
                    if (bitmap != null) { next.put(key, bitmap); }
                }
            }
        }
        cache = next;
        for (var x = tx0; x <= tx1; x++) {
            for (var y = ty0; y <= ty1; y++) {
                var key = x*65536+y;
                var id = tiles.get(key);
                if (id != null) {
                    var bitmap = cache.get(key);
                    if (bitmap == null) {
                        bitmap = WatchUi.loadResource(id);
                        cache.put(key, bitmap);
                    }
                    dc.drawBitmap((x*256-left).toNumber(), (y*256-top).toNumber(), bitmap);
                }
            }
        }
        // Fixed north-up map and a distinct current-position marker.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.fillCircle(w/2,h/2,8);
        dc.setColor(hasFix ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.fillCircle(w/2,h/2,5);
        if (hasFix && heading != null) {
            dc.setPenWidth(3);
            dc.drawLine(w/2,h/2,(w/2+Math.sin(heading)*18).toNumber(),(h/2-Math.cos(heading)*18).toNumber());
        }
        dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT);
        dc.fillRectangle(w/2-75,18,150,24);
        dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT);
        var centerKey = Math.floor(centerX/256).toNumber()*65536+Math.floor(centerY/256).toNumber();
        var label = hasFix ? (tiles.hasKey(centerKey) ? "N  |  OFFLINE" : "OUTSIDE MAP") : "WAIT GPS";
        dc.drawText(w/2,19,Graphics.FONT_XTINY,label,Graphics.TEXT_JUSTIFY_CENTER);
        dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT);
        dc.fillRectangle(w/2-95,h-47,190,28);
        dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT);
        dc.drawText(w/2,h-45,Graphics.FONT_XTINY,"OSM / SRTM / OTM",Graphics.TEXT_JUSTIFY_CENTER);
    }
}
