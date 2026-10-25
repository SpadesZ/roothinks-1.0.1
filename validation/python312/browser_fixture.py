"""只供隔離 Docker 瀏覽器站：測試站綁容器介面，host publish 必須限定 127.0.0.1。

不修改既有 fixture 或 assertions；資料仍由 fixture 在 TMPDIR 新建。
"""
import runpy
import sys
from flask_socketio import SocketIO

original_run=SocketIO.run


def container_run(self,app,**kwargs):
    kwargs['host']='0.0.0.0'
    return original_run(self,app,**kwargs)


SocketIO.run=container_run
sys.argv=['/app/test/integration_e2e/sai_browser_fixture.py','--autonomy']
runpy.run_path(sys.argv[0],run_name='__main__')
