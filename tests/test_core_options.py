import ctypes as C
from pathlib import Path
import tempfile
import unittest

from redux.libretro_host import EmbeddedSession,_Variable,_LogCallback


class CoreOptionTests(unittest.TestCase):
    def session(self,options):
        root=Path(tempfile.gettempdir())/"redux-no-native-option-fixture"
        return EmbeddedSession(root/"core.dll",root/"game.nes",root/"system",root/"saves",audio=False,core_options=options)

    def test_declared_override_reaches_get_variable_and_other_defaults_survive(self):
        host=self.session({"choice":"second"})
        rows=(_Variable*3)(_Variable(b"choice",b"Choice; first|second"),_Variable(b"other",b"Other; yes|no"),_Variable())
        self.assertTrue(host._environment(16,C.addressof(rows)))
        query=_Variable(b"choice",None)
        self.assertTrue(host._environment(15,C.addressof(query)))
        self.assertEqual(query.value,b"second")
        self.assertEqual(host._variables[b"other"],b"yes")
        self.assertEqual(host.declared_options["choice"],("first","second"))

    def test_unoffered_value_is_reported_instead_of_silently_ignored(self):
        host=self.session({"choice":"invented"})
        rows=(_Variable*2)(_Variable(b"choice",b"Choice; first|second"),_Variable())
        self.assertFalse(host._environment(16,C.addressof(rows)))
        self.assertIn("Unsupported core option",host._callback_error)

    def test_malformed_requests_are_rejected_before_native_work(self):
        for options in (["choice"],{"choice":1},{"bad\0key":"yes"},{"a":"b\0"}):
            with self.subTest(options=options),self.assertRaises(ValueError):self.session(options)

    def test_native_log_callback_keeps_bounded_literal_format_text(self):
        host=self.session({});callback=_LogCallback()
        self.assertTrue(host._environment(27,C.addressof(callback)))
        callback.log(1,b"value %s %d")
        self.assertIn("value %s %d",host.messages[-1])
        for _ in range(110):callback.log(2,b"x"*3000)
        self.assertEqual(len(host.messages),100)
        self.assertLessEqual(len(host.messages[-1]),2065)


if __name__=="__main__":unittest.main()
