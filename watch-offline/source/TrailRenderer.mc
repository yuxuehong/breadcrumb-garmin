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
        var tileLevel=trailMin(map.zoom,OfflineMap.ZOOM);
        if (level != tileLevel) {
            release(); tiles = {}; level = tileLevel;
            tiles = level == OfflineMap.ZOOM ? map.baseTiles : OfflineMap.index(level);
        }
        // One extra 2x display level enlarges downloaded detail without
        // claiming extra geographic detail or loading a giant bitmap.
        var magnification=Math.pow(2,map.zoom-level);
        var transform=null;
        if (magnification!=1) {
            transform=new Graphics.AffineTransform();
            transform.setToScale(magnification,magnification);
        }
        var divisor = Math.pow(2,OfflineMap.ZOOM-level).toDouble();
        var ox = Math.floor(OfflineMap.ORIGIN_X/divisor);
        var oy = Math.floor(OfflineMap.ORIGIN_Y/divisor);
        var cx = (map.x+OfflineMap.ORIGIN_X*256.0d)/divisor-ox*256;
        var cy = (map.y+OfflineMap.ORIGIN_Y*256.0d)/divisor-oy*256;
        var screenY = h/2;
        var left = cx-w/2/magnification, top = cy-screenY/magnification;
        var tx0 = Math.floor(left/256).toNumber(), ty0 = Math.floor(top/256).toNumber();
        var tx1 = Math.ceil((left+w/magnification)/256).toNumber()-1;
        var ty1 = Math.ceil((top+h/magnification)/256).toNumber()-1;
        for (var x = tx0; x <= tx1; x++) {
            for (var y = ty0; y <= ty1; y++) {
                var id = tiles.get(x*65536+y);
                if (id != null) {
                    // Retain just one bitmap, even when the screen spans corners.
                    if (id != cachedId) { release(); cachedBitmap = WatchUi.loadResource(id); cachedId = id; }
                    var drawX=Math.floor((x*256-left)*magnification).toNumber();
                    var drawY=Math.floor((y*256-top)*magnification).toNumber();
                    if (transform==null) { dc.drawBitmap(drawX,drawY,cachedBitmap); }
                    else { dc.drawBitmap2(drawX,drawY,cachedBitmap,{:transform=>transform}); }
                }
            }
        }
        if (map.gpsX != null && (map.inside || map.isTracking())) {
            var px = w/2+(map.gpsX-map.x)/divisor*magnification;
            var py = screenY+(map.gpsY-map.y)/divisor*magnification;
            if (px >= 0 && px < w && py >= 0 && py < h) {
                drawPosition(dc,px,py);
            }
        }
        // The only map overlay besides the true GPS marker is a warning
        // when a recording has left coverage. Do not disguise that state
        // by clamping the live position to a downloaded edge.
        if (map.isTracking()) { drawScale(dc,w,h,divisor/magnification); }
        if (map.isTracking() && !map.coveredAt(map.gpsX,map.gpsY,map.zoom)) {
            banner(dc,w,18,trailText(Rez.Strings.Outside));
        } else if (map.recording && map.gpsX==null) {
            banner(dc,w,18,trailText(Rez.Strings.WaitGps));
        } else if (map.isTracking() && map.accuracy==2) {
            banner(dc,w,18,trailText(Rez.Strings.WeakGps));
        }
    }
    function drawPosition(dc,px,py) {
        var color=map.accuracy==2 ? Graphics.COLOR_DK_GRAY : Graphics.COLOR_RED;
        if (map.heading==null) {
            dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT); dc.fillCircle(px,py,11);
            dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT); dc.fillCircle(px,py,10);
            dc.setColor(color,Graphics.COLOR_TRANSPARENT); dc.fillCircle(px,py,7);
            return;
        }
        var shape=[[0,-19],[13,14],[0,7],[-13,14]];
        var cosine=Math.cos(map.heading),sine=Math.sin(map.heading);
        var colors=[Graphics.COLOR_BLACK,Graphics.COLOR_WHITE,color];
        var scales=[1.0,0.92,0.72];
        for(var layer=0;layer<3;layer++) {
            var points=[];
            for(var i=0;i<shape.size();i++) {
                var x=shape[i][0]*scales[layer],y=shape[i][1]*scales[layer];
                points.add([(px+x*cosine-y*sine).toNumber(),(py+x*sine+y*cosine).toNumber()]);
            }
            dc.setColor(colors[layer],Graphics.COLOR_TRANSPARENT); dc.fillPolygon(points);
        }
    }
    function drawScale(dc,w,h,basePixelsPerScreenPixel) {
        var mpp=map.metersPerPixel*basePixelsPerScreenPixel;
        var nominal=mpp*54;
        var unit=Math.pow(10,Math.floor(Math.ln(trailMax(1,nominal))/Math.ln(10)));
        var value=unit;
        if(nominal>=unit*5) { value=unit*5; }
        else if(nominal>=unit*2) { value=unit*2; }
        var length=(value/mpp).toNumber();
        var text=value>=1000 ? (value/1000.0).format("%.1f")+" km" : value.toNumber().toString()+" m";
        var y=h-28, left=(w-length)/2;
        var textWidth=dc.getTextWidthInPixels(text,Graphics.FONT_XTINY);
        var box=trailMax(length+8,textWidth+8);
        dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT); dc.fillRectangle((w-box)/2,y-17,box,27);
        dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT); dc.setPenWidth(2);
        dc.drawText(w/2,y-18,Graphics.FONT_XTINY,text,Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawLine(left,y+5,left+length,y+5);
        dc.drawLine(left,y+1,left,y+8); dc.drawLine(left+length,y+1,left+length,y+8);
        dc.setPenWidth(1);
    }
    function banner(dc,w,y,text) {
        var width = trailMin(w-28,dc.getTextWidthInPixels(text,Graphics.FONT_XTINY)+10);
        dc.setColor(Graphics.COLOR_WHITE,Graphics.COLOR_TRANSPARENT); dc.fillRectangle((w-width)/2,y,width,22);
        dc.setColor(Graphics.COLOR_BLACK,Graphics.COLOR_TRANSPARENT);
        dc.drawText(w/2,y,Graphics.FONT_XTINY,text,Graphics.TEXT_JUSTIFY_CENTER);
    }
}
