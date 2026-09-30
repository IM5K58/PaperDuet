"""Only Playwright imports this deterministic Ask transport. Not bundled."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from paperduet.app import create_app
from m2_sample import FIXTURE, seed
from test_m3 import ChatDouble, Vault

async def main():
    directory=Path(os.environ.pop('PAPERDUET_DATA_DIR'));token=os.environ.pop('PAPERDUET_SESSION_TOKEN')
    seed(directory)
    app=create_app(token,directory,FIXTURE,'http://127.0.0.1:1420',vault=Vault(),provider=ChatDouble())
    if os.environ.pop('PAPERDUET_TEST_ARXIV',''):
        import httpx,pymupdf
        from test_m4 import HTML
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40,50),'Synthetic original paper');pdfbytes=pdf.tobytes()
        store=app.state.store
        png=store.asset_path('m2-paper',store.document('m2-paper').blocks[5].image_path).read_bytes()
        app.state.arxiv.transport=httpx.MockTransport(lambda r:httpx.Response(200,content=png if r.url.path.endswith('.png') else HTML.encode() if '/html/' in r.url.path else pdfbytes))
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    class Server(uvicorn.Server):
        async def startup(self,sockets=None):
            await super().startup(sockets=sockets)
            if self.started:print(json.dumps({'port':listener.getsockname()[1]}),flush=True)
    await Server(uvicorn.Config(app,log_config=None,access_log=False,log_level='critical',loop='asyncio',http='h11')).serve(sockets=[listener])

if __name__=='__main__':asyncio.run(main())
