"""Offline OCR protection, bounds and real bundled-model recognition."""
import hashlib
import math
from pathlib import Path

import pymupdf as fitz
import pytest

from paperduet.ocr_engine import MAX_PIXELS,ocr_availability,recognize_page


def scan(tmp_path,*,rotation=0,cropped=False,korean=False):
    with fitz.open() as sample:
        page=sample.new_page(width=400,height=300)
        page.insert_text((30,80),'PAPERDUET OCR TEST',fontsize=18)
        page.insert_text((30,120),'Original result 42',fontsize=17)
        if korean:page.insert_text((30,160),'논문 문자인식 결과 42',fontname='korea',fontsize=18)
        png=page.get_pixmap(matrix=fitz.Matrix(3,3),alpha=False).tobytes('png')
    path=tmp_path/'scan.pdf'
    with fitz.open() as pdf:
        width,height=(300,400) if rotation in {90,270} else (400,300)
        x,y=(100,200) if cropped else (0,0)
        page=pdf.new_page(width=width+2*x,height=height+2*y)
        target=fitz.Rect(x,y,x+width,y+height)
        # Image rotation is counterclockwise; page rotation is clockwise.
        # Matching values keep the rendered scan upright for recognition.
        page.insert_image(target,stream=png,rotate=rotation,keep_proportion=False)
        if cropped:page.set_cropbox(target)
        page.set_rotation(rotation)
        pdf.save(path)
    return path


def models(tmp_path,eng=True,kor=True):
    directory=tmp_path/'tessdata';directory.mkdir()
    for language,present in [('eng',eng),('kor',kor)]:
        if present:(directory/(language+'.traineddata')).write_bytes(b'x'*2048)
    return directory


def test_availability_lists_only_explicit_language_packs_and_never_uses_environment(tmp_path,monkeypatch):
    directory=models(tmp_path,eng=True,kor=False)
    monkeypatch.setenv('TESSDATA_PREFIX','C:/unrelated/system/tessdata')
    assert ocr_availability(tessdata_dir=directory)=={'available':True,'languages':['eng'],'reason':None}
    (directory/'eng.traineddata').unlink()
    assert ocr_availability(tessdata_dir=directory)=={'available':False,'languages':[],'reason':'OCR_UNAVAILABLE'}


def test_bundled_english_and_korean_models_are_selectable_without_a_system_executable():
    assert ocr_availability()=={'available':True,'languages':['eng','kor','eng+kor'],'reason':None}


def test_existing_native_text_is_untouched_without_initializing_ocr(tmp_path,monkeypatch):
    path=tmp_path/'native.pdf'
    with fitz.open() as pdf:
        pdf.new_page().insert_text((30,80),'Native text 42 remains original.')
        pdf.save(path)
    before=path.read_bytes()
    def forbidden(*args,**kwargs):raise AssertionError('Native text must not be OCRed')
    monkeypatch.setattr(fitz.Page,'get_textpage_ocr',forbidden)
    result=recognize_page(path,0,tessdata_dir=tmp_path/'missing-models')
    assert result['has_native_text'] and not result['items']
    assert path.read_bytes()==before


@pytest.mark.parametrize('language',['../eng','eng+fra','',None])
def test_unsupported_language_cannot_select_files_or_run_native_ocr(tmp_path,language):
    with pytest.raises(ValueError,match='^OCR_LANGUAGE_UNAVAILABLE$'):
        recognize_page(tmp_path/'absent.pdf',0,language=language)


def test_missing_and_missing_selected_language_have_friendly_errors(tmp_path):
    path=scan(tmp_path)
    with pytest.raises(ValueError,match='^OCR_UNAVAILABLE$'):
        recognize_page(path,0,tessdata_dir=tmp_path/'absent')
    directory=models(tmp_path,eng=True,kor=False)
    with pytest.raises(ValueError,match='^OCR_LANGUAGE_UNAVAILABLE$'):
        recognize_page(path,0,language='eng+kor',tessdata_dir=directory)


@pytest.mark.parametrize('index',[-1,True,1,1.5])
def test_invalid_or_out_of_range_page_is_rejected(tmp_path,index):
    path=scan(tmp_path)
    with pytest.raises(ValueError,match='^OCR_PAGE_INVALID$'):recognize_page(path,index)


@pytest.mark.parametrize('dpi',[0,71,601,300.5,True])
def test_invalid_resolution_is_rejected_before_rendering(tmp_path,dpi):
    path=scan(tmp_path)
    with pytest.raises(ValueError,match='^OCR_PAGE_INVALID$'):recognize_page(path,0,dpi=dpi)


def fake_empty_ocr(monkeypatch,calls):
    token=object();get_text=fitz.Page.get_text
    def ocr(page,**kwargs):calls.append(kwargs);return token
    def text(page,option='text',*args,**kwargs):
        if kwargs.get('textpage') is token:return {'blocks':[]}
        return get_text(page,option,*args,**kwargs)
    monkeypatch.setattr(fitz.Page,'get_textpage_ocr',ocr)
    monkeypatch.setattr(fitz.Page,'get_text',text)


