import unittest
from redux.ui_model import FocusTarget,hit_test,next_focus,page_window,ScreenHistory


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.targets=[FocusTarget("home",(0,0,80,30)),FocusTarget("library",(100,0,180,30)),
                      FocusTarget("play",(0,80,100,120)),
                      *[FocusTarget(f"game{i}",(i%3*100,170+i//3*100,i%3*100+90,250+i//3*100)) for i in range(6)]]

    def test_directional_grid_and_header_navigation(self):
        self.assertEqual(next_focus(self.targets,"game1","right"),"game2")
        self.assertEqual(next_focus(self.targets,"game1","down"),"game4")
        self.assertEqual(next_focus(self.targets,"game3","up"),"game0")
        self.assertEqual(next_focus(self.targets,"play","up"),"home")

    def test_edges_do_not_jump_to_unrelated_controls(self):
        self.assertEqual(next_focus(self.targets,"game0","left"),"game0")
        self.assertEqual(next_focus(self.targets,"game5","down"),"game5")
        self.assertEqual(next_focus([],None,"left"),None)

    def test_overlapping_hits_choose_the_topmost_and_blank_is_not_clickable(self):
        targets=[FocusTarget("base",(0,0,100,100)),FocusTarget("dialog",(10,10,50,50))]
        self.assertEqual(hit_test(targets,20,20),"dialog")
        self.assertIsNone(hit_test(targets,200,200))

    def test_scrolling_keeps_selection_in_window(self):
        for count in (1,9,10,11,64,100):
            for selected in range(count):
                start,end=page_window(count,selected)
                self.assertTrue(start<=selected<end)
                self.assertLessEqual(end-start,10)

    def test_history_preserves_copied_state(self):
        history=ScreenHistory();state={"screen":"home","focus":"game1"};history.push(state)
        state["focus"]="changed"
        self.assertEqual(history.pop()["focus"],"game1")
        self.assertIsNone(history.pop())


if __name__=="__main__":unittest.main()
