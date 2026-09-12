import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from a2a.guard import native_guard


@unittest.skipUnless(sys.platform=='darwin','requires native macOS sandbox')
class GuardTests(unittest.TestCase):
    def test_protected_sources_readable_but_not_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);protected=root/'protected';protected.mkdir();outside=root/'runtime';outside.mkdir()
            source=protected/'sentinel';source.write_text('unchanged')
            code='from pathlib import Path;import sys; p=Path(sys.argv[1]);assert p.read_text()=="unchanged";\ntry:\n p.write_text("BAD")\nexcept PermissionError: pass\nelse: raise SystemExit(2)\nPath(sys.argv[2]).write_text("allowed")'
            result=subprocess.run(native_guard([sys.executable,'-c',code,str(source),str(outside/'result')],[protected]),capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr.decode())
            self.assertEqual(source.read_text(),'unchanged')
            self.assertEqual((outside/'result').read_text(),'allowed')
