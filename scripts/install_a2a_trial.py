"""Install one guarded manual launcher from an explicit local configuration."""
import argparse
import json
import os
from pathlib import Path
import sys


def install(config_path, launcher):
    if sys.platform != 'darwin' or sys.version_info < (3, 12):
        raise RuntimeError('macOS and Python 3.12+ required')
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo/'src'))
    from a2a.trial import configuration
    from a2a.storage import atomic_write
    config_path = Path(config_path).resolve(strict=True)
    data = configuration(config_path)
    launcher = Path(launcher).expanduser().absolute()
    if launcher.is_symlink() or launcher.exists():
        raise RuntimeError('launcher already exists; preserve it and use the documented upgrade procedure')
    source = '#!' + sys.executable + '\n' + '''import json,os,sys
from pathlib import Path
os.umask(0o077)
'''+f'sys.path.insert(0,{str(repo/"src")!r})\n'+'''from a2a.guard import native_guard
'''+f'config_path=Path({str(config_path)!r})\n'+'''config=json.loads(config_path.read_text())
protected=[v['root'] for v in config['personas'].values()]
protected += [v['path'] for v in config.get('material_files', [])]
if config.get('life_wiki'):protected.append(config['life_wiki'])
env=dict(os.environ)
'''+f'env["PYTHONPATH"]={str(repo/"src")!r}\n'+'''env['A2A_GUARDED']='1'
env['PYTHONDONTWRITEBYTECODE']='1'
command=native_guard([sys.executable,'-m','a2a.trial','--config',str(config_path),*sys.argv[1:]],protected)
os.execvpe(command[0],command,env)
'''
    atomic_write(launcher, source);launcher.chmod(0o700)
    return {'launcher': str(launcher), 'runtime': str(data['runtime']), 'services_started': False}


if __name__ == '__main__':
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, required=True)
    p.add_argument('--launcher', type=Path, default=Path.home()/'.local/bin/agent-a2a')
    args=p.parse_args()
    try:print(json.dumps(install(args.config,args.launcher)))
    except Exception as error:print(type(error).__name__+': check local configuration',file=sys.stderr);sys.exit(1)
