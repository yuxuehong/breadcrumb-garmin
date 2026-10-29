import Toybox.WatchUi;

class TrailPreview extends WatchUi.View {
    var map;
    var renderer;
    var control = 0;
    var firstFit = false;
    var width = 260;
    var height = 260;
    function initialize(state,painter) {
        View.initialize(); map = state; renderer = painter;
        firstFit = map.follow; map.beginPreview();
    }
    function onUpdate(dc) {
        width = dc.getWidth(); height = dc.getHeight();
        if (firstFit) { map.fit(width,height); firstFit = false; }
        renderer.draw(dc,control);
    }
    function onHide() { renderer.release(); map.save(); }
    function adjust(direction) {
        if (control == 0) { map.changeZoom(direction); }
        else if (control == 1) { map.pan(0,-direction); }
        else { map.pan(-direction,0); }
        WatchUi.requestUpdate();
    }
}

class TrailPreviewDelegate extends WatchUi.BehaviorDelegate {
    var view;
    function initialize(preview) { BehaviorDelegate.initialize(); view = preview; }
    function onPreviousPage() { view.adjust(1); return true; }
    function onNextPage() { view.adjust(-1); return true; }
    function onSelect() {
        view.control = (view.control+1)%3;
        view.map.beginPreview(); WatchUi.requestUpdate(); return true;
    }
    function onBack() { view.map.save(); WatchUi.popView(WatchUi.SLIDE_RIGHT); return true; }
    function onMenu() {
        var menu = new WatchUi.Menu2({:title=>trailText(Rez.Strings.MapMenu)});
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.FullRoute),null,:overview,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.ToStart),null,:start,{}));
        menu.addItem(new WatchUi.MenuItem(trailText(Rez.Strings.FollowAction),null,:follow,{}));
        WatchUi.pushView(menu,new TrailMenuDelegate(view),WatchUi.SLIDE_UP); return true;
    }
}

class TrailMenuDelegate extends WatchUi.Menu2InputDelegate {
    var view;
    function initialize(preview) { Menu2InputDelegate.initialize(); view = preview; }
    function onSelect(item) {
        var id = item.getId();
        if (id == :overview) { view.map.beginPreview(); view.map.fit(view.width,view.height); }
        else if (id == :start) { view.map.startPoint(); }
        else if (id == :follow) { view.map.followLocation(); }
        view.control = 0; view.map.save();
        WatchUi.popView(WatchUi.SLIDE_DOWN); WatchUi.requestUpdate();
    }
}
