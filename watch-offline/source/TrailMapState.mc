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
    var viewportWidth = 260;
    var viewportHeight = 260;
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
                    if (saved.get("width")!=null && saved.get("height")!=null) {
                        viewportWidth=trailMax(200,trailMin(260,saved.get("width")));
                        viewportHeight=trailMax(200,trailMin(260,saved.get("height")));
                    }
                    clamp(); overviewNeeded = false;
                }
            }
        } catch (error) { follow = true; }
    }
    function save() {
        Application.Storage.setValue("camera", {"route"=>OfflineMap.ROUTE_ID, "follow"=>follow,
            "x"=>x, "y"=>y, "zoom"=>zoom,"width"=>viewportWidth,"height"=>viewportHeight});
    }
    function project(lat, lon) {
        var latitude = trailMax(-85.051128, trailMin(85.051128, lat.toDouble()));
        var radians = latitude*Math.PI/180.0d;
        var scale = Math.pow(2, OfflineMap.ZOOM).toDouble();
        return [((lon.toDouble()+180.0d)/360.0d*scale-OfflineMap.ORIGIN_X)*256.0d,
            ((1.0d-Math.ln(Math.tan(radians)+1.0d/Math.cos(radians))/Math.PI)/2.0d*scale-OfflineMap.ORIGIN_Y)*256.0d];
    }
    function contains(px, py) {
        var bounds = OfflineMap.extent(OfflineMap.ZOOM);
        if (px == null || py == null || px < bounds[0] || py < bounds[1] || px >= bounds[2] || py >= bounds[3]) { return false; }
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
        viewportWidth = w; viewportHeight = h;
        if (follow && !isTracking() && overviewNeeded) { fit(w, h); overviewNeeded = false; }
        // Never shift a GPS-following camera away from the true position.
        if (!isTracking()) { clamp(); }
    }
    function beginPreview() { follow = false; clamp(); }
    function followLocation() {
        follow = true; overviewNeeded = true; zoom = OfflineMap.ZOOM;
        updateGps(gpsX, gpsY, heading);
    }
    function fit(w, h) {
        viewportWidth = w; viewportHeight = h;
        x = (OfflineMap.MIN_X+OfflineMap.MAX_X)/2;
        y = (OfflineMap.MIN_Y+OfflineMap.MAX_Y)/2;
        var spanX = trailMax(1, OfflineMap.MAX_X-OfflineMap.MIN_X);
        var spanY = trailMax(1, OfflineMap.MAX_Y-OfflineMap.MIN_Y);
        // Fit the bounding-box diagonal inside the round screen, including
        // its corners, with twelve pixels of clearance around the course.
        var diagonal = Math.sqrt(spanX*spanX+spanY*spanY);
        var ratio = trailMax(1,trailMin(w,h)-24)/diagonal;
        zoom = OfflineMap.ZOOM+Math.floor(Math.ln(trailMax(0.00001, ratio))/Math.ln(2)).toNumber();
        clamp();
    }
    function startPoint() {
        beginPreview(); x = OfflineMap.START_X; y = OfflineMap.START_Y; zoom = OfflineMap.ZOOM;
        clamp();
    }
    function changeZoom(delta) { beginPreview(); zoom += delta; clamp(); }
    function pan(dx, dy) {
        beginPreview();
        var step = 50.0d/Math.pow(2, zoom-OfflineMap.ZOOM);
        x += dx*step; y += dy*step; clamp();
    }
    function clamp() {
        zoom = trailMax(OfflineMap.MIN_ZOOM, trailMin(OfflineMap.ZOOM, zoom));
        var bounds = OfflineMap.extent(zoom);
        var scale = Math.pow(2,OfflineMap.ZOOM-zoom).toDouble();
        var insetX = viewportWidth*scale/2;
        var insetY = viewportHeight*scale/2;
        // Keep the whole viewport inside the downloaded rectangle. The
        // package generator supplies a full-screen margin at every level.
        var minX = bounds[0]+insetX, maxX = bounds[2]-insetX;
        var minY = bounds[1]+insetY, maxY = bounds[3]-insetY;
        x = minX > maxX ? (bounds[0]+bounds[2])/2 : trailMax(minX,trailMin(maxX,x));
        y = minY > maxY ? (bounds[1]+bounds[3])/2 : trailMax(minY,trailMin(maxY,y));
    }
}

function trailMin(a,b) { return a < b ? a : b; }
function trailMax(a,b) { return a > b ? a : b; }
