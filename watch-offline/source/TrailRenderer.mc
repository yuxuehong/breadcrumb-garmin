import Toybox.WatchUi;
import Toybox.Graphics;
import Toybox.Math;

class TrailRenderer {
    var map;
    var level = null;
    var tiles = {};
    var cachedId = null;
    var cachedBitmap = null;
    function initialize(state) { map = state; }
    function release() { cachedBitmap = null; cachedId = null; }
    function draw(dc, control) {
        var w = dc.getWidth(), h = dc.getHeight();
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_LT_GRAY); dc.clear();
        if (w < 200 || h < 200) {
            dc.drawText(w/2,h/2-12,Graphics.FONT_XTINY,trailText(Rez.Strings.OneField),Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }
        map.prepare(w,h);
        if (level != map.zoom) {
            release(); tiles = {}; level = map.zoom;
            tiles = level == OfflineMap.ZOOM ? map.baseTiles : OfflineMap.index(level);
        }
        var divisor = Math.pow(2,OfflineMap.ZOOM-level).toDouble();
        var ox = Math.floor(OfflineMap.ORIGIN_X/divisor);
        var oy = Math.floor(OfflineMap.ORIGIN_Y/divisor);
        var cx = (map.x+OfflineMap.ORIGIN_X*256.0d)/divisor-ox*256;
        var cy = (map.y+OfflineMap.ORIGIN_Y*256.0d)/divisor-oy*256;
        var screenY = h/2;
        var left = cx-w/2, top = cy-screenY;
        var tx0 = Math.floor(left/256).toNumber(), ty0 = Math.floor(top/256).toNumber();
        var tx1 = Math.floor((left+w-1)/256).toNumber(), ty1 = Math.floor((top+h-1)/256).toNumber();
        for (var x = tx0; x <= tx1; x++) {
            for (var y = ty0; y <= ty1; y++) {
                var id = tiles.get(x*65536+y);
                if (id != null) {
                    // Retain just one bitmap, even when the screen spans corners.
                    if (id != cachedId) { release(); cachedBitmap = WatchUi.loadResource(id); cachedId = id; }
                    dc.drawBitmap((x*256-left).toNumber(),(y*256-top).toNumber(),cachedBitmap);
                }
            }
        }
        if (map.gpsX != null && (map.inside || map.isTracking())) {
            var px = w/2+(map.gpsX-map.x)/divisor;
            var py = screenY+(map.gpsY-map.y)/divisor;
            if (px >= 0 && px < w && py >= 0 && py < h) {
                dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT); dc.fillCircle(px,py,8);
                dc.setColor(Graphics.COLOR_RED,Graphics.COLOR_TRANSPARENT); dc.fillCircle(px,py,5);
                if (map.heading != null) {
                    dc.setPenWidth(3);
                    dc.drawLine(px,py,px+Math.sin(map.heading)*18,py-Math.cos(map.heading)*18);
                }
            }
        }
        // The only map overlay besides the true GPS marker is a warning
        // when a recording has left coverage. Do not disguise that state
        // by clamping the live position to a downloaded edge.
        if (map.isTracking() && !map.inside) { banner(dc,w,18,trailText(Rez.Strings.Outside)); }
    }
    function banner(dc,w,y,text) {
        var width = trailMin(w-28,dc.getTextWidthInPixels(text,Graphics.FONT_XTINY)+10);
        dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT); dc.fillRectangle((w-width)/2,y,width,22);
        dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT);
        dc.drawText(w/2,y,Graphics.FONT_XTINY,text,Graphics.TEXT_JUSTIFY_CENTER);
    }
}
