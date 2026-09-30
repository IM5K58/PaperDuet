"""Lossless fixture content import into PRD §7 (no network/AI calls).

The fixture has Korean-only table captions/headers, no figure images, and no
note evidence metadata. Do not manufacture missing English text or images.
Note refs are local candidates, never verified evidence: mark V5 for review.
"""
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup
from markdownify import markdownify

from .models import Block, Card, Cell, Document, Note, Table

DOC_ID = "rex-omni"
INLINE = {"b", "i", "sub", "sup", "code"}
EQUATIONS = {
    "(1)": r"A_i = \frac{r_i-\operatorname{mean}(r_1,\ldots,r_G)}{\operatorname{std}(r_1,\ldots,r_G)}",
    "(2)": r"\mathcal{J}_{\mathrm{GRPO}}(\theta)=\frac{1}{G}\sum_{i=1}^{G}\frac{1}{|o_i|}\sum_{t=1}^{|o_i|}\left[\min\left(\rho_{i,t}\hat{A}_{i,t},\operatorname{clip}(\rho_{i,t},1-\epsilon,1+\epsilon)\hat{A}_{i,t}\right)-\beta D_{\mathrm{KL}}[\pi_\theta\|\pi_{\mathrm{ref}}]\right]",
    "(3)": r"\operatorname{IoU}(b_j^*,\hat{b}_i)=\max_{\hat{b}_i\in\hat{B}}\operatorname{IoU}(b_j^*,\hat{b}_i)",
    "(4)": r"\mathrm{Recall}=\frac{\sum_{j=1}^{n}r_j}{n},\quad\mathrm{Precision}=\frac{\sum_{j=1}^{n}r_j}{m},\quad r_{\mathrm{IoU}}=\frac{2\cdot\mathrm{Precision}\cdot\mathrm{Recall}}{\mathrm{Precision}+\mathrm{Recall}+\epsilon}",
    "(5)": r"\exists\,\hat{p}_i\in\hat{P},\quad\mathrm{s.t.}\quad\hat{p}_i\in M_j",
}


def inline(source: str) -> str:
    soup = BeautifulSoup(source, "html.parser")
    for tag in list(soup.find_all(True)):
        if tag.name in {"script", "style", "iframe"}:
            tag.decompose()
        elif tag.name == "mark":
            tag.name = "b"  # PRD inline allowlist: retain emphasis, strip legacy styling.
            tag.attrs = {}
        elif tag.name == "span" and "tok" in tag.get("class", []):
            tag.name = "code"
            tag.attrs = {}
        elif tag.name not in INLINE:
            tag.unwrap()
        else:
            tag.attrs = {}
    return str(soup)


def plain(source: str) -> str:
    return BeautifulSoup(source, "html.parser").get_text()


def parse_table(source: str) -> Table:
    soup = BeautifulSoup(source, "html.parser")
    result = Table(header=[], body=[])
    for row in soup.select("tr"):
        header = row.find_parent("thead") is not None
        cells = []
        for col, cell in enumerate(row.find_all(["th", "td"], recursive=False)):
            value = inline(cell.decode_contents())
            text = cell.get_text().strip()
            # Only actual Korean headers have a Korean alternate in the fixture.
            cells.append(Cell(
                text_en=value,
                text_ko=value if header and re.search(r"[가-힣]", text) else None,
                colspan=int(cell.get("colspan", 1)), rowspan=int(cell.get("rowspan", 1)),
                is_header=header or cell.name == "th" or "grp" in row.get("class", []),
                numeric=bool(re.fullmatch(r"[−+\-]?\d+(?:\.\d+)?%?", text)),
            ))
            if not header and cell.select_one(".best"):
                result.best_cells.append((len(result.body), col))
        if header:
            result.header.append(cells)
        else:
            if "hi" in row.get("class", []):
                result.highlight_rows.append(len(result.body))
            result.body.append(cells)
    return result


def adapt_fixture(path: Path) -> Document:
    raw = json.loads(path.read_text(encoding="utf-8"))
    named = {str(item["n"]): f"b{i:04d}" for i, item in enumerate(raw) if item.get("n")}
    blocks: list[Block] = []
    section_path: list[str] = []
    previous = "b0000"
    for i, item in enumerate(raw):
        kind = item["t"]
        if kind in {"sec", "sub", "ssub"}:
            depth = {"sec": 0, "sub": 1, "ssub": 2}[kind]
            section_path = section_path[:depth] + [item["n"]]
        block = Block(id=f"b{i:04d}", doc_id=DOC_ID, order=i, type=kind,
                      section_path=section_path.copy(), n=item.get("n"))
        if kind in {"sec", "sub", "ssub", "p", "li"}:
            block.en, block.ko = inline(item["en"]), inline(item["ko"])
        elif kind == "tab":
            block.caption_ko = inline(item["cap"])
            block.table = parse_table(item["html"])
        elif kind == "fig":
            block.caption_en, block.caption_ko = inline(item["en"]), inline(item["ko"])
        elif kind == "eq":
            block.latex = EQUATIONS[item["n"]]
        elif kind == "card":
            block.ko = inline(item["h"])
            block.card = Card(body=plain(item["b"]), explain_ko=inline(item.get("ct", "")))
        elif kind == "note":
            refs = [previous]
            text = plain(item["b"])
            for prefix, number in re.findall(r"(Table\s*|표\s*|Figure\s*|그림\s*|§)(\d+(?:\.\d+)*)", text):
                label = ("Table " if prefix.strip() in {"Table", "표"} else
                         "Figure " if prefix.strip() in {"Figure", "그림"} else "") + number
                if label in named and named[label] not in refs:
                    refs.append(named[label])
            block.note = Note(kind=item["k"], title=item["h"],
                              body_md=markdownify(item["b"], heading_style="ATX").strip(),
                              claim="interpretation", refs=refs, origin="generated")
            block.qa_flags = ["V5"]
        blocks.append(block)
        if kind != "note":
            previous = block.id
    return Document(id=DOC_ID, title="Detect Anything via Next Point Prediction",
                    title_ko="다음 점 예측으로 무엇이든 검출하다", arxiv_id="2510.12798",
                    status="fixture", blocks=blocks)
