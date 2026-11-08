"""Conservative, side-effect-free identities for a second source extraction.

Matching means "the same source location/content", not "safe to keep a Korean
translation". Callers must compare source_signature (and image fingerprints)
before retaining derived content. IDs, translations and user notes never enter
the signature. Uncertain splits, merges and duplicate occurrences stay unmatched.
"""
import hashlib
import json
import math
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from collections.abc import Mapping, Sequence

from bs4 import BeautifulSoup, Comment, NavigableString

from .models import Block


def _text(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', value or '')).strip()


def _inline(value):
    """Normalize entities/spacing without flattening scripts or inline math."""
    soup=BeautifulSoup(value or '', 'html.parser')
    for tag in soup.find_all(True):
        tag.name={'strong':'b', 'em':'i'}.get(tag.name,tag.name)
        # Stored source inline HTML has no semantic attributes. Presentation
        # attributes do not make a new source; tag structure still does.
        tag.attrs={}
    for node in list(soup.find_all(string=True)):
        if isinstance(node,Comment):
            node.extract()
        else:
            node.replace_with(NavigableString(re.sub(r'\s+',' ',unicodedata.normalize('NFC',str(node)))))
    return str(soup).strip()


def source_signature(block: Block) -> str:
    """SHA-256 of source type, label, HTML, caption, LaTeX and table structure.

    Position, section path, IDs, image paths, all Korean values, notes, restore
    errors and score decorations are excluded. Compare image bytes separately:
    a file path alone is not evidence that its pixels stayed the same.
    """
    table=None
    if block.table is not None:
        table=[[[(_inline(cell.text_en),cell.colspan,cell.rowspan,cell.is_header)
                  for cell in row] for row in part]
               for part in (block.table.header,block.table.body)]
    value=[block.type,_text(block.n),_inline(block.en),_inline(block.caption_en),_text(block.latex),table]
    encoded=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _provenance(sources, blocks):
    if isinstance(sources,Mapping):
        return [sources.get(block.id,{}) or {} for block in blocks]
    if isinstance(sources,Sequence):
        return [sources[index] if index<len(sources) and sources[index] else {} for index in range(len(blocks))]
    return [{} for _ in blocks]


def _section(block):
    return tuple(_text(part) for part in block.section_path)


def _rectangle(source):
    for key in ('region','bbox'):
        value=source.get(key)
        if not isinstance(value,(list,tuple)) or len(value)!=4:
            continue
        try:
            x0,y0,x1,y1=map(float,value)
        except (TypeError,ValueError):
            continue
        if all(math.isfinite(v) for v in (x0,y0,x1,y1)) and x1>x0 and y1>y0:
            return x0,y0,x1,y1
    return None


def _distance(old,new,old_source,new_source):
    """Comparable source pixels on the same page, otherwise no location proof."""
    if old.page is None or new.page is None or old.page!=new.page:
        return None
    if old_source.get('origin') and new_source.get('origin') and old_source['origin']!=new_source['origin']:
        return None
    a,b=_rectangle(old_source),_rectangle(new_source)
    if a is None or b is None:
        return None
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    area_a,area_b=(a[2]-a[0])*(a[3]-a[1]),(b[2]-b[0])*(b[3]-b[1])
    iou=intersection/(area_a+area_b-intersection)
    center=math.hypot((a[0]+a[2]-b[0]-b[2])/2,(a[1]+a[3]-b[1]-b[3])/2)
    horizontal=max(0,min(a[2],b[2])-max(a[0],b[0]))/min(a[2]-a[0],b[2]-b[0])
    if iou<.15 and not (center<=max(24,min(60,max(a[3]-a[1],b[3]-b[1])*.5)) and horizontal>=.5):
        return None
    return (1-iou)+center/1000


def _has_source(block):
    return block.type not in {'note','card'} and bool(block.en or block.caption_en or block.latex or block.table or block.n)


def _fingerprint(source):
    value=source.get('image_fingerprint')
    return value if isinstance(value,str) and value else None


def _visible(block):
    return _text(BeautifulSoup(block.en or '', 'html.parser').get_text(' ',strip=True))


def _partial(before,after):
    # An exact prefix/suffix is often one half of a resegmented paragraph.
    return before!=after and (before in after or after in before)


def _better_combination(blocks,index,available,target,score):
    """A neighboring split/merge explains the source better than a fuzzy pair."""
    block=blocks[index]
    for start,end in ((index-1,index+1),(index,index+2),(index-2,index+1),(index,index+3)):
        if start<0 or end>len(blocks) or any(i not in available for i in range(start,end)):
            continue
        group=blocks[start:end]
        if any(b.type!=block.type or _section(b)!=_section(block) for b in group):
            continue
        pages=[b.page for b in group if b.page is not None]
        if pages and max(pages)-min(pages)>1:
            continue
        combined=' '.join(_visible(b) for b in group)
        if abs(len(target)-len(combined))>=abs(len(target)-len(_visible(block))):
            continue
        if SequenceMatcher(None,target,combined,autojunk=False).ratio()>score+.01:
            return True
    return False


def match_blocks(old_blocks: list[Block], old_sources, new_blocks: list[Block], new_sources) -> list[tuple[str | None,int | None]]:
    """Return pairs in new order, followed by missing old IDs in old order.

    Provenance accepts either id->dict (Store.sources) or a parallel list
    (parser.structure). A unique source_anchor is preferred; optional
    image_fingerprint strings match identical artwork even without captions.
    Exact source matches survive reordering. Duplicate content first uses page
    geometry, then occurrence order only for equally sized compatible groups.
    Named figures/tables/equations can be related despite changed extraction.
    Fuzzy prose needs the same section, page, nearby geometry, >=94% similarity
    and an unambiguous mutual best candidate; it never implies unchanged source.
    """
    if len({b.id for b in old_blocks})!=len(old_blocks):
        raise ValueError('Old source block IDs must be unique')
    old_prov,new_prov=_provenance(old_sources,old_blocks),_provenance(new_sources,new_blocks)
    remaining_old=set(range(len(old_blocks)))
    remaining_new=set(range(len(new_blocks)))
    matched={}

    def bind(oi,ni):
        matched[ni]=oi
        remaining_old.remove(oi)
        remaining_new.remove(ni)

    def unique(key):
        old_groups,new_groups=defaultdict(list),defaultdict(list)
        for index in sorted(remaining_old):
            value=key(old_blocks[index],old_prov[index])
            if value is not None:old_groups[value].append(index)
        for index in sorted(remaining_new):
            value=key(new_blocks[index],new_prov[index])
            if value is not None:new_groups[value].append(index)
        for value,old in old_groups.items():
            new=new_groups.get(value,[])
            if len(old)==len(new)==1:bind(old[0],new[0])

    unique(lambda b,s:(b.type,s.get('origin',''),s['source_anchor']) if b.type not in {'note','card'} and s.get('source_anchor') else None)
    unique(lambda b,s:(b.type,_fingerprint(s)) if b.type in {'fig','tab','eq'} and _fingerprint(s) else None)
    unique(lambda b,s:source_signature(b) if _has_source(b) else None)

    old_groups,new_groups=defaultdict(list),defaultdict(list)
    for oi in sorted(remaining_old):
        if _has_source(old_blocks[oi]):old_groups[source_signature(old_blocks[oi])].append(oi)
    for ni in sorted(remaining_new):
        if _has_source(new_blocks[ni]):new_groups[source_signature(new_blocks[ni])].append(ni)
    for signature,old in old_groups.items():
        new=new_groups.get(signature,[])
        # Repeated source occurrences need stronger evidence than global text.
        edges={}
        for oi in old:
            for ni in new:
                distance=_distance(old_blocks[oi],new_blocks[ni],old_prov[oi],new_prov[ni])
                if distance is not None:edges[oi,ni]=distance
        _mutual(edges,bind,remaining_old,remaining_new,minimum_margin=1e-6,lower=True)
        old=[i for i in old if i in remaining_old]
        new=[i for i in new if i in remaining_new]
        # Page/section identities distinguish removed and inserted repetitions.
        old_locations,new_locations=defaultdict(list),defaultdict(list)
        for oi in old:old_locations[old_blocks[oi].page,_section(old_blocks[oi])].append(oi)
        for ni in new:new_locations[new_blocks[ni].page,_section(new_blocks[ni])].append(ni)
        for location,old_at in old_locations.items():
            new_at=new_locations.get(location,[])
            if len(old_at)==len(new_at):
                for oi,ni in zip(old_at,new_at):
                    # Known but incompatible rectangles are evidence against
                    # an occurrence match, not a reason to fall back to order.
                    if _rectangle(old_prov[oi]) and _rectangle(new_prov[ni]) and _distance(old_blocks[oi],new_blocks[ni],old_prov[oi],new_prov[ni]) is None:
                        continue
                    bind(oi,ni)

    # Explicit labels are stable document identities, but reused labels in
    # different sections/pages must not turn unrelated material into a change.
    def named_key(block,source):
        return (block.type,_text(block.n),_section(block)) if block.type in {'fig','tab','eq','sec','sub','ssub'} and _text(block.n) else None
    old_named,new_named=defaultdict(list),defaultdict(list)
    for oi in sorted(remaining_old):
        key=named_key(old_blocks[oi],old_prov[oi])
        if key is not None:old_named[key].append(oi)
    for ni in sorted(remaining_new):
        key=named_key(new_blocks[ni],new_prov[ni])
        if key is not None:new_named[key].append(ni)
    for key,old in old_named.items():
        new=new_named.get(key,[])
        if len(old)==len(new)==1:
            oi,ni=old[0],new[0]
            a,b=old_blocks[oi].page,new_blocks[ni].page
            if a is None or b is None or abs(a-b)<=1:bind(oi,ni)

    edges={}
    for oi in sorted(remaining_old):
        before=old_blocks[oi]
        if before.type not in {'p','li','sec','sub','ssub'}:continue
        a=_inline(before.en)
        if len(a)<40:continue
        for ni in sorted(remaining_new):
            after=new_blocks[ni]
            if after.type!=before.type or _section(after)!=_section(before):continue
            if _distance(before,after,old_prov[oi],new_prov[ni]) is None:continue
            b=_inline(after.en)
            if not b or min(len(a),len(b))/max(len(a),len(b))<.9:continue
            if _partial(_visible(before),_visible(after)):continue
            score=SequenceMatcher(None,a,b,autojunk=False).ratio()
            if score<.94:continue
            visible_score=SequenceMatcher(None,_visible(before),_visible(after),autojunk=False).ratio()
            if _better_combination(new_blocks,ni,remaining_new,_visible(before),visible_score):continue
            if _better_combination(old_blocks,oi,remaining_old,_visible(after),visible_score):continue
            edges[oi,ni]=score
    _mutual(edges,bind,remaining_old,remaining_new,minimum_margin=.025)
    return [(old_blocks[matched[ni]].id if ni in matched else None,ni) for ni in range(len(new_blocks))]+[(old_blocks[oi].id,None) for oi in sorted(remaining_old)]


def _mutual(edges,bind,remaining_old,remaining_new,minimum_margin,lower=False):
    """Only isolated mutual bests; ambiguous equal candidates are never guessed."""
    while edges:
        old_candidates,new_candidates=defaultdict(list),defaultdict(list)
        for (oi,ni),score in edges.items():
            if oi in remaining_old and ni in remaining_new:
                value=score if lower else -score
                old_candidates[oi].append((value,ni))
                new_candidates[ni].append((value,oi))
        def best(candidates):
            result={}
            for index,values in candidates.items():
                values.sort()
                if len(values)==1 or values[1][0]-values[0][0]>minimum_margin:result[index]=values[0][1]
            return result
        old_best,new_best=best(old_candidates),best(new_candidates)
        pairs=[(oi,ni) for oi,ni in old_best.items() if new_best.get(ni)==oi]
        if not pairs:break
        for oi,ni in pairs:bind(oi,ni)
        edges={pair:score for pair,score in edges.items() if pair[0] in remaining_old and pair[1] in remaining_new}
