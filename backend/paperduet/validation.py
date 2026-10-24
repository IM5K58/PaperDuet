import re
from collections import Counter

from .adapter import plain
from .models import Block, Table

NUMERIC_CELL = re.compile(r"[+−-]?\d+(?:[.,]\d+)*%?")


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


def translation_complete(block: Block, source: Block | None = None) -> bool:
    """Completion means text exists. Length, numbers and terminology do not
    classify a translation or cause another paid request."""
    source = source or block
    present = lambda value: bool(plain(value or '').strip())
    if block.type in {'sec','sub','ssub','p','li'} and present(source.en) and not present(block.ko):
        return False
    if present(source.caption_en) and not present(block.caption_ko):
        return False
    if block.table and any(not cell.numeric and present(cell.text_en) and not present(cell.text_ko)
                           for row in block.table.header for cell in row):
        return False
    return True
