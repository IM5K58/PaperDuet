import json
import os

import pymupdf
from fastapi.testclient import TestClient

from paperduet.app import create_app
from paperduet.models import ReadingPosition
from test_m1 import MemoryVault


def shelf_api(tmp_path):
    app=create_app('s'*64,tmp_path,tmp_path/'no-sample.json',vault=MemoryVault())
    store=app.state.store
    with store.connect() as db:
        db.execute("INSERT INTO documents(id,title,page_count,status) VALUES('shelf','Shelf paper',12,'complete')")
        # Non-contiguous sort orders must use the block's rank, not the raw order value.
        for index,order in enumerate([0,10,30,90]):
            payload=json.dumps({'id':f'b{index}','doc_id':'shelf','order':order,'type':'p','section_path':[],
                                'page':index*3+1,'en':'Paper text'})
            db.execute("INSERT INTO blocks(id,doc_id,sort_order,type,section_path,payload) VALUES(?,'shelf',?,'p','[]',?)",
                       (f'b{index}',order,payload))
    return app,store


def test_library_reading_position_uses_block_rank_and_page(tmp_path):
    app,store=shelf_api(tmp_path)
    with TestClient(app,headers={'Authorization':'Bearer '+'s'*64}) as client:
        item=client.get('/documents').json()[0]
        assert item['last_page'] is None and item['read_percent']==0
        assert not item['has_thumbnail'] and item['block_count']==4
        store.position('shelf',ReadingPosition(block_id='b1'))
        item=client.get('/documents').json()[0]
        assert item['last_page']==4 and item['read_percent']==50
        store.position('shelf',ReadingPosition(block_id='b3'))
        assert client.get('/documents').json()[0]['read_percent']==100
        with store.connect() as db:
            db.execute("UPDATE documents SET last_block_id='removed' WHERE id='shelf'")
        assert client.get('/documents').json()[0]['read_percent']==0


def test_lightweight_library_avoids_reading_stats(tmp_path):
    app,store=shelf_api(tmp_path)
    with TestClient(app,headers={'Authorization':'Bearer '+'s'*64}) as client:
        with store.connect() as db:
            db.execute("INSERT INTO jobs(id,doc_id,stage,status,progress) VALUES('old-job','shelf','Extract','running',.2)")
            db.execute("INSERT INTO jobs(id,doc_id,stage,status,progress) VALUES('latest-job','shelf','Translate','running',.86)")
        items=client.get('/documents?stats=false').json()
        assert len(items)==1
        item=items[0]
        assert item['id']=='shelf' and item['status']=='complete'
        assert item['job_status']=='running' and item['progress']==.86 and item['stage']=='Translate'
        assert 'block_count' not in item and 'read_percent' not in item
        assert client.get('/documents').json()[0]['block_count']==4


def test_thumbnail_is_small_authenticated_cached_and_invalidated(tmp_path):
    app,store=shelf_api(tmp_path)
    directory=tmp_path/'documents/shelf'
    directory.mkdir(parents=True)
    source=directory/'source.pdf'
    def pdf(text):
        with pymupdf.open() as document:
            document.new_page(width=600,height=800).insert_text((40,60),text)
            document.save(source)
    pdf('First cover')
    with store.connect() as db:
        db.execute("UPDATE documents SET source_path='documents/shelf/source.pdf' WHERE id='shelf'")
    with TestClient(app,headers={'Authorization':'Bearer '+'s'*64}) as client:
        assert client.get('/documents').json()[0]['has_thumbnail']
        response=client.get('/documents/shelf/thumbnail')
        assert response.status_code==200 and response.headers['content-type']=='image/png'
        image=pymupdf.Pixmap(response.content)
        assert (image.width,image.height)==(300,400)
        cached=directory/'thumbnail.png'
        modified=cached.stat().st_mtime_ns
        assert client.get('/documents/shelf/thumbnail').content==response.content
        assert cached.stat().st_mtime_ns==modified
        pdf('Changed cover')
        os.utime(source,ns=(modified+1_000_000,modified+1_000_000))
        assert client.get('/documents/shelf/thumbnail').content!=response.content
        assert client.get('/documents/shelf/thumbnail',headers={'Authorization':''}).status_code==401
        assert client.get('/documents/missing/thumbnail').status_code==404
        with store.connect() as db:
            db.execute("UPDATE documents SET source_path=NULL WHERE id='shelf'")
        assert client.get('/documents/shelf/thumbnail').status_code==404
        with store.connect() as db:
            db.execute("UPDATE documents SET source_path='documents/other/source.pdf' WHERE id='shelf'")
        assert client.get('/documents/shelf/thumbnail').status_code==404

