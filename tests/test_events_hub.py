"""The per-user event log behind the session WebSocket: sequence numbers, replay, buffer limit, expiry."""
import asyncio

from orchestrator.events import EventHub


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def publish(hub, user, n=1, channel="checkout", type_="step"):
    return [hub.publish(user, channel, type_, {"n": i}) for i in range(n)]


def test_each_user_has_their_own_strictly_increasing_sequence():
    hub = EventHub()
    a = publish(hub, "U-1", 3)
    b = publish(hub, "U-2", 2)
    assert [e["seq"] for e in a] == [1, 2, 3] and [e["seq"] for e in b] == [1, 2]
    assert set(a[0]) == {"seq", "channel", "type", "data", "ts"} and a[0]["channel"] == "checkout"


def test_replay_returns_events_after_n_in_order():
    hub = EventHub()
    publish(hub, "U-1", 5)
    events, resync = hub.replay("U-1", after=2)
    assert [e["seq"] for e in events] == [3, 4, 5] and not resync
    assert hub.replay("U-1", after=5) == ([], False)
    assert hub.replay("U-1", after=None) == ([], False)  # no `after` means "from now"
    assert [e["seq"] for e in hub.replay("U-1", after=0)[0]] == [1, 2, 3, 4, 5]


def test_only_the_last_n_events_are_kept_and_an_older_cursor_means_resync():
    hub = EventHub(max_events=3)
    publish(hub, "U-1", 10)
    events, resync = hub.replay("U-1", after=7)
    assert [e["seq"] for e in events] == [8, 9, 10] and not resync  # exactly what the buffer still has: no gap
    events, resync = hub.replay("U-1", after=3)
    assert resync and events == []  # events 4..7 are gone: the client must refetch instead
    assert hub.replay("U-1", after=0)[1] is True


def test_a_cursor_from_the_future_means_resync():
    """After a server restart the log is empty and numbering restarts, so an old client cursor can be ahead."""
    hub = EventHub()
    publish(hub, "U-1", 2)
    assert hub.replay("U-1", after=40) == ([], True)
    assert hub.replay("U-new", after=3) == ([], True)


def test_subscribers_receive_new_events_only_and_each_gets_a_copy():
    async def scenario():
        hub = EventHub()
        publish(hub, "U-1", 2)
        one, two = hub.subscribe("U-1"), hub.subscribe("U-1")
        hub.publish("U-1", "checkout", "done", {})
        hub.publish("U-2", "checkout", "done", {})  # someone else's event
        return [(await asyncio.wait_for(s.get(), 1))["seq"] for s in (one, two)], one.empty()

    assert asyncio.run(scenario()) == ([3, 3], True)


def test_a_user_with_no_sockets_is_dropped_an_hour_after_the_last_one_closed():
    clock = Clock()
    hub = EventHub(clock=clock)
    publish(hub, "U-1", 2)
    sub = hub.subscribe("U-1")
    clock.now += 7200
    hub.purge()
    assert hub.latest_seq("U-1") == 2  # still connected: kept however old
    hub.unsubscribe("U-1", sub)
    clock.now += 3599
    hub.purge()
    assert hub.latest_seq("U-1") == 2  # not an hour yet
    clock.now += 2
    hub.purge()
    assert hub.latest_seq("U-1") == 0 and hub.replay("U-1", after=2) == ([], True)


def test_reconnecting_before_the_hour_keeps_the_log():
    clock = Clock()
    hub = EventHub(clock=clock)
    publish(hub, "U-1", 3)
    hub.unsubscribe("U-1", hub.subscribe("U-1"))
    clock.now += 1800
    hub.subscribe("U-1")
    clock.now += 7200
    hub.purge()
    assert hub.latest_seq("U-1") == 3
