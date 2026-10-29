import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Activity;
import Toybox.Lang;

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
        map.recording = info.timerState == Activity.TIMER_STATE_ON || info.timerState == Activity.TIMER_STATE_PAUSED;
        if (info.currentLocation == null) { map.updateGps(null, null, null); }
        else {
            var p = info.currentLocation.toDegrees();
            var xy = map.project(p[0], p[1]) as Lang.Array;
            map.updateGps(xy[0], xy[1], info.currentHeading);
        }
    }
    function onTimerStart() { map.followLocation(); }
    function onHide() { renderer.release(); }
    function onUpdate(dc) { renderer.draw(dc, null); }
}

function trailText(id) { return WatchUi.loadResource(id); }
