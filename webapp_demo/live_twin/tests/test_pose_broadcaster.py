from live_twin.backend.pose.broadcaster import PoseBroadcaster


def test_frame_subscription_keeps_only_the_latest_preview_frame():
    broadcaster = PoseBroadcaster()
    queue = broadcaster.subscribe_frames()

    broadcaster._publish_frame(b"older-jpeg")
    broadcaster._publish_frame(b"latest-jpeg")

    assert queue.get_nowait() == b"latest-jpeg"
    assert queue.empty()

    broadcaster.unsubscribe_frames(queue)
    assert queue not in broadcaster._frame_subscribers
