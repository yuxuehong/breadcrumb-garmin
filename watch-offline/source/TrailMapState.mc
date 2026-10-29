import Toybox.Application;
import Toybox.Math;
import Toybox.Lang;

// Camera coordinates stay in base-level pixels. GPS never changes a locked
// preview. The route fingerprint prevents reusing another race's camera.
class TrailMapState {
    var x = OfflineMap.START_X;
    var y = OfflineMap.START_Y;
    var zoom = OfflineMap.ZOOM;
    var follow = true;
    var standalone = false;
    var recording = false;
    var gpsX = null;
    var gpsY = null;
    var heading = null;
    var inside = false;
    var overviewNeeded = true;
    var baseTiles;

    function initialize() {
        baseTiles = OfflineMap.index(OfflineMap.ZOOM);
        try {
            var saved = Application.Storage.getValue("camera") as Lang.Dictionary or Null;
            var route = saved == null ? null : saved.get("route") as Lang.String or Null;
            if (route != null && route.equals(OfflineMap.ROUTE_ID)) {
                follow = saved.get("follow") != false;
                if (!follow && saved.get("x") != null && saved.get("y") != null && saved.get("zoom") != null) {
                    x = saved.get("x"); y = saved.get("y"); zoom = saved.get("zoom");
                    clamp(); overviewNeeded = false;
                }
            }
        } catch (error) { follow = true; }
    }
    function save() {
        Application.Storage.setValue("camera", {"route"=>OfflineMap.ROUTE_ID, "follow"=>follow,
            "x"=>x, "y"=>y, "zoom"=>zoom});
    }
    function project(lat, lon) {
        var latitude = trailMax(-85.051128, trailMin(85.051128, lat.toDouble()));
        var radians = latitude*Math.PI/180.0d;
        var scale = Math.pow(2, OfflineMap.ZOOM).toDouble();
        return [((lon.toDouble()+180.0d)/360.0d*scale-OfflineMap.ORIGIN_X)*256.0d,
            ((1.0d-Math.ln(Math.tan(radians)+1.0d/Math.cos(radians))/Math.PI)/2.0d*scale-OfflineMap.ORIGIN_Y)*256.0d];
    }
    function contains(px, py) {
        if (px == null || py == null || px < 0 || py < 0 || px >= OfflineMap.MAP_WIDTH || py >= OfflineMap.MAP_HEIGHT) { return false; }
        return baseTiles.hasKey(Math.floor(px/256).toNumber()*65536+Math.floor(py/256).toNumber());
    }
    function updateGps(px, py, direction) {
        var wasInside = inside;
        gpsX = px; gpsY = py; heading = direction;
        inside = contains(px, py);
        if (follow && inside) {
            x = px; y = py;
            if (!wasInside) { zoom = OfflineMap.ZOOM; }
            overviewNeeded = true;
        } else if (follow && recording && px != null) {
            // Keep the real position and warning during a race, even outside
            // coverage; never imply that the runner jumped back to the start.
            x = px; y = py; zoom = OfflineMap.ZOOM;
        }
    }
    function isTracking() { return follow && gpsX != null && (inside || recording); }
    function prepare(w, h) {
        if (follow && !isTracking() && overviewNeeded) { fit(w, h); overviewNeeded = false; }
    }
    function beginPreview() { follow = false; clamp(); }
    function followLocation() {
        follow = true; overviewNeeded = true; zoom = OfflineMap.ZOOM;
        updateGps(gpsX, gpsY, heading);
    }
    function fit(w, h) {
        x = (OfflineMap.MIN_X+OfflineMap.MAX_X)/2;
        y = (OfflineMap.MIN_Y+OfflineMap.MAX_Y)/2;
        var spanX = trailMax(1, OfflineMap.MAX_X-OfflineMap.MIN_X);
        var spanY = trailMax(1, OfflineMap.MAX_Y-OfflineMap.MIN_Y);
        var ratio = trailMin((w-90)/spanX, (h-130)/spanY);
        zoom = OfflineMap.ZOOM+Math.floor(Math.ln(trailMax(0.00001, ratio))/Math.ln(2)).toNumber();
        clamp();
    }
    function startPoint() {
        beginPreview(); x = OfflineMap.START_X; y = OfflineMap.START_Y; zoom = OfflineMap.ZOOM;
    }
    function changeZoom(delta) { beginPreview(); zoom += delta; clamp(); }
    function pan(dx, dy) {
        beginPreview();
        var step = 50.0d/Math.pow(2, zoom-OfflineMap.ZOOM);
        x += dx*step; y += dy*step; clamp();
    }
    function clamp() {
        x = trailMax(0, trailMin(OfflineMap.MAP_WIDTH-1, x));
        y = trailMax(0, trailMin(OfflineMap.MAP_HEIGHT-1, y));
        zoom = trailMax(OfflineMap.MIN_ZOOM, trailMin(OfflineMap.ZOOM, zoom));
    }
}

function trailMin(a,b) { return a < b ? a : b; }
function trailMax(a,b) { return a > b ? a : b; }
