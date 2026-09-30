"""Section annotation contracts and deterministic evidence checks (not fact checking)."""
import hashlib
import re

from pydantic import Field

from .adapter import plain
from .models import Block, Model, Note
from .validation import number_tokens


class Candidate(Note):
    after_block_id: str
    origin: str = 'generated'
    title: str = Field(min_length=1,max_length=300)
    body_md: str = Field(min_length=1,max_length=12000)
    refs: list[str] = Field(min_length=1,max_length=32)


class AnnotationBatch(Model):
    notes: list[Candidate] = Field(max_length=32)


def source_text(b):
    values=[b.n,b.en,b.caption_en,b.latex,b.card.body if b.card else None]
    if b.table:
        values.extend(c.text_en for row in b.table.header+b.table.body for c in row)
    return plain(' '.join(v for v in values if v))


def first_occurrences(blocks,glossary):
    result={}
    for term in glossary:
        pattern=re.compile(r'(?<!\w)'+re.escape(term['term'])+r'(?!\w)',re.I)
        for b in blocks:
            if b.type in {'p','li','card','tab','fig'} and pattern.search(source_text(b)):
                result[term['term']]=b.id;break
    return result


def section_batches(blocks,sources):
    """One annotation call per top-level section; subsections stay together and
    oversized sections split at block boundaries. Fewer, larger calls keep the
    fixed per-call cost (instructions, glossary, CLI overhead) small."""
    batches=[];batch=[];length=0
    for b in blocks:
        if b.note or sources.get(b.id,{}).get('references',True):continue
        size=len(source_text(b))
        if batch and (b.section_path[:1]!=batch[-1].section_path[:1] or length+size>20000 or len(batch)>=60):
            batches.append(batch);batch=[];length=0
        batch.append(b);length+=size
    if batch:batches.append(batch)
    return [(hashlib.sha256('|'.join(b.id for b in batch).encode()).hexdigest()[:20],batch) for batch in batches]


def section_window(blocks,sources,anchor_id,limit=24):
    """The anchor's own subsection (at most `limit` blocks around it), used when
    repairing a single note so it does not resend a whole top-level section."""
    anchor=next(b for b in blocks if b.id==anchor_id)
    same=[b for b in blocks if not b.note and not sources.get(b.id,{}).get('references',True) and b.section_path==anchor.section_path]
    index=next((i for i,b in enumerate(same) if b.id==anchor_id),0)
    start=max(0,min(index-limit//2,len(same)-limit))
    return same[start:start+limit] or [anchor]


CANDIDATE_STOP=set('the a an of and or for to in on with by from as at is are was were be been this that these those we our their its it which using use used based via than into over under between each all both more most less such can may also not no one two three first second new other where when while however thus'.split())


def glossary_candidates(texts,limit=150):
    """Frequent terminology in the paper, found locally so the glossary call
    needs only an excerpt plus this list instead of the full text."""
    from collections import Counter
    counts=Counter();spelling={}
    def add(term):
        key=term.casefold();counts[key]+=1;spelling.setdefault(key,term)
    for text in texts:
        words=re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[-'][A-Za-z0-9]+)*",plain(text or ''))
        for i,w in enumerate(words):
            if len(w)>1 and (any(c.isdigit() for c in w) or sum(c.isupper() for c in w)>=2 or '-' in w):add(w)
            if i+1<len(words):
                a,b=words[i],words[i+1]
                if a.lower() not in CANDIDATE_STOP and b.lower() not in CANDIDATE_STOP and len(a)>2 and len(b)>2:
                    add(a+' '+b)
                    if i+2<len(words) and words[i+2].lower() not in CANDIDATE_STOP and len(words[i+2])>2:add(a+' '+b+' '+words[i+2])
    ranked=[(spelling[k],n) for k,n in counts.most_common() if n>=2 or ' ' not in spelling[k]]
    return [[term,n] for term,n in ranked[:limit]]


def validate_note(block,by_id,anchor,firsts):
    note=block.note
    if not note:return []
    flags=[]
    refs=[by_id[r] for r in note.refs if r in by_id and not by_id[r].note]
    if len(refs)!=len(set(note.refs)) or not refs:flags.append('V5')
    # A marker excuses only its own calculation paragraph, not the whole note.
    text=re.sub(r'(?m)^\s*(?:[-*] )?(?:\*\*)?계산값(?:\*\*)?\s*[:：][^\n]*','',note.body_md)
    text=re.sub(r'(?m)^\s*\d+[.)]\s+','',text)
    if set(number_tokens(note.title+' '+text))-set(number_tokens(' '.join(source_text(b) for b in refs))):flags.append('V5')
    if note.kind=='mth':
        if not any(b.type=='eq' for b in refs) or by_id.get(anchor) is None or by_id[anchor].type!='eq' or not all(label in note.body_md for label in ['식이 하는 일','기호별 의미','식이 의미하는 바']):
            flags.append('NOTE_MATH')
    if note.kind=='trm':
        term=next((term for term in firsts if term.casefold()==note.title.strip().casefold()),None)
        if term is None or firsts[term]!=anchor:flags.append('NOTE_TERM')
    return sorted(set(flags))


def make_notes(doc_id,candidates,targets,by_id,firsts):
    result=[];seen=set()
    for item in candidates:
        if item.after_block_id not in targets or (item.after_block_id,item.kind) in seen:
            raise ValueError('Invalid annotation anchor')
        seen.add((item.after_block_id,item.kind))
        anchor=by_id[item.after_block_id]
        identity=hashlib.sha256((item.after_block_id+'|'+item.kind).encode()).hexdigest()[:20]
        note=Note(**item.model_dump(exclude={'after_block_id','origin'}),origin='generated')
        block=Block(id='n'+identity,doc_id=doc_id,order=0,type='note',section_path=anchor.section_path,page=anchor.page,note=note)
        block.qa_flags=validate_note(block,by_id,anchor.id,firsts)
        result.append((anchor.id,block))
    return result
