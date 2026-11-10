import unittest

from redux.controllers import GamepadState, NavigationMapper, XInputController


def pressed(events):return [e.action for e in events if e.pressed]


class ControllerTests(unittest.TestCase):
    def test_button_edges_releases_and_direction_repeat(self):
        mapper=NavigationMapper()
        events=mapper.update(0,GamepadState(0x1008),0)
        self.assertEqual(pressed(events),["confirm","right"])
        self.assertEqual(mapper.update(0,GamepadState(0x1008),.2),[])
        repeat=mapper.update(0,GamepadState(0x1008),.4)
        self.assertEqual(pressed(repeat),["right"]);self.assertTrue(repeat[0].repeat)
        self.assertEqual(pressed(mapper.update(0,GamepadState(0x1008),5)),["right"])
        self.assertEqual({e.action for e in mapper.update(0,GamepadState(),5.1) if not e.pressed},{"confirm","right"})

    def test_stick_hysteresis_does_not_chatter(self):
        mapper=NavigationMapper()
        self.assertEqual(pressed(mapper.update(0,GamepadState(thumb_lx=18000),0)),["right"])
        self.assertEqual(mapper.update(0,GamepadState(thumb_lx=14000),.1),[])
        events=mapper.update(0,GamepadState(thumb_lx=10000),.2)
        self.assertEqual([(e.action,e.pressed) for e in events],[("right",False)])
        self.assertEqual(mapper.update(0,GamepadState(thumb_lx=14000),.3),[])
        self.assertEqual(pressed(mapper.update(0,GamepadState(thumb_lx=-18000),.4)),["left"])

    def test_start_back_chord_is_one_edge_without_home_or_normal_start(self):
        mapper=NavigationMapper()
        self.assertEqual(mapper.update(0,GamepadState(0x10),0),[])
        events=mapper.update(0,GamepadState(0x30),.05)
        self.assertEqual([(e.action,e.chord) for e in events],[("menu",True)])
        self.assertEqual(mapper.update(0,GamepadState(0x30),1),[])
        self.assertEqual(mapper.update(0,GamepadState(0x20),1.1),[])
        self.assertEqual(mapper.update(0,GamepadState(0x30),1.2),[])
        mapper.update(0,GamepadState(),1.3)
        self.assertTrue(mapper.update(0,GamepadState(0x30),1.4)[0].chord)

    def test_quick_single_start_tap_is_preserved(self):
        mapper=NavigationMapper();mapper.update(2,GamepadState(0x10),0)
        events=mapper.update(2,GamepadState(),.03)
        self.assertEqual([(e.action,e.pressed,e.controller,e.chord) for e in events],
                         [("menu",True,2,False),("menu",False,2,False)])

    def test_disconnect_releases_and_reconnect_starts_clean(self):
        mapper=NavigationMapper();mapper.update(1,GamepadState(0x1001),0)
        released=mapper.update(1,None,.1)
        self.assertEqual({e.action for e in released},{"confirm","up"})
        self.assertTrue(all(not e.pressed for e in released))
        self.assertEqual(pressed(mapper.update(1,GamepadState(0x1000),.2)),["confirm"])
        mapper.update(2,GamepadState(0x10),0);mapper.update(2,GamepadState(0x10),.11)
        self.assertEqual([(e.action,e.pressed) for e in mapper.update(2,None,.12)],[("menu",False)])

    def test_poller_reports_connected_controllers_without_injection(self):
        class API:
            def state(self,index):return GamepadState(0x1000) if index==3 else None
        controller=XInputController(api=API())
        self.assertTrue(controller.available)
        self.assertEqual(controller.poll(0)[0].controller,3)
        self.assertEqual(controller.connected_indices,(3,))

    def test_native_xinput_no_controller_is_normal(self):
        controller=XInputController();events=controller.poll()
        self.assertIsInstance(events,list)
        self.assertTrue(all(0<=index<=3 for index in controller.connected_indices))


if __name__=="__main__":unittest.main()
