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
from paperduet.provider import ProviderError

class E2EDouble(ChatDouble):
    """Ask streaming plus the two paper-summary stages for the reader's summary tab."""
    async def json(self,stage,model,payload,image=None):
        await asyncio.sleep(.2)
        if stage=='summary_section':
            ids=[b['id'] for b in payload['blocks']]
            return {'role':'핵심 결과','summary':'섹션 요약','claims':[{'text':'Nova는 42.0을 보고한다.','refs':[i for i in ids if i in {'b0001','b0003'}] or ids[:1]}],'visuals':[]},{'tokens_in':10,'tokens_out':5}
        if stage!='summary_paper':raise ProviderError('AI_REQUEST_FAILED')
        return {'structured':{'problem':[{'text':'Nova가 풀려는 검출 문제를 다룬다.','refs':['b0001']}],'gap':[],'method':[],
                    'results':[{'text':'Nova는 Table 1에서 AP 42.0을 기록했다.','refs':['b0003']},{'text':'Nova는 99.9를 기록했다.','refs':['b0003']}],'limits':[]},
                'flow':[{'section':s['section'],'role':s['role'],'summary':'이 섹션의 한 줄 요약','why_next':'다음 섹션으로 이어진다','block_id':s['first_block_id']} for s in payload['sections']],
                'visuals':[{'block_id':'b0003','why':'핵심 수치를 담은 표'}]},{'tokens_in':20,'tokens_out':9}

async def main():
    directory=Path(os.environ.pop('PAPERDUET_DATA_DIR'));token=os.environ.pop('PAPERDUET_SESSION_TOKEN')
    seed(directory)
    app=create_app(token,directory,FIXTURE,'http://127.0.0.1:1420',vault=Vault(),provider=E2EDouble())
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
