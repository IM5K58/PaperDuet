from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Cell(Model):
    text_en: str
    text_ko: str | None = None
    colspan: int = Field(default=1, ge=1)
    rowspan: int = Field(default=1, ge=1)
    is_header: bool = False
    numeric: bool = False


class Table(Model):
    header: list[list[Cell]]
    body: list[list[Cell]]
    highlight_rows: list[int] = Field(default_factory=list)
    best_cells: list[tuple[int, int]] = Field(default_factory=list)


class Note(Model):
    kind: Literal["key", "res", "lim", "ins", "mth", "trm"]
    title: str = Field(min_length=1,max_length=300)
    body_md: str = Field(min_length=1,max_length=12000)
    claim: Literal["stated", "interpretation", "mixed"]
    refs: list[str] = Field(min_length=1,max_length=32)
    origin: Literal["generated", "user", "ai_answer"]


class Card(Model):
    body: str
    explain_ko: str


class Block(Model):
    id: str
    doc_id: str
    order: int = Field(ge=0)
    type: Literal["sec", "sub", "ssub", "p", "li", "note", "card", "eq", "fig", "tab"]
    section_path: list[str]
    page: int | None = None
    n: str | None = None
    en: str | None = None
    ko: str | None = None
    latex: str | None = None
    table: Table | None = None
    image_path: str | None = None
    caption_en: str | None = None
    caption_ko: str | None = None
    card: Card | None = None
    note: Note | None = None
    qa_flags: list[str] = Field(default_factory=list)


class ReadingPosition(Model):
    block_id: str | None = None
    offset: float = Field(default=0, allow_inf_nan=False)


class Document(Model):
    source_kind: str = 'pdf'
    source_url: str = ''
    id: str
    title: str
    title_ko: str
    arxiv_id: str
    status: str
    blocks: list[Block]
    reading_position: ReadingPosition = Field(default_factory=ReadingPosition)
    page_count: int = 0
    authors: str = ""
    glossary: list[dict] = Field(default_factory=list)


class PipelineOptions(Model):
    provider: Literal["anthropic", "openai", "google"] = "anthropic"
    mode: Literal["api_key", "cli"] = "api_key"
    glossary_model: str = Field(default="claude-sonnet-5", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    translate_model: str = Field(default="claude-haiku-4-5", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    restore_model: str = Field(default="claude-sonnet-5", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    annotate_model: str = Field(default="claude-sonnet-5", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_.:-]+$")
    min_ratio: float = Field(default=0.25, ge=0.05, le=1, allow_inf_nan=False)
    # Saving mode: the provider's batch API at half price, answers within 24 h.
    # Chosen per job; never stored as the default (see Pipeline.approve).
    batch: bool = False


class ReaderSettings(Model):
    view: Literal["en", "split", "ko"] = "split"
    theme: Literal["system", "light", "dark"] = "system"
    font_size: int = Field(default=16, ge=13, le=21)
    show_notes: bool = True
    note_kinds: list[Literal["key", "res", "lim", "ins", "mth", "trm"]] = Field(
        default_factory=lambda: ["key", "res", "lim", "ins", "mth", "trm"]
    )
    density: Literal["low", "normal", "high"] = "high"
