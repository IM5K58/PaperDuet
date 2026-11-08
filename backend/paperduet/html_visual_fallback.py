"""Supplement selected HTML visuals from the saved PDF, without replacing prose.

The operation is all-or-nothing: an ambiguous/missing label, inconsistent
caption, incomplete crop, or failed write returns an empty set without changing
the supplied blocks/provenance. Only successfully resolved PNGs are published.
"""
import math
import re
import tempfile
import unicodedata
import uuid
from pathlib import Path

import pymupdf as fitz
from bs4 import BeautifulSoup,NavigableString

from .pdf_parser import PDF_LOCK,cluster_graphics,lines_of,save_crop,visible_drawings


_LABEL=re.compile(r'^(Figure|Fig\.?|Table)\s*([A-Za-z]?\s*\d+(?:\.\d+)*[a-z]?)(?=\s|[:.]|$)\s*[:.]?\s*',re.I)
_NUMBER=re.compile(r'[A-Za-z]?\d+(?:\.\d+)*[a-z]?')
_SUBCAPTION=re.compile(r'^(?:\([a-z]\)|[a-z]\))(?=\s|$)')


def _normal(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFC',value or '')).strip()


def _caption(value):
    soup=BeautifulSoup(value or '', 'html.parser')
    # A lost exponent/index is not an equivalent caption. Cosmetic inline
    # styles may differ, but scientific script structure must not disappear.
    for node in soup.find_all(['sup','sub']):
        node.replace_with(NavigableString(('^' if node.name=='sup' else '_')+'{'+node.get_text()+'}'))
    return _normal(soup.get_text())


def _label(kind,value):
    value=_caption(value)
    if kind=='eq':
        number=value.strip('() ')
        return (kind,number.casefold()) if _NUMBER.fullmatch(number) else None
    match=_LABEL.match(value)
    if match:
        found='tab' if match[1].lower()=='table' else 'fig'
        number=re.sub(r'\s+','',match[2]).casefold()
        return (found,number) if found==kind else None
    number=re.sub(r'\s+','',value).rstrip(': .')
    return (kind,number.casefold()) if _NUMBER.fullmatch(number) else None


def _identity(block,source):
    if block.type=='eq':
        label=_label('eq',block.n)
        expression=_caption(block.latex or block.en)
        expression=re.sub(r'\\[\(\)\[\]]','',expression).strip('$ ')
        if label:expression=re.sub(r'\('+re.escape(label[1])+r'\)\s*$','',expression,flags=re.I)
        expression=re.sub(r'\s+','',expression)
        return (label,expression) if label and expression else None
    text=_caption(source.get('visual_fallback_caption',block.caption_en))
    embedded=_LABEL.match(text)
    explicit=_label(block.type,block.n) if block.n else None
    if block.n and explicit is None:return None
    embedded_label=_label(block.type,embedded[0]) if embedded else None
    if explicit and embedded_label and explicit!=embedded_label:
        return None
    label=explicit or embedded_label
    if embedded:
        text=text[embedded.end():].strip()
    if label is None:
        # An unlabeled caption can identify a visual only when substantial;
        # "Overview" is not enough evidence to transplant arbitrary artwork.
        if len(text)<40 or len(re.findall(r'\w+',text))<6:return None
    if not text and block.type!='eq':return None
    return label,text


def _rect(value):
    if not isinstance(value,(tuple,list)) or len(value)!=4:return None
    try:numbers=list(map(float,value))
    except (TypeError,ValueError):return None
    if not all(math.isfinite(n) and abs(n)<1e9 for n in numbers):return None
    return fitz.Rect(numbers) if numbers[2]>numbers[0] and numbers[3]>numbers[1] else None


def _fraction(rect,region):
    return (rect&region).get_area()/rect.get_area() if rect.get_area()>0 else 0


def _same_label(line,identity,kind,caption):
    if identity is None:return False
    match=_LABEL.match(line['text'])
    return bool(match and _label(kind,match[0])==identity and caption.startswith(_normal(line['text'][match.end():])))


def _safe_region(pdf,candidate,source,candidates,identity):
    """Reuse a whole visual; do not accept a crop that loses adjacent panels."""
    page_number=candidate.page
    if not page_number or not 1<=page_number<=len(pdf):return None
    page=pdf[page_number-1]
    # The PDF parser currently associates source geometry in unrotated space.
    # Reject rotated-page supplements rather than reusing a potentially wrong
    # crop; the caller can retain the complete PDF import for these documents.
    if page.rotation:return None
    region=_rect(source.get('region'))
    bbox=_rect(source.get('bbox'))
    if region is None or bbox is None or not page.rect.contains(region):return None
    original=fitz.Rect(region)
    lines=lines_of(page)
    other_regions=[_rect(prov.get('region')) for other,prov in candidates
                   if other is not candidate and other.page==page_number and other.n]
    other_regions=[r for r in other_regions if r is not None]
    captions=[line for line in lines if _same_label(line,identity,candidate.type,_caption(candidate.caption_en))]
    if identity and candidate.type!='eq' and len(captions)!=1:return None
    caption=captions[0] if captions else None
    if candidate.type=='fig' and caption:
        cap=fitz.Rect(caption['bbox'])
        below=cap.y1<=region.y0
        for line in lines:
            label=fitz.Rect(line['bbox'])
            if not _SUBCAPTION.match(line['text']) or line['size']>caption['size']+1:continue
            if not original.y0-40<=label.y0 or label.y1>original.y1+40:continue
            if (below and label.y0<=cap.y1) or (not below and label.y1>=cap.y0):continue
            if label.x1<original.x0-20 or label.x0>original.x1+20:
                gap=max(original.x0-label.x1,label.x0-original.x1,0)
                owned_elsewhere=any(label.x1>other.x0 and label.x0<other.x1 and other.y0-40<=label.y0<=other.y1+40 for other in other_regions)
                if gap<=max(72,page.rect.width*.15) and not owned_elsewhere:return None
                continue
            region |= label
            previous=label
            for continuation in sorted(lines,key=lambda item:(item['bbox'][1],item['bbox'][0])):
                box=fitz.Rect(continuation['bbox'])
                if continuation is line or continuation['block']!=line['block'] or box.y0<label.y1:continue
                if _SUBCAPTION.match(continuation['text']) or _LABEL.match(continuation['text']):continue
                if abs(continuation['size']-line['size'])>.75 or abs(box.x0-label.x0)>20:continue
                if box.y0-previous.y1>line['size']*1.75:break
                if box.y1>original.y1+120 or (not below and box.y1>=cap.y0) or (below and box.y0<=cap.y1):break
                if box.x1>original.x1+20:return None
                region |= box
                previous=box
    region &= page.rect
    images=[fitz.Rect(item['bbox'])&page.rect for item in page.get_image_info()]
    images=[r for r in images if r.width>=12 and r.height>=12 and r.get_area()>=300]
    drawings=visible_drawings(page)
    vector=[fitz.Rect(d['rect']) for d in drawings if d['rect'].width>8 and d['rect'].height>8]
    thin=[(fitz.Rect(d['rect'])+(-.5,-.5,.5,.5),len(d['items'])) for d in drawings
          if max(d['rect'].width,d['rect'].height)>8 and min(d['rect'].width,d['rect'].height)<=8]
    vector.extend(rect for rect,count in cluster_graphics(thin) if count>=2 and rect.width>12 and rect.height>12)
    supported=False
    for graphic in images+vector:
        covered=_fraction(graphic,region)
        if covered>=.98:
            supported=True
        elif .05<covered<.98:
            # A raster/vector path that crosses the boundary can contain a
            # missing panel or legend. It must not be silently truncated.
            return None
        elif not any(_fraction(graphic,r)>.98 for r in other_regions):
            vertical=max(0,min(graphic.y1,original.y1)-max(graphic.y0,original.y0))/min(graphic.height,original.height)
            gap=max(original.x0-graphic.x1,graphic.x0-original.x1,0)
            if vertical>=.55 and gap<=max(72,page.rect.width*.15):return None
    # Native table detection is also positive evidence for a text/rule table.
    if candidate.type=='tab':
        for table in page.find_tables().tables:
            covered=_fraction(fitz.Rect(table.bbox),region)
            if .05<covered<.98:return None
        if candidate.table is not None:supported=True
    if candidate.type=='eq' and candidate.en:supported=True
    return list(region) if supported else None


def resolve_pdf_visuals(parser,pdf_path,blocks,sources,block_ids,target_dir,*,image_prefix=None) -> set[str]:
    """Resolve all requested HTML visual IDs, returning those IDs or set().

    `sources` is parallel to `blocks`. Existing HTML captions, labels, tables and
    prose stay intact; absent table/equation source text may be supplemented.
    `target_dir` is caller-owned staging. The default stored image prefix is
    documents/<doc_id>/crops; reanalysis callers provide their preview prefix.
    No Store, AI provider or network access is required.
    """
    requested=set(block_ids)
    if not requested:return set()
    if len(blocks)!=len(sources) or len({b.id for b in blocks})!=len(blocks):return set()
    positions={block.id:index for index,block in enumerate(blocks)}
    if not requested<=positions.keys():return set()
    if any(blocks[positions[bid]].type not in {'fig','tab','eq'} or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',bid) for bid in requested):return set()
    target_dir=Path(target_dir)
    created=[]
    try:
        with tempfile.TemporaryDirectory(prefix='paperduet-html-visual-') as temporary, PDF_LOCK:
            scratch=Path(temporary)
            metadata=parser.metadata(Path(pdf_path))
            pages=[parser.page(Path(pdf_path),index,scratch) for index in range(metadata['page_count'])]
            pdf_blocks,pdf_sources=parser.structure(blocks[0].doc_id,pages,metadata)
            candidates=[(block,source) for block,source in zip(pdf_blocks,pdf_sources)
                        if block.type in {'fig','tab','eq'} and block.image_path]
            selected=[];claimed=set()
            with fitz.open(pdf_path) as pdf:
                for bid in sorted(requested,key=positions.get):
                    index=positions[bid];html_block=blocks[index];html_source=sources[index]
                    identity=_identity(html_block,html_source)
                    if identity is None:return set()
                    label,caption=identity
                    possible=[(block,source) for block,source in candidates if block.type==html_block.type and
                              (_label(block.type,block.n)==label if label is not None else _caption(block.caption_en)==caption)]
                    # Reused labels are ambiguous even when one caption happens
                    # to match: do not resolve across appendix/revision variants.
                    if len(possible)!=1:return set()
                    candidate,source=possible[0]
                    if id(candidate) in claimed:return set()
                    pdf_label=_label(candidate.type,candidate.n)
                    if pdf_label and sum(block.type==candidate.type and _label(block.type,block.n)==pdf_label for block,_ in candidates)>1:return set()
                    found=_identity(candidate,{})
                    if found is None or found[1]!=caption:return set()
                    crop=scratch/Path(candidate.image_path).name
                    if not crop.is_file():return set()
                    region=_safe_region(pdf,candidate,source,candidates,label)
                    if region is None:return set()
                    # Include verified subcaptions outside the parser's original
                    # picture bounds while keeping the main HTML caption separate.
                    save_crop(pdf[candidate.page-1],fitz.Rect(region),crop)
                    data=crop.read_bytes()
                    if not data.startswith(b'\x89PNG\r\n\x1a\n'):return set()
                    selected.append((index,candidate,source,region,data))
                    claimed.add(id(candidate))
            token=uuid.uuid4().hex
            target_dir.mkdir(parents=True,exist_ok=True)
            published=[]
            for index,candidate,source,region,data in selected:
                block=blocks[index]
                name=f'html-pdf-{block.id}-{token}.png'
                output=target_dir/name
                with output.open('xb') as stream:
                    created.append(output)
                    stream.write(data)
                prefix=image_prefix or f'documents/{block.doc_id}/crops'
                published.append((index,candidate,source,region,prefix.rstrip('/')+'/'+name))
            for index,candidate,source,region,image_path in published:
                block=blocks[index]
                block.page=candidate.page;block.image_path=image_path
                if block.type in {'tab','eq'} and not block.en:block.en=candidate.en
                provenance=sources[index]
                provenance.update(origin='arxiv_pdf',visual_origin='pdf_crop',bbox=source['bbox'],region=region,
                                  needs_restore=(block.type=='tab' and block.table is None) or (block.type=='eq' and not block.latex))
                provenance['bbox']=list(fitz.Rect(provenance['bbox'])|fitz.Rect(region))
            return requested
    except Exception:
        for path in created:path.unlink(missing_ok=True)
        return set()
