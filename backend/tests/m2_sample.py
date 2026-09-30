"""Test-only scientific content and AI double. Never shipped in the sidecar."""
import json
from pathlib import Path

import pymupdf

from paperduet.adapter import parse_table
from paperduet.models import Block, PipelineOptions
from paperduet.provider import ProviderError
from paperduet.store import Store

FIXTURE=Path(__file__).resolve().parents[2]/'fixtures/rex-omni.blocks.json'


class SampleProvider:
    def __init__(self,bad=False):
        self.calls=[];self.bad=bad;self.fail_after=None;self.malformed=False;self.invalid_json=False
    def connected(self):return True
    async def health_check(self):return {'status':'ok','models':[]}
    async def json(self,stage,model,payload,image=None):
        self.calls.append((stage,payload))
        assert stage=='annotate', 'M1 content should remain cached'
        if self.fail_after and len(self.calls)>=self.fail_after:raise ProviderError('AI_RATE_LIMIT')
        if self.invalid_json:raise ProviderError('AI_INVALID_JSON',usage={'tokens_in':20,'tokens_out':10})
        if self.malformed:return {'notes':[{'invalid':'schema'}]},{}
        if 'repair_note' in payload:
            item=payload['repair_note'].copy();item['body_md']='근거에서 보고한 결과는 42.0이다.'
            return {'notes':[item]},{'tokens_in':10,'tokens_out':5}
        ids={b['id'] for b in payload['target_blocks']};notes=[]
        if 'b0001' in ids:
            for kind,title,text,claim in [('key','핵심 모델','Nova 모델을 제안한다.','stated'),('res','실험 결과',f'결과는 {"99999" if self.bad else "42.0"}이다.','stated'),('lim','해석의 한계','이 결과만으로 일반화를 보장하지는 않는다.','interpretation'),('ins','맥락','모델과 평가 결과를 함께 읽어야 한다.','mixed'),('trm','Nova','이 논문이 제안한 모델 이름이다.','stated')]:
                notes.append(dict(after_block_id='b0001',kind=kind,title=title,body_md=text,claim=claim,refs=['b0001']))
        if 'b0004' in ids:
            notes.append(dict(after_block_id='b0004',kind='mth',title='식의 의미',body_md='**식이 하는 일**\n결과를 변수에 대응한다.\n\n**기호별 의미**\nx는 결과를 나타낸다.\n\n**식이 의미하는 바**\n결과는 42.0이다.',claim='mixed',refs=['b0004']))
        return {'notes':notes},{'tokens_in':10,'tokens_out':5}


def seed(directory):
    store=Store(directory,FIXTURE);doc_id='m2-paper'
    if store.document(doc_id):return store,doc_id
    blocks=[
        Block(id='b0000',doc_id=doc_id,order=0,type='sec',n='Abstract',section_path=['Abstract'],en='Abstract',ko='초록'),
        Block(id='b0001',doc_id=doc_id,order=1,type='p',section_path=['Abstract'],en='We propose Nova and report 42.0. See Table 1.',ko='우리는 Nova를 제안하고 42.0을 보고한다. Table 1을 참조한다.'),
        Block(id='b0002',doc_id=doc_id,order=2,type='sec',n='1',section_path=['1'],en='Results',ko='결과'),
        Block(id='b0003',doc_id=doc_id,order=3,type='tab',n='Table 1',section_path=['1'],caption_en='Results for Nova.',caption_ko='Nova의 실험 결과이다.',table=parse_table('<table><thead><tr><th rowspan="2">Model</th><th colspan="2">Score</th></tr><tr><th>AP</th><th>Recall</th></tr></thead><tbody><tr><td>Baseline</td><td>41.0</td><td>80.0</td></tr><tr><td>Nova</td><td>42.0</td><td>80.0</td></tr></tbody></table>')),
        Block(id='b0004',doc_id=doc_id,order=4,type='eq',n='(1)',section_path=['1'],en='x=42.0',latex='x=42.0'),
        Block(id='b0005',doc_id=doc_id,order=5,type='fig',n='Figure 1',section_path=['1'],caption_en='Nova detection diagram.',caption_ko='Nova의 검출 도식이다.',image_path=f'documents/{doc_id}/crops/figure.png'),
        Block(id='b0006',doc_id=doc_id,order=6,type='sec',n='References',section_path=['References'],en='References'),
        Block(id='b0007',doc_id=doc_id,order=7,type='p',section_path=['References'],en='[1] Reference 2025.'),
    ]
    for row in blocks[3].table.header:
        for c in row:c.text_ko={'Model':'모델','Score':'점수','AP':'AP','Recall':'재현율'}[c.text_en]
    directory=directory/'documents'/doc_id;directory.mkdir(parents=True,exist_ok=True)
    (directory/'metadata.json').write_text(json.dumps({'page_count':1}),encoding='utf-8')
    crops=directory/'crops';crops.mkdir()
    pdf=pymupdf.open();page=pdf.new_page(width=640,height=300)
    page.insert_text((35,40),'M2 TEST IMAGE - Nova detection',fontsize=18)
    for x,y,w,h in [(40,70,160,140),(220,90,170,120),(410,60,175,175)]:
        page.draw_rect(pymupdf.Rect(x,y,x+w,y+h),color=(.1,.4,.7),fill=(.88,.94,1),width=3)
    page.get_pixmap().save(crops/'figure.png');pdf.close()
    options=PipelineOptions().model_dump();options.pop('annotate_model')  # Real M1 checkpoint shape.
    checkpoint={'ai_approved':True,'options':options,'completed_stages':['Ingest','Extract','Structure','Restore','Glossary','Translate','Validate','Render'],
                'translated':[b.id for b in blocks if b.type!='eq' and b.id not in {'b0006','b0007'}],'restored':['b0003','b0004'],'extracted_pages':1}
    with store.connect() as db:
        db.execute('INSERT INTO documents(id,title,status,page_count) VALUES(?,?,?,?)',(doc_id,'M2 Test Paper','complete',1))
        db.execute('INSERT INTO jobs(id,doc_id,stage,status,progress,checkpoint) VALUES(?,?,?,?,?,?)',(doc_id,doc_id,'Render','complete',1,json.dumps(checkpoint)))
        db.execute('INSERT INTO glossary VALUES(?,?,?,?,?)',(doc_id,'Nova','Nova',1,'이 논문이 제안한 모델'))
    store.save_blocks(doc_id,blocks,[{'references':b.id in {'b0006','b0007'},'bbox':[0,0,640,300]} for b in blocks])
    return store,doc_id