def test_large_page_reduces_actual_dpi_before_any_native_raster_allocation(tmp_path,monkeypatch):
    path=tmp_path/'large.pdf'
    with fitz.open() as pdf:pdf.new_page(width=2000,height=2000);pdf.save(path)
    calls=[];fake_empty_ocr(monkeypatch,calls)
    result=recognize_page(path,0)
    assert 72<=result['dpi']<300
    assert math.ceil(2000*result['dpi']/72)**2<=MAX_PIXELS
    assert calls[0]['dpi']==result['dpi'] and calls[0]['full'] is True
    assert calls[0]['tessdata'] and calls[0]['language']=='eng'
    assert not result['items'] and not result['has_native_text']


def test_page_that_cannot_fit_the_pixel_budget_at_minimum_dpi_is_rejected(tmp_path,monkeypatch):
    path=tmp_path/'oversized.pdf'
    with fitz.open() as pdf:pdf.new_page(width=10000,height=10000);pdf.save(path)
    calls=[];fake_empty_ocr(monkeypatch,calls)
    with pytest.raises(ValueError,match='^OCR_PAGE_TOO_LARGE$'):recognize_page(path,0)
    assert not calls


def test_native_ocr_failure_is_friendly_and_leaves_original_bytes_unchanged(tmp_path,monkeypatch):
    path=scan(tmp_path);before=path.read_bytes()
    def fail(*args,**kwargs):raise RuntimeError('Native OCR failed with internal detail')
    monkeypatch.setattr(fitz.Page,'get_textpage_ocr',fail)
    with pytest.raises(ValueError,match='^OCR_FAILED$'):recognize_page(path,0)
    assert path.read_bytes()==before


def test_actual_english_ocr_recovers_source_text_and_numbers_without_creating_assets(tmp_path):
    path=scan(tmp_path);before=hashlib.sha256(path.read_bytes()).hexdigest()
    result=recognize_page(path,0)
    text=' '.join(item['en'] for item in result['items'])
    assert 'PAPERDUET OCR TEST' in text and 'Original result 42' in text
    assert result['engine']=='tesseract' and result['language']=='eng' and result['dpi']==300
    assert not result['has_native_text']
    assert all(item['type'] in {'p','li'} and item['page']==1 for item in result['items'])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert list(tmp_path.iterdir())==[path]


@pytest.mark.parametrize('language',['kor','eng+kor'])
def test_actual_bundled_korean_and_combined_language_ocr(tmp_path,language):
    path=scan(tmp_path,korean=True)
    result=recognize_page(path,0,language=language)
    text=' '.join(item['en'] for item in result['items'])
    assert '42' in text and '논문' in text and '결과' in text
    assert result['language']==language and result['items']


def test_actual_sparse_two_column_scan_reads_the_left_column_before_the_right(tmp_path):
    with fitz.open() as source:
        page=source.new_page(width=600,height=500)
        for x,prefix in [(30,'LEFT'),(330,'RIGHT')]:
            page.insert_text((x,100),prefix+' FIRST RESULT 11',fontsize=14)
            page.insert_text((x,180),prefix+' SECOND RESULT 12',fontsize=14)
        png=page.get_pixmap(matrix=fitz.Matrix(3,3),alpha=False).tobytes('png')
    path=tmp_path/'columns.pdf'
    with fitz.open() as pdf:
        page=pdf.new_page(width=600,height=500);page.insert_image(page.rect,stream=png);pdf.save(path)
    result=recognize_page(path,0)
    text=' '.join(item['en'] for item in result['items'])
    needles=['LEFT FIRST RESULT 11','LEFT SECOND RESULT 12','RIGHT FIRST RESULT 11','RIGHT SECOND RESULT 12']
    positions=[text.index(needle) for needle in needles]
    assert positions==sorted(positions)
    assert all(text.count(needle)==1 for needle in needles)


@pytest.mark.parametrize('rotation',[90,270])
@pytest.mark.parametrize('cropped',[False,True])
def test_actual_ocr_native_bounds_map_to_the_rendered_scan_for_rotation_and_cropbox(tmp_path,rotation,cropped):
    path=scan(tmp_path,rotation=rotation,cropped=cropped)
    result=recognize_page(path,0)
    assert 'PAPERDUET OCR TEST' in ' '.join(item['en'] for item in result['items'])
    with fitz.open(path) as pdf:
        page=pdf[0];pixels=page.get_pixmap(alpha=False)
        assert result['page_size']=={'width':float(pixels.width),'height':float(pixels.height)}
        projected=[]
        for item in result['items']:
            rect=fitz.Rect(item['bbox'])*page.rotation_matrix
            assert all(math.isfinite(v) for v in item['bbox'])
            assert page.rect.contains(rect)
            projected.append(rect)
        recognized=fitz.Rect(projected[0])
        for rect in projected[1:]:recognized |= rect
        assert 20<=recognized.x0<=45 and 45<=recognized.y0<=75
        assert 110<=recognized.y1<=135
        # A result covering the wrong page corner would contain no source ink.
        clip=page.get_pixmap(clip=recognized,alpha=False)
        assert min(clip.samples)<50
