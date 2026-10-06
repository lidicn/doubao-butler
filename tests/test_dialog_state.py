import unittest

from butler.core.state import DialogState, RuntimeState


class TestState(unittest.TestCase):
    def test_present_tracking(self):
        st = RuntimeState()
        st.mark_present("Kevin", 0.9)
        self.assertTrue(st.is_present("Kevin"))
        st.mark_absent("Kevin")
        self.assertFalse(st.is_present("Kevin"))

    def test_state_transitions(self):
        st = RuntimeState()
        self.assertEqual(st.state, DialogState.IDLE)
        st.set_state(DialogState.THINKING)
        self.assertEqual(st.state, DialogState.THINKING)
        st.set_state(DialogState.SPEAKING)
        self.assertEqual(st.state, DialogState.SPEAKING)

    def test_speak_note_rollover(self):
        st = RuntimeState()
        before = st.today_turns
        st.note_speak("Kevin")
        self.assertEqual(st.today_turns, before + 1)
        self.assertEqual(st.last_speak_member, "Kevin")

    def test_mute(self):
        st = RuntimeState()
        self.assertFalse(st.is_muted())
        st.mute(30)
        self.assertTrue(st.is_muted())


if __name__ == "__main__":
    unittest.main()
