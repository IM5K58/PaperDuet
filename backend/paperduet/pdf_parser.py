"""Coordinate based PDF adapter. No fixture lookup or AI-dependent source text.

Page caches retain positions independently from the public PRD Block schema.
All MuPDF access is serialized: its native document objects are not thread safe.
"""
import html
import json
import re
import threading
from collections import Counter
from pathlib import Path

import pymupdf as fitz

from .models import Block, Cell, Table

PDF_LOCK = threading.RLock()
fitz.no_recommend_layout()
fitz.set_messages(pylogging=True, pylogging_level=50)
fitz.TOOLS.mupdf_display_errors(False)
fitz.TOOLS.mupdf_display_warnings(False)
CAPTION = re.compile(r"^(Table|Figure|Fig\.)\s+(\d+[a-z]?)\s*[:.]\s*", re.I)
HEADING = re.compile(r"^((?:\d+(?:\.\d+){0,3}|[A-Z](?:\.\d+){0,3}))\.?\s+(.+)$")
NUMBER = re.compile(r"^[+−-]?\d+(?:[.,]\d+)*%?$|^[-–—]$")


def clean(text):
    return text.replace("\x00", "").replace("\u00ad", "").strip()


def union(rectangles):
    result = fitz.Rect(rectangles[0])
    for r in rectangles[1:]:
        result |= fitz.Rect(r)
    return result


def overlap(rect, region):
    r = fitz.Rect(rect)
    return r.get_area() > 0 and (r & fitz.Rect(region)).get_area() / r.get_area() > .55


def lines_of(page):
    result = []
    for bi, block in enumerate(page.get_text("dict", flags=fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_IMAGES)["blocks"]):
        for line in block.get("lines", []):
            if abs(line["dir"][0]) < .9:
                continue
            spans = line["spans"]
            text = clean("".join(s["text"] for s in spans))
            if text:
                result.append({"bbox": list(line["bbox"]), "text": text, "block": bi,
                               "size": max(s["size"] for s in spans),
                               "bold": all((s["flags"] & 16) or "Bold" in s["font"] for s in spans if s["text"].strip()),
                               "spans": spans})
    return result


def inline_lines(lines):
    parts = []
    for line in lines:
        text = ""
        for s in line["spans"]:
            value = html.escape(s["text"], quote=False)
            if s["flags"] & 16:
                value = f"<b>{value}</b>"
            elif s["flags"] & 2:
                value = f"<i>{value}</i>"
            text += value
        parts.append(text.strip())
    # Preserve explicit hyphens rather than silently changing scientific terms.
    return " ".join(parts)


def coordinates(values, tolerance=1.5):
    groups = []
    for value in sorted(values):
        if not groups or value - groups[-1][-1] > tolerance:
            groups.append([value])
        else:
            groups[-1].append(value)
    return [sum(g) / len(g) for g in groups]


def native_table(table):
    xs = coordinates([c[x] for c in table.cells for x in (0, 2)])
    ys = coordinates([c[y] for c in table.cells for y in (1, 3)])
    grid = [[] for _ in range(len(ys)-1)]
    texts = table.extract()
    seen = set()
    for ri, row in enumerate(table.rows):
        for ci, rect in enumerate(row.cells):
            if rect is None or tuple(rect) in seen:
                continue
            seen.add(tuple(rect))
            x0, x1 = [min(range(len(xs)), key=lambda i: abs(xs[i]-rect[x])) for x in (0, 2)]
            y0, y1 = [min(range(len(ys)), key=lambda i: abs(ys[i]-rect[y])) for y in (1, 3)]
            value = clean(texts[ri][ci] or "").replace("\n", " ")
            grid[y0].append((x0, Cell(text_en=html.escape(value), colspan=max(1, x1-x0), rowspan=max(1, y1-y0), numeric=bool(NUMBER.fullmatch(value)))))
    rows = [[cell for _, cell in sorted(row, key=lambda c: c[0])] for row in grid]
    for c in rows[0]:
        c.is_header = True
    return Table(header=rows[:1], body=rows[1:])


