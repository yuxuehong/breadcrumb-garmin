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
    var navigation = null;
    var speed = null;
    var accuracy = null;
    var running = false;
    var navigationReady = false;
    var pendingZoom = null;
    var pendingTicks = 0;
    var metersPerPixel = OfflineMap.METERS_PER_PIXEL;

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
    function maxZoom() { return OfflineMap.ZOOM+1; }
    function coveredAt(px, py, z) {
        var b=OfflineMap.extent(trailMin(z,OfflineMap.ZOOM));
        return px!=null && py!=null && px>=b[0] && py>=b[1] && px<b[2] && py<b[3];
    }
    // Ignore last-known fixes. An activity can continue recording while GPS
    // is unavailable, so keep the viewport but remove the live marker.
    function updateMotion(px, py, direction, velocity, quality) {
        accuracy=quality; speed=velocity;
        if (quality==null || quality==0 || quality==1) { px=null; py=null; }
        updateGps(px,py,direction);
        if (!follow || px==null || (!inside && !recording) || quality==2 || (recording && !running)) {
            pendingZoom=null; pendingTicks=0; return;
        }
        var target=navigationZoom();
        if (!navigationReady) {
            zoom=target; navigationReady=true; pendingZoom=null; pendingTicks=0;
        } else if (target==zoom) {
            pendingZoom=null; pendingTicks=0;
        } else {
            if (pendingZoom!=target) { pendingZoom=target; pendingTicks=1; }
            else { pendingTicks++; }
            // Widen sooner when leaving the route, and return to detail more
            // slowly. Repeated one-second fixes avoid flickering zoom levels.
            if (pendingTicks >= (target<zoom ? 8 : 12)) {
                zoom=target; pendingZoom=null; pendingTicks=0;
            }
        }
    }
    function nearestRouteDistance(px,py) {
        if (navigation==null) { navigation=OfflineMap.navigationPoints(); }
        var best=null;
        var ax=null, ay=null;
        for (var i=0;i+1<navigation.size();i+=2) {
            var bx=navigation[i], by=navigation[i+1];
            if (bx==null || by==null) { ax=null; ay=null; continue; }
            var nx=bx, ny=by;
            if (ax!=null) {
                var dx=bx-ax,dy=by-ay;
                var length=dx*dx+dy*dy;
                var t=length>0 ? trailMax(0,trailMin(1,((px-ax)*dx+(py-ay)*dy)/length)) : 0;
                nx=ax+t*dx; ny=ay+t*dy;
            }
            var ex=px-nx,ey=py-ny;
            var distance=ex*ex+ey*ey;
            if (best==null || distance<best) { best=distance; }
            ax=bx; ay=by;
        }
        return best==null ? 0 : Math.sqrt(best);
    }
    function navigationZoom() {
        var target=maxZoom();
        if (speed!=null && speed>3.5) { target--; }
        if (speed!=null && speed>6.0) { target--; }
        var distance=nearestRouteDistance(gpsX,gpsY);
        var radius=trailMin(viewportWidth,viewportHeight)*0.30;
        while (target>OfflineMap.MIN_ZOOM && distance*Math.pow(2,target-OfflineMap.ZOOM)>radius) { target--; }
        return trailMax(OfflineMap.MIN_ZOOM,target);
    }
    function updateGps(px, py, direction) {
        gpsX = px; gpsY = py; heading = direction;
        inside = contains(px, py);
        if (follow && inside) {
            x = px; y = py;
            overviewNeeded = true;
        } else if (follow && recording && px != null) {
            // Keep the real position and warning during a race, even outside
            // coverage; never imply that the runner jumped back to the start.
            x = px; y = py;
        }
    }
    function isTracking() { return follow && gpsX != null && (inside || recording); }
    function prepare(w, h) {
        viewportWidth = w; viewportHeight = h;
        if (follow && !isTracking() && !recording && overviewNeeded) {
            fit(w,h); overviewNeeded=false;
            navigationReady=false; pendingZoom=null; pendingTicks=0;
        }
        // Never shift a GPS-following camera away from the true position.
        if (!isTracking() && !recording) { clamp(); }
    }
    function beginPreview() { follow = false; clamp(); }
    function followLocation() {
        if (!follow) { navigationReady=false; pendingZoom=null; pendingTicks=0; }
        follow = true; overviewNeeded = true;
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
        beginPreview(); x = OfflineMap.START_X; y = OfflineMap.START_Y; zoom = maxZoom();
        clamp();
    }
    function changeZoom(delta) { beginPreview(); zoom += delta; clamp(); }
    function pan(dx, dy) {
        beginPreview();
        var step = 50.0d/Math.pow(2, zoom-OfflineMap.ZOOM);
        x += dx*step; y += dy*step; clamp();
    }
    function clamp() {
        zoom = trailMax(OfflineMap.MIN_ZOOM, trailMin(maxZoom(), zoom));
        var bounds = OfflineMap.extent(trailMin(zoom,OfflineMap.ZOOM));
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
