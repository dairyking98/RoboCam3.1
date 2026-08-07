"""Tests for MotionController's cached-profile accessor."""
from robocam.motion import MotionController


class TestGetCachedProfile:
    """get_cached_profile() must never trigger a hardware query itself --
    it exists specifically so a live UI recompute (e.g. estimated time per
    pass, as well selection/settings change) doesn't hammer the backend
    with a fresh read_profiles() every time -- a real M503 serial
    round-trip on Marlin, up to a 15s timeout."""

    def test_empty_until_a_profile_is_applied(self):
        # SimulationBackend starts with no profile by design ("simulate
        # mode shouldn't invent plausible-looking numbers") -- matches
        # get_cached_profile()'s empty-dict fallback.
        motion = MotionController(simulate=True)
        assert motion.get_cached_profile() == {}

    def test_reflects_applied_profile_without_a_fresh_read(self):
        motion = MotionController(simulate=True)
        motion.apply_profiles({
            "max_feed_x": 100.0, "max_accel_x": 500.0,
            "max_feed_y": 100.0, "max_accel_y": 500.0,
            "max_feed_z": 20.0, "max_accel_z": 100.0,
        })
        cached = motion.get_cached_profile()
        assert cached["max_feed_x"] == 100.0
        assert cached["max_accel_z"] == 100.0

    def test_returned_dict_is_a_copy(self):
        motion = MotionController(simulate=True)
        motion.apply_profiles({"max_feed_x": 100.0})
        cached = motion.get_cached_profile()
        cached["max_feed_x"] = 999.0
        assert motion.get_cached_profile()["max_feed_x"] == 100.0

    def test_read_profiles_also_updates_the_cache(self):
        motion = MotionController(simulate=True)
        motion.apply_profiles({"max_feed_x": 50.0})
        # A direct read_profiles() call (e.g. from run()'s own ETA path)
        # should keep the cache in sync too, not just apply_profiles().
        live = motion.read_profiles()
        assert motion.get_cached_profile() == live
