import Toybox.Application;
import Toybox.WatchUi;
import Toybox.Timer;

// Pre-race browsing is an independent app, so the watch's activity settings
// menu and the current GPS location cannot hide or interrupt its controls.
// No GPS or activity recording is started by this app.
class TrailViewerApp extends Application.AppBase {
    var map;
    var renderer;
    var noticeShown = false;
    function initialize() {
        AppBase.initialize();
        map = new TrailMapState();
        map.standalone = true;
        renderer = new TrailRenderer(map);
    }
    function getInitialView() {
        var view = new TrailPreview(map,renderer);
        if (!noticeShown) {
            noticeShown = true;
            var notice = new TrailAttributionView(view);
            return [notice,new TrailAttributionDelegate(notice)];
        }
        return [view,new TrailPreviewDelegate(view)];
    }
    function onStop(state) { map.save(); }
}

// Attribution appears once per app launch, on its own screen. The map itself
// stays unobstructed; the same sources and links remain in the map's menu.
class TrailAttributionView extends WatchUi.View {
    var preview;
    var timer = null;
    var finished = false;
    function initialize(nextView) { View.initialize(); preview=nextView; }
    function onShow() {
        if (!finished && timer==null) {
            timer = new Timer.Timer(); timer.start(method(:enterMap),5000,false);
        }
    }
    function stopTimer() { if (timer!=null) { timer.stop(); timer=null; } }
    function onHide() { stopTimer(); }
    function onUpdate(dc) {
        trailDrawCredits(dc,[Rez.Strings.AttributionData,Rez.Strings.AttributionTerrain,
            Rez.Strings.AttributionStyle,Rez.Strings.AttributionLicense,Rez.Strings.AttributionContinue]);
    }
    function enterMap() as Void {
        if (finished) { return; }
        finished=true; stopTimer();
        WatchUi.switchToView(preview,new TrailPreviewDelegate(preview),WatchUi.SLIDE_IMMEDIATE);
    }
    function leave() {
        if (finished) { return; }
        finished=true; stopTimer(); WatchUi.popView(WatchUi.SLIDE_RIGHT);
    }
}

class TrailAttributionDelegate extends WatchUi.BehaviorDelegate {
    var view;
    function initialize(notice) { BehaviorDelegate.initialize(); view=notice; }
    function onSelect() { view.enterMap(); return true; }
    function onBack() { view.leave(); return true; }
}
