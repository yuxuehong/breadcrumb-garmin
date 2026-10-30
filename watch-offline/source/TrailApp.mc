import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Activity;
import Toybox.Lang;
import Toybox.Math;

class TrailApp extends Application.AppBase {
    var map;
    var renderer;
    function initialize() {
        AppBase.initialize();
        map = new TrailMapState();
        renderer = new TrailRenderer(map);
    }
    function getInitialView() { return [new TrailField(map, renderer)]; }
    function getSettingsView() {
        renderer.release();
        var view = new TrailPreview(map, renderer);
        return [view, new TrailPreviewDelegate(view)];
    }
    function onStop(state) { map.save(); }
}

class TrailField extends WatchUi.DataField {
    var map;
    var renderer;
    function initialize(state, painter) {
        DataField.initialize(); map = state; renderer = painter;
    }
    function compute(info) {
        map.running = info.timerState == Activity.TIMER_STATE_ON;
        map.recording = map.running || info.timerState == Activity.TIMER_STATE_PAUSED || info.timerState == Activity.TIMER_STATE_STOPPED;
        if (info.currentLocation == null) { map.updateMotion(null,null,null,null,0); }
        else {
            var p = info.currentLocation.toDegrees();
            var xy = map.project(p[0], p[1]) as Lang.Array;
            map.metersPerPixel=40075016.68557849d*Math.cos(p[0]*Math.PI/180.0d)/(256.0d*Math.pow(2,OfflineMap.ZOOM));
            map.updateMotion(xy[0],xy[1],info.currentHeading,info.currentSpeed,info.currentLocationAccuracy);
        }
    }
    function onTimerStart() { map.recording=true; map.running=true; map.followLocation(); }
    function onHide() { renderer.release(); }
    function onUpdate(dc) { renderer.draw(dc, null); }
}

function trailText(id) { return WatchUi.loadResource(id); }
