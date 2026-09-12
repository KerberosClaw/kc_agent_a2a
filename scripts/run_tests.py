#!/usr/bin/env python3
"""Independent discovery roots; offline tests never require live model credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
runner='''import contextlib,io,json,sys,unittest
suite=unittest.TestLoader().discover(sys.argv[1])
with contextlib.redirect_stdout(io.StringIO()):
 result=unittest.TextTestRunner(verbosity=0).run(suite)
print(json.dumps({'suite':sys.argv[1],'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped)}))
sys.exit(not result.wasSuccessful())
'''
result=0
for folder in ['tests/a2a','tests/discord_party','tests/native','tests/public']:
 env=dict(os.environ,PYTHONPATH=str(root/'src'),PYTHONDONTWRITEBYTECODE='1')
 code=subprocess.run([sys.executable,'-c',runner,folder],cwd=root,env=env).returncode
 result=result or code
sys.exit(result)
