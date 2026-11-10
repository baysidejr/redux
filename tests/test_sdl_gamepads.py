"""Controller protocol normalization and shortcut behavior without key injection."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from redux.controllers import (ControllerManager,GamepadState,GameplayInputMapper,
    NavigationMapper,create_controller)
from redux.sdl_gamepads import SDLGamepadAPI,SDLInputError,_GUID,validate_mapping


class FakeSDL:
    def __init__(self):
        self.present={41,42};self.mapped={41};self.hints=[];self.closed=[];self.pumps=0;self.maps=[]
        self.buttons={41:{0,5,11}};self.axes={41:{0:20000,1:-12000,4:32767}}
    def SetHint(self,*args):self.hints.append(args);return True
    def InitSubSystem(self,flags):return True
    def QuitSubSystem(self,flags):self.closed.append(("quit",flags))
    def SetGamepadEventsEnabled(self,value):pass
    def SetJoystickEventsEnabled(self,value):pass
    def PumpEvents(self):self.pumps+=1
    def UpdateGamepads(self):pass
    def ids(self,gamepads):return sorted(self.present&self.mapped if gamepads else self.present)
    def OpenGamepad(self,instance):return instance
    def CloseGamepad(self,handle):self.closed.append(("gamepad",handle))
    def OpenJoystick(self,instance):return instance
    def CloseJoystick(self,handle):self.closed.append(("joystick",handle))
    def GetGamepadJoystick(self,handle):return handle
    def GetGamepadName(self,handle):return b"Test pad"
    def GetJoystickNameForID(self,instance):return b"Unknown NES USB pad"
    def GetGamepadSerial(self,handle):return b"serial"+str(handle).encode()
    GetJoystickSerial=GetGamepadSerial
    def GetJoystickGUIDForID(self,instance):return _GUID((instance,*([0]*15)))
    def GetJoystickVendorForID(self,instance):return 123
    def GetJoystickProductForID(self,instance):return instance
    def GamepadHasButton(self,handle,button):return button==5
    def GetNumJoystickButtons(self,handle):return 12
    def GetNumJoystickAxes(self,handle):return 6
    def GetNumJoystickHats(self,handle):return 1
    def GetGamepadButton(self,handle,button):return button in self.buttons.get(handle,set())
    GetJoystickButton=GetGamepadButton
    def GetGamepadAxis(self,handle,axis):return self.axes.get(handle,{}).get(axis,0)
    GetJoystickAxis=GetGamepadAxis
    def GetJoystickHat(self,handle,index):return 1
    def GetGamepadType(self,handle):return 1
    def AddGamepadMapping(self,value):
        self.maps.append(value)
        if value.startswith(b"2a"+b"0"*30):self.mapped.add(42)
        return 1
    def GetVersion(self):return 3004020
    def error(self):return "test SDL error"


class SDLGamepadTests(unittest.TestCase):
    def make(self,native=None):
        native=native or FakeSDL()
        with patch("redux.sdl_gamepads.native_directory",return_value=Path("Z:/missing-test-assets")):
            api=SDLGamepadAPI(native=native)
        self.addCleanup(api.close)
        return api,native

    def test_sdl_normalizes_guide_dpad_axes_and_triggers_without_guessing_unknown_pad(self):
        api,native=self.make()
        state=api.state(0)
        self.assertEqual(state.buttons,0x1401)
        self.assertEqual((state.thumb_lx,state.thumb_ly,state.left_trigger),(20000,12000,255))
        devices=api.devices()
        self.assertEqual([(d["instance_id"],d["mapped"],d["slot"]) for d in devices],[(41,True,0),(42,False,None)])
        self.assertEqual(api.version,"3.4.20")
        self.assertIn((b"SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS",b"1"),native.hints)
        self.assertIsNone(api.state(1))

    def test_unknown_pad_exposes_read_only_raw_state_and_mapping_promotes_it(self):
        api,native=self.make()
        state=api.raw_state(42)
        self.assertEqual(len(state["buttons"]),12)
        self.assertEqual(len(state["axes"]),6)
        self.assertEqual(state["hats"],(1,))
        self.assertFalse(state["mapped"])
        mapping="2a"+"0"*30+",NES USB,a:b0,b:b1,back:b6,start:b7,dpup:h0.1,"
        api.add_mapping(mapping);api.refresh()
        self.assertTrue(next(d for d in api.devices() if d["instance_id"]==42)["mapped"])
        self.assertIn(("joystick",42),native.closed)
        self.assertEqual(api.slot_ids(),{0:41,1:42})

    def test_hotplug_keeps_connected_slots_and_clears_detached_state(self):
        api,native=self.make();native.present.add(43);native.mapped.add(43);api.refresh()
        self.assertEqual(api.slot_ids(),{0:41,1:43})
        native.present.remove(41);api.refresh()
        self.assertEqual(api.slot_ids(),{1:43});self.assertIsNone(api.state(0))
        self.assertIn(("gamepad",41),native.closed)
        native.present.add(41);api.refresh()
        self.assertEqual(api.slot_ids(),{1:43,0:41})

    def test_player_port_assignment_swaps_without_duplicate_slots_and_remembers_reconnect(self):
        api,native=self.make();native.present.add(43);native.mapped.add(43);api.refresh()
        api.assign_slot(41,1)
        self.assertEqual(api.slot_ids(),{1:41,0:43})
        api.assign_slot(41,3)
        self.assertEqual(api.slot_ids(),{3:41,0:43})
        native.present.remove(41);api.refresh();native.present.add(41);api.refresh()
        self.assertEqual(api.slot_ids(),{0:43,3:41})
        for bad in (-1,4,True):
            with self.assertRaises(ValueError):api.assign_slot(41,bad)
        with self.assertRaises(ValueError):api.assign_slot(42,1)

    def test_close_is_idempotent_and_does_not_shutdown_other_subsystems(self):
        api,native=self.make();api.close();api.close()
        self.assertEqual(native.closed.count(("gamepad",41)),1)
        self.assertEqual(native.closed.count(("joystick",42)),1)
        self.assertEqual(native.closed.count(("quit",0x2000)),1)
        self.assertIsNone(api.state(0));self.assertEqual(api.devices(),[])

    def test_mapping_file_filters_non_windows_profiles_and_custom_lines_are_bounded(self):
        api,native=self.make()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"pads.txt"
            path.write_text("# test\n"+"0"*32+",Pad,a:b0,platform:Linux,\n"+"1"*32+",Pad,a:b1,platform:Windows,\n",encoding="utf-8")
            self.assertEqual(api.load_mappings(path),1)
            self.assertEqual(len(native.maps),1)
        for bad in ("bad",None,"0"*32+",Pad,a:b0,\n", "0"*32+",Pad,a:b0,\0"):
            with self.subTest(bad=bad),self.assertRaises(ValueError):validate_mapping(bad)

    def test_native_input_must_remain_on_its_creating_main_thread(self):
        api,_=self.make();errors=[]
        def wrong_thread():
            try:api.refresh()
            except SDLInputError as error:errors.append(str(error))
        thread=threading.Thread(target=wrong_thread);thread.start();thread.join()
        self.assertEqual(len(errors),1)

    def test_guide_is_one_edge_with_no_same_frame_confirm_or_start_select(self):
        mapper=NavigationMapper(chord_hold=1)
        events=mapper.update(0,GamepadState(0x1430),0)
        self.assertEqual([(e.action,e.pressed) for e in events],[("guide",True)])
        self.assertEqual(mapper.update(0,GamepadState(0x1430),2),[])
        events=mapper.update(0,GamepadState(0x1030),2.1)
        self.assertEqual([(e.action,e.pressed) for e in events],[("guide",False)])
        self.assertEqual(mapper.update(0,GamepadState(0x1030),5),[])
        mapper.update(0,GamepadState(),5.1)
        self.assertEqual(mapper.update(0,GamepadState(0x1000),5.2)[0].action,"confirm")

    def test_select_start_requires_continuous_one_second_hold_and_rearms_after_release(self):
        mapper=NavigationMapper(chord_hold=1)
        self.assertEqual(mapper.update(0,GamepadState(0x30),0),[])
        self.assertEqual(mapper.update(0,GamepadState(0x30),.99),[])
        events=mapper.update(0,GamepadState(0x30),1)
        self.assertEqual([(e.action,e.chord) for e in events],[("menu",True)])
        self.assertEqual(mapper.update(0,GamepadState(0x30),5),[])
        mapper.update(0,GamepadState(),6)
        mapper.update(0,GamepadState(0x30),7)
        mapper.update(0,GamepadState(0x20),7.5)
        self.assertEqual(mapper.update(0,GamepadState(0x30),9),[])
        mapper.update(0,GamepadState(),9.1);mapper.update(0,GamepadState(0x30),10)
        self.assertTrue(mapper.update(0,GamepadState(0x30),11)[0].chord)

    def test_guide_disconnect_is_a_release_and_reconnect_is_clean(self):
        mapper=NavigationMapper();mapper.update(2,GamepadState(0x400),0)
        released=mapper.update(2,None,1)
        self.assertEqual([(e.action,e.pressed) for e in released],[("guide",False)])
        self.assertEqual(mapper.update(2,GamepadState(0x400),2)[0].action,"guide")

    def test_quit_chord_has_priority_over_guide_and_menu_and_latches_until_release(self):
        mapper=NavigationMapper(chord_hold=1)
        events=mapper.update(0,GamepadState(0x1730),0)
        self.assertEqual([(e.action,e.chord) for e in events],[("quit_game",True)])
        for buttons in (0x1730,0x330,0x30,0x10,0x330):
            self.assertEqual(mapper.update(0,GamepadState(buttons),2),[])
        mapper.update(0,GamepadState(),3)
        self.assertEqual(mapper.update(0,GamepadState(0x330),4)[0].action,"quit_game")
        mapper.update(0,GamepadState(),5);mapper.update(0,GamepadState(0x30),6)
        self.assertTrue(mapper.update(0,GamepadState(0x30),7)[0].chord)
        game=GameplayInputMapper()
        for buttons in (0x1730,0x330,0x130,0x100,0):
            self.assertEqual(game.update(0,GamepadState(buttons),5),0)
        self.assertEqual(game.update(0,GamepadState(0x1000),6),1<<8)

    def test_guide_and_resume_barrier_never_reach_the_game_or_replay_pending_taps(self):
        mapper=GameplayInputMapper()
        mapper.update(0,GamepadState(0x10),0)
        self.assertEqual(mapper.update(0,GamepadState(0x1410),.01),0)
        self.assertEqual(mapper.update(0,GamepadState(),.02),0)
        mapper.block_until_release()
        for now in (1,2,3):self.assertEqual(mapper.update(0,GamepadState(0x1000),now),0)
        self.assertEqual(mapper.update(0,GamepadState(),4),0)
        self.assertEqual(mapper.update(0,GamepadState(0x1000),5),1<<8)

    def test_controller_manager_prefers_one_native_source_and_resets_replaced_slots(self):
        api,native=self.make();controller=ControllerManager(api=api)
        self.assertEqual(controller.mapper.chord_hold,1)
        events=controller.poll(0)
        self.assertEqual([e.action for e in events if e.pressed],["guide"])
        self.assertEqual(controller.connected_indices,(0,))
        native.present.remove(41);native.present.add(43);native.mapped.add(43);native.buttons[43]={0}
        self.assertEqual([e.action for e in controller.poll(1) if e.pressed],["confirm"])

    def test_native_sdl_initialization_and_read_only_discovery(self):
        controller=create_controller()
        try:
            self.assertIsInstance(controller.poll(),list)
            self.assertTrue(controller.available)
            self.assertEqual(controller.backend,"SDL3",controller.failure)
            for device in controller.devices():
                self.assertIn("mapped",device)
                self.assertEqual(len(device["guid"]),32)
        finally:controller.close()

    def test_closed_manager_does_not_pump_native_input_again(self):
        api,native=self.make();controller=ControllerManager(api=api);controller.close()
        before=native.pumps
        self.assertFalse(controller.available)
        self.assertEqual(controller.poll(),[]);self.assertEqual(controller.devices(),[])
        self.assertEqual(native.pumps,before)

    def test_corrupt_custom_mapping_does_not_disable_known_controller_support(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"pads.txt";path.write_text("damaged controller profile",encoding="utf-8")
            with patch("redux.sdl_gamepads.native_directory",return_value=Path("Z:/missing-test-assets")):
                api=SDLGamepadAPI(native=FakeSDL(),mapping_file=path)
            try:
                self.assertEqual(len(api.warnings),1)
                self.assertIsNotNone(api.state(0))
            finally:api.close()


if __name__=="__main__":unittest.main()
