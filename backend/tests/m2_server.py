"""Playwright-only server: real app/SQLite/pipeline, deterministic AI transport."""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from paperduet.app import create_app
from paperduet.models import PipelineOptions
from paperduet.pipeline import Pipeline
from m2_sample import FIXTURE, SampleProvider, seed


class TestVault:
    def get(self):return None
    def set(self,value):raise AssertionError('Test server must not accept real keys')
    def delete(self):pass


async def main():
    directory=Path(os.environ.pop('PAPERDUET_DATA_DIR'))
    token=os.environ.pop('PAPERDUET_SESSION_TOKEN')
    store,doc_id=seed(directory);provider=SampleProvider(bad=True)
    pipeline=Pipeline(store,provider)
    pipeline.approve(doc_id,PipelineOptions());await pipeline.tasks[doc_id]
    app=create_app(token,directory,FIXTURE,'http://127.0.0.1:1420',vault=TestVault(),provider=provider)
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(128)
    port=listener.getsockname()[1]
    class Server(uvicorn.Server):
        async def startup(self,sockets=None):
            await super().startup(sockets=sockets)
            if self.started:print(json.dumps({'port':port}),flush=True)
    await Server(uvicorn.Config(app,log_config=None,access_log=False,log_level='critical',loop='asyncio',http='h11')).serve(sockets=[listener])


if __name__=='__main__':asyncio.run(main())
