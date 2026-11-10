"""Menu routing and input isolation without Tk, hardware, or emulator processes."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from redux.console_ui import ConsoleApp
from redux.controllers import GamepadState, GameplayInputMapper, InputEvent


class UnifiedGameMenuTests(unittest.TestCase):
    def test_player_port_reassignment_is_not_a_physical_disconnect(self):
        app=self.bare();device={"instance_id":77,"slot":0}
        app.controller=SimpleNamespace(devices=lambda:[dict(device)])
        before=app._controller_connections()
        device["slot"]=2
        after=app._controller_connections()
        self.assertEqual(set(before)-set(after),set())
        self.assertEqual(after,{77:2})

    def test_load_and_used_slot_save_require_confirmation_before_backend_operation(self):
        from redux.state_presentation import SlotCard
        for operation in ("save","load"):
            app=self.embedded();app.state_operation=Mock()
            card=SlotCard(2,True,"2026-11-08T20:00:00Z")
            app._request_state_operation(operation,card)
            app.state_operation.assert_not_called()
            self.assertIn("this state?",app.overlay["title"].lower())
            app.overlay["items"][1][1]()
            app.state_operation.assert_called_once_with(operation,2,None)

    def bare(self):
        app=object.__new__(ConsoleApp)
        app.busy=False;app.embedded=None;app.overlay=None;app.screen="home"
        app.focus="hero:play";app.held_keys=set();app.input_release_gate=False
        app.playback=SimpleNamespace(active=Mock(return_value=[]),pause=Mock(),resume=Mock())
        app.root=SimpleNamespace(deiconify=Mock(),lift=Mock(),focus_force=Mock(),iconify=Mock())
        app.bridges={};app.status=SimpleNamespace(set=Mock())
        app.schedule_draw=Mock();app.navigate=Mock()
        app.gameplay_input=GameplayInputMapper()
        app.controller=None
        return app

    def embedded(self):
        app=self.bare();app.screen="gameplay"
        app.embedded=SimpleNamespace(host=SimpleNamespace(set_input=Mock()),
            pause=Mock(),resume=Mock(),play_seconds=23)
        app.embedded_game={"id":2,"title":"Fixture game","system":"nes","bundle_hash":"a"*64}
        app.game_filter="sharp"
        return app

    def test_guide_and_confirm_same_frame_do_not_activate_fresh_menu_in_either_order(self):
        for events in ([InputEvent("guide"),InputEvent("confirm")],
                       [InputEvent("confirm"),InputEvent("guide")]):
            with self.subTest(events=events):
                app=self.embedded()
                app.input_action=Mock(side_effect=AssertionError("Must discard navigation from the menu-opening frame"))
                app._route_controller_events(events)
                self.assertEqual(app.overlay["title"],"Game paused")
                app.embedded.pause.assert_called_once()
                app.input_action.assert_not_called()

    def test_guide_and_fallback_chord_share_the_reserved_path_and_only_toggle_once(self):
        for event in (InputEvent("guide"),InputEvent("menu",chord=True)):
            app=self.bare();app._request_game_menu=Mock();app.input_action=Mock()
            app._route_controller_events([event,InputEvent("confirm")])
            app._request_game_menu.assert_called_once_with()
            app.input_action.assert_not_called()
        app=self.bare();app._request_game_menu=Mock()
        app._route_controller_events([InputEvent("guide"),InputEvent("menu",chord=True)])
        app._request_game_menu.assert_called_once_with()

    def test_release_edges_and_ordinary_start_do_not_open_menu_during_gameplay(self):
        app=self.embedded();app._request_game_menu=Mock();app.input_action=Mock()
        app._route_controller_events([InputEvent("guide",False),InputEvent("menu"),InputEvent("confirm")])
        app._request_game_menu.assert_not_called();app.input_action.assert_not_called()
        self.assertIsNone(app.overlay)

    def test_menu_navigation_runs_only_after_menu_is_already_open(self):
        app=self.embedded();app.game_menu();app.input_action=Mock()
        app._route_controller_events([InputEvent("down"),InputEvent("confirm"),InputEvent("guide",False)])
        self.assertEqual([call.args[0] for call in app.input_action.call_args_list],["down","confirm"])

    def test_embedded_guide_toggles_pause_resume_and_clears_held_keyboard(self):
        app=self.embedded();app.held_keys={"x","up"}
        app._request_game_menu()
        self.assertEqual(app.overlay["title"],"Game paused")
        self.assertTrue(app.input_release_gate);self.assertEqual(app.held_keys,set())
        self.assertIn("Display",[label for label,_ in app.overlay["items"]])
        app.held_keys={"return"};app._request_game_menu()
        app.embedded.resume.assert_called_once_with()
        self.assertIsNone(app.overlay);self.assertTrue(app.input_release_gate)
        self.assertEqual(app.held_keys,set());self.assertEqual(app.focus,"gameplay:menu")

    def test_resume_holds_all_four_players_at_zero_until_every_button_releases(self):
        app=self.embedded()
        states={0:GamepadState(0x1000),1:GamepadState(),2:GamepadState(),3:GamepadState(0x2000)}
        app.controller=SimpleNamespace(api=SimpleNamespace(state=lambda port:states.get(port)))
        masks={};app.embedded.host.set_input=lambda port,mask:masks.update({port:mask})
        app._resume_embedded()
        app._embedded_input()
        self.assertEqual(masks,{0:0,1:0,2:0,3:0});self.assertTrue(app.input_release_gate)
        states[0]=GamepadState();app._embedded_input()
        self.assertEqual(masks,{0:0,1:0,2:0,3:0});self.assertTrue(app.input_release_gate)
        states[3]=GamepadState();app._embedded_input()
        self.assertFalse(app.input_release_gate);self.assertEqual(masks,{0:0,1:0,2:0,3:0})
        states[0]=GamepadState(0x1000);app._embedded_input()
        self.assertEqual(masks[0],1<<8)

    def test_input_held_while_paused_never_reaches_embedded_core(self):
        app=self.embedded();app.game_menu()
        app.controller=SimpleNamespace(api=SimpleNamespace(state=lambda port:GamepadState(0x1000) if port==0 else None))
        app._embedded_input()
        self.assertEqual([call.args for call in app.embedded.host.set_input.call_args_list],[(0,0),(1,0),(2,0),(3,0)])

    def test_resume_waits_for_sticks_and_triggers_to_return_to_neutral_across_players(self):
        for held in (GamepadState(thumb_lx=24000),GamepadState(thumb_ly=-24000),
                     GamepadState(thumb_rx=24000),GamepadState(thumb_ry=-24000),
                     GamepadState(left_trigger=180),GamepadState(right_trigger=180)):
            with self.subTest(held=held):
                app=self.embedded();states={0:GamepadState(0x1000),3:held}
                app.controller=SimpleNamespace(api=SimpleNamespace(state=lambda port:states.get(port)))
                masks={};app.embedded.host.set_input=lambda port,mask:masks.update({port:mask})
                app._resume_embedded();app._embedded_input()
                self.assertEqual(masks,{0:0,1:0,2:0,3:0})
                states[0]=GamepadState();app._embedded_input()
                self.assertTrue(app.input_release_gate,"Another player's held stick/trigger must retain the resume gate")
                self.assertEqual(masks,{0:0,1:0,2:0,3:0})
                states[3]=GamepadState();app._embedded_input()
                self.assertFalse(app.input_release_gate)
                states[0]=GamepadState(0x1000);app._embedded_input()
                self.assertEqual(masks[0],1<<8)

    def test_external_menu_toggle_resumes_only_matching_owned_session(self):
        app=self.bare();app.playback.active.return_value=[SimpleNamespace(session_id="current")]
        app.overlay={"resume_id":"current"};app._resume=Mock();app.game_menu=Mock()
        app._request_game_menu()
        app._resume.assert_called_once_with("current");app.game_menu.assert_not_called()
        app._resume.reset_mock();app.overlay={"resume_id":"old-session"}
        app._request_game_menu()
        app._resume.assert_not_called();app.game_menu.assert_called_once_with()

    def test_arbitrary_external_emulator_menu_does_not_claim_state_or_filter_support(self):
        app=self.bare();app.playback.active.return_value=[SimpleNamespace(session_id="custom",paused=False)]
        app.game_menu()
        labels=[label for label,_ in app.overlay["items"]]
        self.assertEqual(app.overlay["resume_id"],"custom")
        app.playback.pause.assert_called_once_with("custom")
        self.assertIn("Resume game",labels)
        self.assertFalse(any("state" in label.lower() or "filter" in label.lower() for label in labels),labels)
        with self.assertRaisesRegex(RuntimeError,"no state interface"):
            app.state_menu("save","custom")

    def test_ready_external_bridge_exposes_real_state_callbacks_for_current_session(self):
        app=self.bare();app.playback.active.return_value=[SimpleNamespace(session_id="psx",paused=True)]
        app.bridges["psx"]=SimpleNamespace(ready=True)
        app.state_menu=Mock();app.state_operation=Mock();app.game_menu()
        items=dict(app.overlay["items"])
        items["Save state"]();items["Load state"]();items["Take screenshot"]()
        self.assertEqual([call.args for call in app.state_menu.call_args_list],[("save","psx"),("load","psx")])
        app.state_operation.assert_called_once_with("capture",sid="psx")
        self.assertNotIn("Video filters",items)

    def test_busy_menu_request_does_not_interrupt_state_operation(self):
        app=self.embedded();app.busy=True;app.game_menu=Mock()
        app._request_game_menu()
        app.game_menu.assert_not_called();app.embedded.resume.assert_not_called()

    def test_guide_does_not_abandon_text_editor_when_no_game_is_running(self):
        app=self.bare();app.screen="text";app._request_game_menu()
        app.navigate.assert_not_called()
        app.screen="library";app._request_game_menu()
        app.navigate.assert_called_once_with("home",remember=False)

    def test_quick_quit_has_priority_over_guide_and_confirmation(self):
        for embedded in (True,False):
            with self.subTest(embedded=embedded):
                app=self.embedded() if embedded else self.bare()
                app.playback.active.return_value=[SimpleNamespace(session_id="owned-session")]
                app._stop_embedded=Mock();app._stop=Mock();app._request_game_menu=Mock();app.input_action=Mock()
                app._route_controller_events([InputEvent("confirm"),InputEvent("guide"),InputEvent("quit_game")])
                if embedded:app._stop_embedded.assert_called_once_with()
                else:app._stop.assert_called_once_with("owned-session")
                app._request_game_menu.assert_not_called();app.input_action.assert_not_called()

    def test_quick_quit_during_state_operation_does_not_interrupt_or_open_menu(self):
        app=self.embedded();app.busy=True;app._stop_embedded=Mock();app._request_game_menu=Mock()
        app._route_controller_events([InputEvent("quit_game"),InputEvent("guide")])
        app._stop_embedded.assert_not_called();app._request_game_menu.assert_not_called()

    def test_disconnect_pauses_embedded_game_and_identifies_affected_player(self):
        app=self.embedded();app.pending_controller_disconnect=2
        app._pause_for_controller_disconnect()
        app.embedded.pause.assert_called_once_with()
        self.assertIsNone(app.pending_controller_disconnect)
        self.assertIn("PLAYER 3",app.overlay["message"])
        app.status.set.assert_called_once_with("CONTROLLER DISCONNECTED")

    def test_disconnect_keeps_existing_paused_submenu_without_resuming_or_replacing_it(self):
        app=self.embedded();app.overlay={"title":"Save state","items":[("Slot 1",lambda:None)]}
        old=app.overlay;app.pending_controller_disconnect=0
        app._pause_for_controller_disconnect()
        self.assertIs(app.overlay,old)
        self.assertEqual(app.overlay["title"],"Save state")
        app.embedded.resume.assert_not_called();app.embedded.pause.assert_not_called()


if __name__=="__main__":unittest.main()
