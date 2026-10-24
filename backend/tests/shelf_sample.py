"""Deterministic local bookshelf for browser regression tests and screenshots."""
import json
from pathlib import Path
import sys

import pymupdf

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperduet.store import Store


def seed(directory):
    store=Store(Path(directory),Path(directory)/'absent.json')
    titles=[
        'VisualAD: Language-Free Zero-Shot Anomaly Detection via Vision Transformer',
        'Multiplying matrices via polynomials: exposition and barriers',
        'Attention Is All You Need',
        'Understanding LSTM – a tutorial into Long Short-Term Memory Recurrent Neural Networks',
        'Segment Anything',
        'Denoising Diffusion Probabilistic Models',
        'Deep Residual Learning for Image Recognition',
        'BERT: Pre-training of Deep Bidirectional Transformers',
        'Learning Transferable Visual Models From Natural Language Supervision',
        'An Image is Worth 16x16 Words',
        'Universal Background Models for Speaker Verification',
        'Language Models are Few-Shot Learners',
        'Neural Radiance Fields',
        'Masked Autoencoders Are Scalable Vision Learners',
        'Detect Anything via Next Point Prediction',
        'Scaling Laws for Neural Language Models',
        'Proximal Policy Optimization Algorithms',
        'Generative Adversarial Networks',
        'LoRA: Low-Rank Adaptation of Large Language Models',
        'Chain-of-Thought Prompting Elicits Reasoning',
    ]
    with store.connect() as db:
        db.execute('DELETE FROM documents')
        for i,title in enumerate(titles):
            doc_id=f'shelf-{i:02}'
            folder=Path(directory)/'documents'/doc_id
            folder.mkdir(parents=True,exist_ok=True)
            with pymupdf.open() as pdf:
                page=pdf.new_page(width=595,height=794)
                page.insert_textbox((50,50,545,160),title,fontname='tibo',fontsize=24)
                page.insert_text((260,185),'Abstract',fontname='tibo',fontsize=12)
                text=('This locally generated paper is a bookshelf UI test fixture. '
                      'We study a simple approach and evaluate its results across tasks. ')*10
                for left,right in [(45,285),(310,550)]:
                    page.insert_textbox((left,215,right,750),text,fontname='tiro',fontsize=9)
                for _ in range(10):pdf.new_page(width=595,height=794)
                pdf.save(folder/'source.pdf')
            status='running' if i in (1,10) else 'failed' if i==4 else 'complete'
            read=[42,0,88,5,0,64,100,23,0,71,0,9,0,55,12,0,100,38,0,17][i]
            db.execute("""INSERT INTO documents(id,title,page_count,status,source_path,last_block_id,opened_at,created_at)
                VALUES(?,?,11,?,?,?,?,?)""",(doc_id,title,status,f'documents/{doc_id}/source.pdf',
                f'b{read-1:03}' if read else None,'2026-10-22 00:00:00' if i==0 else None,f'2026-10-21 00:00:{59-i:02}'))
            for n in range(100):
                payload={'id':f'b{n:03}','doc_id':doc_id,'order':n,'type':'p','section_path':[],
                         'page':8 if i==0 and n==41 else n//10+1,'en':f'{title}. Reading block {n+1}.'}
                db.execute("INSERT INTO blocks(id,doc_id,sort_order,type,section_path,payload) VALUES(?,?,?,'p','[]',?)",
                           (payload['id'],doc_id,n,json.dumps(payload)))
            db.execute("INSERT INTO jobs(id,doc_id,stage,status,progress,checkpoint) VALUES(?,?,'Translate',?,?,?)",
                       (f'job-{i}',doc_id,status,.86 if i==1 else .34 if i==10 else .2 if i==4 else 1,
                        json.dumps({'completed_stages':['Ingest','Extract','Structure']})))


if __name__=='__main__':seed(sys.argv[1])