def aligned_numeric_table(page, found, region, lines):
    """Repair ruled evaluation tables whose unruled body rows were merged.

    Column edges come from the PDF's cell geometry. Numeric baselines determine
    body rows; source group labels become rowspans. No numeric value is generated.
    """
    xs = coordinates([c[x] for c in found.cells for x in (0, 2)])
    if len(xs) < 5:
        return None
    words = page.get_text("words", clip=region)
    numeric = [w for w in words if NUMBER.fullmatch(w[4]) and w[0] > xs[2]]
    ys = coordinates([(w[1]+w[3])/2 for w in numeric], 2)
    baselines = [y for y in ys if sum(abs((w[1]+w[3])/2-y)<2 for w in numeric) >= max(2, (len(xs)-3)//2)]
    if len(baselines) < 3:
        return None
    # Header threshold labels (0.5 etc.) are separated by a full-width rule.
    rules = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l" and abs(item[1].y-item[2].y)<.5 and abs(item[1].x-item[2].x) > region.width*.85:
                rules.append(item[1].y)
    first_rule = next((y for y in sorted(rules) if y > region.y0+5 and any(b>y for b in baselines)), None)
    if first_rule is not None:
        baselines = [y for y in baselines if y > first_rule]
    if len(baselines) < 3:
        return None
    first = min(w[1] for w in words if abs((w[1]+w[3])/2-baselines[0]) < 2)
    header_bottom = first_rule if first_rule is not None else first-1
    bounds = [header_bottom] + [(a+b)/2 for a,b in zip(baselines,baselines[1:])] + [region.y1+1]
    rows = [[[] for _ in range(len(xs)-1)] for _ in baselines]
    for word in words:
        x, y = (word[0]+word[2])/2, (word[1]+word[3])/2
        if y < header_bottom:
            continue
        ri = min(range(len(baselines)), key=lambda i: abs(baselines[i]-y))
        ci = next((i for i in range(len(xs)-1) if xs[i]-1 <= x <= xs[i+1]+1), None)
        if ci is not None:
            rows[ri][ci].append(word)
    body = [[Cell(text_en=html.escape(" ".join(w[4] for w in sorted(cell, key=lambda w:w[0]))),
                  numeric=bool(NUMBER.fullmatch(" ".join(w[4] for w in cell))) and any(ch.isdigit() for w in cell for ch in w[4])) for cell in row] for row in rows]
    # A centered first-column category spans the contiguous otherwise-empty rows.
    leading = [i for i,row in enumerate(body) if row[0].text_en]
    if leading and len(leading) < len(body)/2:
        for index, ri in enumerate(leading):
            center = baselines[ri]
            before = max([r for r in rules if header_bottom-.5 <= r < center] or [header_bottom])
            after = min([r for r in rules if center < r <= region.y1+2] or [region.y1+1])
            start = next((i for i,y in enumerate(baselines) if y>before), ri)
            end = max([i for i,y in enumerate(baselines) if y<after] or [ri])
            # If rules are absent, split the empty run at adjacent label midpoints.
            if any(i!=ri and start<=i<=end for i in leading):
                start = 0 if index==0 else (leading[index-1]+ri)//2+1
                end = len(body)-1 if index==len(leading)-1 else (ri+leading[index+1])//2
            value = body[ri][0].text_en
            body[start][0] = Cell(text_en=value, rowspan=end-start+1)
            for j in range(start+1,end+1):
                body[j][0] = None
        body = [[c for c in row if c is not None] for row in body]
    # Detect spanning top-level header cells using original cell rectangles.
    head_lines = [l for l in lines if overlap(l["bbox"], region) and l["bbox"][3] < header_bottom+1]
    groups = []
    for rect in found.cells:
        x0, x1 = [min(range(len(xs)), key=lambda i:abs(xs[i]-rect[x])) for x in (0,2)]
        contained = [l for l in head_lines if overlap(l["bbox"], rect)]
        if x1-x0>1 and len(contained)==1 and rect[3]<header_bottom-1:
            groups.append((x0,x1,contained[0]))
    top, bottom = [], []
    ci = 0
    while ci < len(xs)-1:
        group = next((g for g in groups if g[0]==ci), None)
        if group:
            start,end,line = group
            top.append(Cell(text_en=html.escape(line["text"]), colspan=end-start, is_header=True))
            for col in range(start,end):
                content = [w for w in words if xs[col] <= (w[0]+w[2])/2 < xs[col+1] and line["bbox"][3] <= (w[1]+w[3])/2 < header_bottom]
                bottom.append(Cell(text_en=html.escape(" ".join(w[4] for w in sorted(content,key=lambda w:(round(w[1],0),w[0])))), is_header=True))
            ci=end
        else:
            content = [w for w in words if xs[ci] <= (w[0]+w[2])/2 < xs[ci+1] and (w[1]+w[3])/2<header_bottom]
            top.append(Cell(text_en=html.escape(" ".join(w[4] for w in sorted(content,key=lambda w:(round(w[1],0),w[0])))), is_header=True, rowspan=2 if groups else 1))
            ci+=1
    return Table(header=[top,bottom] if groups else [top], body=body)


def reading_order(items, width):
    """Full-width anchors split vertical bands; within each band read left then right."""
    middle = width/2
    left = [i for i in items if i["bbox"][2] <= middle+8]
    right = [i for i in items if i["bbox"][0] >= middle-8]
    if len(left)<2 or len(right)<2:
        return sorted(items, key=lambda i:(i["bbox"][1],i["bbox"][0]))
    wide = sorted([i for i in items if i not in left and i not in right],key=lambda i:i["bbox"][1])
    result, remaining = [], left+right
    for anchor in wide:
        band = [i for i in remaining if i["bbox"][1] < anchor["bbox"][1]]
        result.extend(sorted(band,key=lambda i:(0 if i["bbox"][0]<middle-8 else 1,i["bbox"][1])))
        remaining=[i for i in remaining if i not in band]
        result.append(anchor)
    result.extend(sorted(remaining,key=lambda i:(0 if i["bbox"][0]<middle-8 else 1,i["bbox"][1])))
    return result


class PyMuPDFParser:
    def __init__(self):
        self.margin_cache = {}
        self.body_font_cache = {}

    def margins(self,pdf,path):
        key=str(path)
        if key not in self.margin_cache:
            counts=Counter()
            fonts=Counter()
            for p in pdf:
                page_lines=lines_of(p)
                for l in page_lines:
                    if len(l['text'])>45:fonts[round(l['size'])]+=len(l['text'])
                values={l['text'] for l in page_lines if l['bbox'][1]<p.rect.height*.1 or l['bbox'][3]>p.rect.height*.9}
                counts.update(values)
            self.margin_cache[key]={s for s,count in counts.items() if count>=max(2,len(pdf)//3)}
            self.body_font_cache[key]=fonts.most_common(1)[0][0] if fonts else 11
        return self.margin_cache[key]

    def metadata(self, path: Path):
        with PDF_LOCK, fitz.open(path) as pdf:
            if not pdf.is_pdf or pdf.needs_pass or not 1<=len(pdf)<=1000:
                raise ValueError("UNSUPPORTED_PDF")
            lines=lines_of(pdf[0])
            title=clean(pdf.metadata.get("title") or "")
            if not title:
                largest=max((l["size"] for l in lines if l["bbox"][1]<pdf[0].rect.height*.3),default=0)
                title=" ".join(l["text"] for l in lines if l["size"]>largest-.5 and l["bbox"][1]<pdf[0].rect.height*.3)
            first_text=pdf[0].get_text('text')
            arxiv=re.search(r"(?:arXiv:)?(\d{4}\.\d{4,5})",first_text)
            authors=clean(pdf.metadata.get("author") or "")
            if not authors:
                authors=" ".join(l["text"] for l in lines if 110<l["bbox"][1]<160 and l["bold"] and l["size"]<16)
            return {"title":title or path.stem,"authors":authors,"page_count":len(pdf),"arxiv_id":arxiv[1] if arxiv else "","toc":pdf.get_toc()}

    def page(self, path: Path, page_number: int, target: Path):
        with PDF_LOCK, fitz.open(path) as pdf:
            page=pdf[page_number]
            lines=lines_of(page)
            height,width=page.rect.height,page.rect.width
            margins=self.margins(pdf,path)
            # Retain footnotes and text close to page edges. Only repeated margin
            # text and isolated margin page numbers are furniture.
            lines=[l for l in lines if not ((l['bbox'][1]<height*.1 or l['bbox'][3]>height*.9) and (l['text'] in margins or l['text'].isdigit()))]
            font_counts=Counter()
            for l in lines:
                for s in l["spans"]:
                    font_counts[round(s["size"])]+=len(s["text"])
            body_size=self.body_font_cache[str(path)]
            grouped={}
            for l in lines:
                grouped.setdefault(l["block"],[]).append(l)
            captions=[]
            for group in grouped.values():
                text=" ".join(l["text"] for l in group)
                match=CAPTION.match(text)
                # A body sentence may also begin "Table 2. For the ...".
                # Period-style captions use the smaller caption font in CV papers.
                if match and not ('.' in text[:match.end()] and max(l['size'] for l in group)>body_size-.25 and len(text)>180):
                    captions.append({"kind":"tab" if match[1].lower()=="table" else "fig","n":("Table " if match[1].lower()=="table" else "Figure ")+match[2],
                                     "bbox":list(union([l["bbox"] for l in group])),"text":text[match.end():].strip(),"group":group})
            drawings=page.get_drawings()
            graphics=[fitz.Rect(i["bbox"]) for i in page.get_image_info() if fitz.Rect(i["bbox"]).get_area()>80]
            graphics += [d["rect"] for d in drawings if d["rect"].width>8 and d["rect"].height>8 and d["rect"].height<height*.85]
            detected=page.find_tables().tables if any(c["kind"]=="tab" for c in captions) else []
            excluded=[]
            items=[]
            target.mkdir(parents=True,exist_ok=True)
            for ci,caption in enumerate(sorted(captions,key=lambda c:c["bbox"][1])):
                cap=fitz.Rect(caption["bbox"])
                column=fitz.Rect(0,0,width,height) if cap.width>width*.55 else fitz.Rect(0 if cap.x0<width/2 else width/2,0,width/2 if cap.x0<width/2 else width,height)
                same_column=lambda r: (fitz.Rect(r)&column).width>=fitz.Rect(r).width*.8
                founds=[t for t in detected if t.bbox[3]<=cap.y0+3 and cap.y0-t.bbox[3]<60 and fitz.Rect(t.bbox).width>60 and same_column(t.bbox)]
                found=max(founds,key=lambda t:t.bbox[3],default=None) if caption["kind"]=="tab" else None
                if found:
                    region=fitz.Rect(found.bbox)
                    # Adjacent detected fragments can be one captioned table.
                    for t in founds:
                        if t is not found and abs(t.bbox[3]-region.y0)<15:
                            region |= fitz.Rect(t.bbox)
                else:
                    previous=max([c["bbox"][3] for c in captions if c["bbox"][3]<cap.y0 and same_column(c['bbox'])]+[r[3] for r in excluded if r[3]<cap.y0 and same_column(r)] or [height*.085])
                    candidates=[g for g in graphics if g.y0>=previous-2 and g.y1<=cap.y0+2 and g.x1>cap.x0 and g.x0<cap.x1]
                    if candidates:
                        # Stop at a genuine body paragraph between artwork and caption.
                        nearest=max(g.y1 for g in candidates)
                        body_before=[l["bbox"][3] for l in lines if l["bbox"][3]<nearest and same_column(l['bbox']) and l["size"]>=body_size-.5 and len(l["text"])>60 and not any(overlap(l["bbox"],g) for g in candidates)]
                        start=max(body_before or [previous])
                        candidates=[g for g in candidates if g.y1>start and g.y0>=start-2]
                    region=union(candidates) if candidates else fitz.Rect(cap.x0,max(previous,cap.y0-120),cap.x1,cap.y0-3)
                if caption['kind']=='tab':
                    previous=max([c['bbox'][3] for c in captions if c['bbox'][3]<cap.y0 and same_column(c['bbox'])] or [height*.085])
                    prose=[l['bbox'][3] for l in lines if previous<l['bbox'][3]<region.y0 and same_column(l['bbox']) and l['size']>=9.5 and len(l['text'])>65]
                    lower=max(prose or [previous])
                    rules=[]
                    for d in drawings:
                        for part in d['items']:
                            if part[0]=='l' and abs(part[1].y-part[2].y)<.5 and abs(part[1].x-part[2].x)>width*.55 and lower<part[1].y<cap.y0:
                                rules.append(fitz.Rect(min(part[1].x,part[2].x),part[1].y,max(part[1].x,part[2].x),part[1].y+.1))
                    if rules:
                        region |= union(rules)
                if caption['kind']=='tab' and '.' in ' '.join(l['text'] for l in caption['group'])[:20]:
                    # Top captions are common in CVPR PDFs. Use rules from the
                    # same column and stop at the next genuine prose paragraph.
                    rules=[]
                    for d in drawings:
                        for part in d['items']:
                            if part[0]=='l' and abs(part[1].y-part[2].y)<.6:
                                r=fitz.Rect(min(part[1].x,part[2].x),part[1].y,max(part[1].x,part[2].x),part[1].y+.1)
                                if same_column(r) and r.width>cap.width*.65:rules.append(r)
                    after=sorted([r for r in rules if r.y0>=cap.y1-1],key=lambda r:r.y0)
                    before=sorted([r for r in rules if r.y1<=cap.y0+1],key=lambda r:r.y0)
                    if after and after[0].y0-cap.y1<55 and (not before or after[0].y0-cap.y1<cap.y0-before[-1].y1):
                        start=after[0].y0
                        boundaries=[l['bbox'][1] for l in lines if same_column(l['bbox']) and l['bbox'][1]>start and len(l['text'])>55 and l['size']>body_size-.25]
                        boundaries += [c['bbox'][1] for c in captions if c is not caption and same_column(c['bbox']) and c['bbox'][1]>start]
                        end=min(boundaries or [height*.94])
                        relevant=[r for r in after if r.y0<end]
                        region=union(relevant)
                        # Include header text above the first rule, below caption.
                        top=[l['bbox'][1] for l in lines if same_column(l['bbox']) and cap.y1<l['bbox'][1]<region.y1]
                        region.y0=min(top or [region.y0])-2
                        region.y1+=2
                        found=next((t for t in detected if overlap(t.bbox,region) and fitz.Rect(t.bbox).width>region.width*.9),None)
                if caption['kind']=='fig':
                    # Axes and legend labels can lie outside vector/image bounds.
                    labels=[l['bbox'] for l in lines if same_column(l['bbox']) and region.y0-8<=l['bbox'][1] and l['bbox'][3]<=min(cap.y0-2,region.y1+8) and l['size']<body_size]
                    if labels: region |= union(labels)
                region &= page.rect
                region &= column
                if region.is_empty:
                    continue
                filename=f"p{page_number+1:04d}-{caption['kind']}-{ci}.png"
                scale=min(2,1800/max(region.width,region.height))
                page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=region,alpha=False).save(target/filename)
                item={"type":caption["kind"],"n":caption["n"],"caption_en":html.escape(caption["text"]),"bbox":list(region|cap),
                      "crop":filename,"region":list(region),"page":page_number+1}
                if caption["kind"]=="tab":
                    table=(aligned_numeric_table(page,found,region,lines) or native_table(found)) if found else None
                    if table and (not table.body or (found and fitz.Rect(found.bbox).width<region.width*.9)):
                        table=None
                    item["en"]=html.escape(page.get_text("text",clip=region,sort=True))
                    if table:
                        from .validation import suspicious_table, table_grid
                        try: table_grid(table)
                        except ValueError: table=None
                        # A merged or partial grid reads worse than the page image;
                        # leave it to image restoration instead.
                        if table and suspicious_table(table,item["en"]): table=None
                    item["table"]=table.model_dump(exclude_none=True) if table else None
                    item["needs_restore"]=True
                excluded.extend([list(region),list(cap)])
                items.append(item)
            # Equation labels occupy the right margin; collect adjacent mathematical
            # lines instead of flattening their fragments into prose.
            two_column=sum(l['bbox'][0]>=width/2 and len(l['text'])>45 for l in lines)>2 and sum(l['bbox'][2]<width/2+8 and len(l['text'])>45 for l in lines)>2
            def prose_line(l):
                return (l['text'].endswith(':') and bool(re.search('[a-zA-Z]{3}',l['text']))) or len(re.findall(r'[A-Za-z]{3,}',l['text']))>=4 or (bool(re.match(r'[A-Za-z][a-z]{2,}\s',l['text'])) and len(re.findall(r'[A-Za-z]{2,}',l['text']))>=3) or (l['bold'] and bool(HEADING.match(l['text'])))
            for line in lines:
                label=re.search(r'\((\d+)\)$',line['text'])
                right_edge=width/2-10 if two_column and line['bbox'][2]<width/2+8 else width*.91
                if label and line['bbox'][2]>right_edge-18 and not prose_line(line) and not any(overlap(line["bbox"],r) for r in excluded):
                    y=(line['spans'][-1]['bbox'][1]+line['spans'][-1]['bbox'][3])/2
                    same=lambda l: not two_column or (l['bbox'][0]<width/2)==(line['bbox'][0]<width/2)
                    other_labels=[(l['bbox'][1]+l['bbox'][3])/2 for l in lines if same(l) and re.search(r'\(\d+\)$',l['text']) and l is not line and not prose_line(l)]
                    above=max([l['bbox'][3] for l in lines if same(l) and prose_line(l) and l['bbox'][3]<y]+[(v+y)/2 for v in other_labels if v<y] or [y-30])
                    below=min([l['bbox'][1] for l in lines if same(l) and prose_line(l) and l['bbox'][1]>y]+[(v+y)/2 for v in other_labels if v>y] or [y+30])
                    math_lines=[l for l in lines if same(l) and max(y-30,above)<(l['bbox'][1]+l['bbox'][3])/2<min(y+30,below) and not prose_line(l) and not any(overlap(l["bbox"],r) for r in excluded)]
                    if not math_lines:continue
                    region=union([l["bbox"] for l in math_lines])
                    filename=f"p{page_number+1:04d}-eq-{label[1]}.png"
                    page.get_pixmap(matrix=fitz.Matrix(2,2),clip=region+(-3,-3,3,3),alpha=False).save(target/filename)
                    items.append({"type":"eq","n":'('+label[1]+')',"en":inline_lines(math_lines),"bbox":list(region),"region":list(region),"crop":filename,"page":page_number+1,"needs_restore":True})
                    excluded.append(list(region))
            for group in grouped.values():
                # Split at the column gutter even if MuPDF grouped both columns.
                remaining=[l for l in group if not any(overlap(l["bbox"],r) for r in excluded)]
                if not remaining:
                    continue
                columns=[remaining]
                if all(l["bbox"][2]-l["bbox"][0]<width*.48 for l in remaining):
                    columns=[[l for l in remaining if l["bbox"][0]<width/2],[l for l in remaining if l["bbox"][0]>=width/2]]
                for subset in columns:
                    if not subset:
                        continue
                    text=" ".join(l["text"] for l in subset)
                    kind,n="p",None
                    match=HEADING.match(text)
                    if len(text)<160 and all(l["bold"] for l in subset) and match:
                        n=match[1]; kind=["sec","sub","ssub"][min(n.count('.'),2)]
                        content=html.escape(match[2])
                    elif text.lower() in {"abstract","references","bibliography","acknowledgements","acknowledgments","appendix"}:
                        kind,n="sec",text; content=html.escape(text)
                    else:
                        content=inline_lines(subset)
                        if text.startswith(('•','– ')):
                            kind="li"
                    items.append({"type":kind,"n":n,"en":content,"bbox":list(union([l["bbox"] for l in subset])),"page":page_number+1})
            if page_number==0 and not any(i.get('n')=='Abstract' for i in items):
                outline=pdf.get_toc()
                has_early_introduction=any('introduction' in t[1].lower() and t[2]<=2 for t in outline)
                abstract=next((i for i in items if i['type']=='p' and len(i.get('en',''))>600 and
                    (has_early_introduction or any(d['rect'].width>width*.6 and d['rect'].height>60 and overlap(i['bbox'],d['rect']) for d in drawings))),None)
                if abstract:
                    rect=abstract['bbox']
                    items.append({'type':'sec','n':'Abstract','en':'Abstract','bbox':[rect[0],rect[1]-.1,rect[2],rect[1]],'page':1,'inferred_heading':True})
            return reading_order(items,width)

    def structure(self, doc_id: str, pages: list[list[dict]], metadata: dict):
        blocks,sources,path=[],[],[]
        references=False
        for page_items in pages:
            for item in page_items:
                if item["type"] in {"sec","sub","ssub"}:
                    depth={"sec":0,"sub":1,"ssub":2}[item["type"]]
                    path=path[:depth]+[item["n"] or ""]
                    references=(item["n"] or "").lower() in {"references","bibliography"}
                public={k:v for k,v in item.items() if k in {"type","n","en","page","caption_en","table","latex"} and v is not None}
                b=Block(id=f"b{len(blocks):04d}",doc_id=doc_id,order=len(blocks),section_path=path.copy(),**public)
                if item.get("crop"):
                    b.image_path=f"documents/{doc_id}/crops/{item['crop']}"
                if b.type=='tab' and not b.table:
                    b.qa_flags=['EXTRACT_REVIEW']
                blocks.append(b)
                sources.append({"bbox":item["bbox"],"region":item.get("region"),"references":references,"needs_restore":item.get("needs_restore",False),"inferred_heading":item.get('inferred_heading',False)})
        # An unlabelled abstract is located by its first-page enclosing box.
        # Its inferred heading carries provenance and uses that paragraph's bbox.
        # Text is never substituted with fixture content.
        return blocks,sources
