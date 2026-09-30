import re
from collections import Counter

from .adapter import plain
from .models import Block, Table

NUMBERS = re.compile(r"(?<![\w.])[+−-]?\d+(?:[.,]\d+)*%?")
NUMERIC_CELL = re.compile(r"[+−-]?\d+(?:[.,]\d+)*%?")


def number_tokens(text: str):
    return Counter(NUMBERS.findall(plain(text)))


def table_grid(table: Table):
    rows = table.header + table.body
    occupied = {}
    widths = []
    for ri, row in enumerate(rows):
        col = 0
        for cell in row:
            while (ri,col) in occupied:
                col += 1
            if cell.colspan>200 or cell.rowspan>len(rows)-ri:
                raise ValueError("Invalid table span")
            for r in range(ri,ri+cell.rowspan):
                for c in range(col,col+cell.colspan):
                    if (r,c) in occupied:
                        raise ValueError("Overlapping table cells")
                    occupied[r,c]=cell.text_en
            col += cell.colspan
        width=max([c+1 for r,c in occupied if r==ri] or [0])
        if any((ri,c) not in occupied for c in range(width)):
            raise ValueError("Missing table cell")
        widths.append(width)
    if not widths or not widths[0] or len(set(widths))!=1:
        raise ValueError("Inconsistent table widths")
    return occupied, widths[0]


def numeric_cells(table: Table):
    return Counter(plain(c.text_en).strip() for row in table.body for c in row if NUMERIC_CELL.fullmatch(plain(c.text_en).strip()))


def numeric_locations(table: Table):
    grid,_=table_grid(table)
    return {(r-len(table.header),c):plain(value).strip() for (r,c),value in grid.items()
            if r>=len(table.header) and NUMERIC_CELL.fullmatch(plain(value).strip())}


def validate_block(block: Block, source: Block, glossary: list[dict], references=False, ratio=.25):
    flags=[]
    if references:
        return flags
    pairs=[]
    if block.type in {"sec","sub","ssub","p","li"} and source.en:
        pairs.append((source.en,block.ko or ""))
    if source.caption_en:
        pairs.append((source.caption_en,block.caption_ko or ""))
    if block.table:
        try:
            table_grid(block.table)
            if source.table and numeric_locations(block.table)!=numeric_locations(source.table):
                flags.append("V4")
        except ValueError:
            flags.append("V4")
        for row in block.table.header:
            for c in row:
                if not c.numeric and plain(c.text_en).strip():
                    pairs.append((c.text_en,c.text_ko or ""))
    for en,ko in pairs:
        en,ko=plain(en).strip(),plain(ko).strip()
        if not ko:
            flags.append("V1")
            continue
        if len(ko)/max(1,len(en)) < ratio:
            flags.append("V2")
        if set(number_tokens(en))-set(number_tokens(ko)):
            flags.append("V3")
        for term in glossary:
            pattern=r"(?<!\w)"+re.escape(term["term"])+r"(?!\w)"
            if re.search(pattern,en,re.I) and term["ko"].casefold() not in ko.casefold():
                flags.append("V6")
    return sorted(set(flags))
