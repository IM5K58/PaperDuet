import re
import html
from collections import Counter
from bs4 import BeautifulSoup

from .adapter import MATH, inline, plain
from .models import Block, Table

NUMERIC_CELL = re.compile(r"[+−-]?\d+(?:[.,]\d+)*%?")
NUMBER_TOKEN = re.compile(r"(?<![A-Za-z0-9_.])[+−-]?\d+(?:[.,]\d+)*(?:[eE][+−-]?\d+)?%?")


def translation_preserves_content(source: str, translated: str) -> bool:
    """Check exact source numbers and inline math before accepting AI output.

    This is an integrity check, not a prose-quality score. Existing/manual
    translations still use the presence-only completion rule below.
    """
    def tokens(text):
        value=plain(inline(text)).replace('−','-')
        value=re.sub(r'(?<=\d)\s*(?:퍼센트|percent\b)', '%', value, flags=re.I)
        value=re.sub(r'(?<=\d)\s+(?=%)', '', value)
        return NUMBER_TOKEN.findall(value)
    numbers = lambda text: Counter(n.rstrip('%') for n in tokens(text))
    percentages = lambda text: Counter(n.rstrip('%') for n in tokens(text) if n.endswith('%'))
    formulas = lambda text: Counter(re.sub(r'\s+', '', html.unescape(m[2:-2] if m.startswith(('\\(', '\\[', '$$')) else m[1:-1]))
        for m in MATH.findall(text))
    scripts = lambda text: Counter((tag.name, tag.get_text())
        for tag in BeautifulSoup(inline(text),'html.parser').find_all(['sub','sup']))
    return not (numbers(source) - numbers(translated) or formulas(source) - formulas(translated)
                or scripts(source) - scripts(translated) or percentages(source) - percentages(translated))


def table_numeric_conflict(source: Table | None, recovered: Table) -> bool:
    """Use positions only when the extracted and recovered grids align.

    Broken/merged extractions cannot be an oracle for a repaired grid. For
    matching dimensions and row labels, however, moving/changing a numeric
    value must not silently replace the original.
    """
    if source is None:
        return False
    try:
        before, width = table_grid(source)
        after, recovered_width = table_grid(recovered)
    except ValueError:
        return False
    if width != recovered_width or len(source.body) != len(recovered.body) or len(source.header) != len(recovered.header):
        return False
    # Scores may be tuples, scientific notation or mean ± deviation rather
    # than a single numeric cell. Keep all their numeric tokens at that cell.
    old = {(r-len(source.header),c): tuple(n.replace('−','-') for n in NUMBER_TOKEN.findall(plain(value)))
           for (r,c),value in before.items() if r>=len(source.header) and NUMBER_TOKEN.search(plain(value))}
    if not old:
        return False
    # First-column labels anchor the rows; changed labels indicate a repaired
    # structure whose old coordinates cannot be compared reliably.
    for row in range(len(source.body)):
        a = plain(before.get((row + len(source.header), 0), '')).strip()
        b = plain(after.get((row + len(recovered.header), 0), '')).strip()
        if not NUMERIC_CELL.fullmatch(a) and a != b:
            return False
    new = {(r-len(recovered.header),c): tuple(n.replace('−','-') for n in NUMBER_TOKEN.findall(plain(value)))
           for (r,c),value in after.items() if r>=len(recovered.header)}
    return any(new.get(location,())!=value for location,value in old.items())


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


def numeric_locations(table: Table):
    grid,_=table_grid(table)
    return {(r-len(table.header),c):plain(value).strip() for (r,c),value in grid.items()
            if r>=len(table.header) and NUMERIC_CELL.fullmatch(plain(value).strip())}


def translation_complete(block: Block, source: Block | None = None) -> bool:
    """Saved/manual translations are complete when their text exists.

    New AI answers undergo a separate integrity check before they are saved;
    reading or editing an existing document never triggers paid requests.
    """
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
