/*
 * Module: "follow the selected train" — recenters the map on it every poll while active.
 * Starts automatically on selection; a manual drag/scroll of the map cancels it (dragging
 * is never triggered by our own panTo, so this only fires on real user intent). Toggle via
 * Follow.toggle(); other modules read Follow.isFollowing() and listen for the 'follow' event.
 */
window.Follow = (function () {
  var tripIdx = null;
  var following = false;

  function positionOf(idx) {
    return App.state.positions.find(function (t) { return t.trip_idx === idx; });
  }

  function centerOn(t, animate) {
    App.map.panTo([t.lat, t.lon], { animate: animate, duration: 0.9, easeLinearity: 0.3 });
  }

  function setFollowing(on) {
    if (on === following) return;
    following = on && tripIdx !== null;
    App.emit('follow', following);
    if (following) {
      var t = positionOf(tripIdx);
      if (t) centerOn(t, false);
    }
  }

  App.on('select', function (idx) {
    tripIdx = idx;
    setFollowing(idx !== null);
  });

  App.on('positions', function () {
    if (!following) return;
    var t = positionOf(tripIdx);
    if (t) centerOn(t, true);
  });

  App.map.on('dragstart', function () { setFollowing(false); });

  return {
    isFollowing: function () { return following; },
    toggle: function () { setFollowing(!following); }
  };
})();
