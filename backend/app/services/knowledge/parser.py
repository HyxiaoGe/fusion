from __future__ import annotations

import csv
import io
import multiprocessing
import re
import signal
import unicodedata
import zipfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import PurePath


class KnowledgeParseError(ValueError):
    def __init__(self, code: str, summary: str):
        self.code = code
        self.summary = summary
        super().__init__(summary)


@dataclass(frozen=True)
class ParsedSection:
    text: str
    page: int | None = None
    section: str | None = None


def _isolated_parse_child(connection, content: bytes, mimetype: str, filename: str) -> None:
    try:
        import resource

        memory_limit = 512 * 1024 * 1024
        with suppress(Exception):
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        with suppress(Exception):
            resource.setrlimit(resource.RLIMIT_CPU, (45, 45))
        connection.send(("ok", KnowledgeDocumentParser().parse(content, mimetype=mimetype, filename=filename)))
    except KnowledgeParseError as exc:
        connection.send(("error", (exc.code, exc.summary)))
    except Exception:
        connection.send(("error", ("KNOWLEDGE_DOCUMENT_INVALID", "文档解析子进程异常")))
    finally:
        connection.close()


def parse_document_isolated(
    content: bytes,
    *,
    mimetype: str,
    filename: str,
    timeout_seconds: int,
) -> list[ParsedSection]:
    """在有限资源子进程中解析复杂容器格式。"""
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_isolated_parse_child,
        args=(child, content, mimetype, filename),
        daemon=True,
    )
    process.start()
    child.close()
    try:
        if not parent.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=5)
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_PARSE_TIMEOUT", "文档解析超过时间上限")
        try:
            status, payload = parent.recv()
        except (EOFError, OSError) as exc:
            process.join(timeout=5)
            if process.exitcode == -signal.SIGXCPU:
                raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_PARSE_TIMEOUT", "文档解析超过 CPU 时间上限") from exc
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "文档解析子进程异常退出") from exc
        process.join(timeout=5)
        if status == "ok":
            return payload
        code, summary = payload
        raise KnowledgeParseError(code, summary)
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


_MARKDOWN_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t#]*$")
_MARKDOWN_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_DOCX_HEADING_STYLE = re.compile(r"^(?:heading|标题)\s*(\d)$", re.IGNORECASE)
# Milvus VARCHAR 按 UTF-8 字节计长度，中文一字 3 字节；同时满足 PostgreSQL String(120)。
MAX_SECTION_LABEL_BYTES = 120


class _HeadingSections:
    """按标题切分文档块：每块带完整标题路径；只有标题没有正文的块并入下一块。"""

    def __init__(self) -> None:
        self.sections: list[ParsedSection] = []
        self._path: list[tuple[int, str]] = []
        self._lines: list[str] = []
        self._has_body = False

    def heading(self, level: int, title: str, line: str) -> None:
        if self._has_body:
            self._flush()
        while self._path and self._path[-1][0] >= level:
            self._path.pop()
        title = " ".join(unicodedata.normalize("NFKC", title).split())
        if title:
            self._path.append((level, title))
        self._lines.append(line)

    def body(self, line: str) -> None:
        self._lines.append(line)
        if line.strip():
            self._has_body = True

    def finish(self) -> list[ParsedSection]:
        self._flush()
        return self.sections

    def _flush(self) -> None:
        text = "\n".join(self._lines)
        if text.strip():
            self.sections.append(ParsedSection(text, section=self._label()))
        self._lines = []
        self._has_body = False

    def _label(self) -> str | None:
        label = " > ".join(title for _level, title in self._path)
        if len(label.encode()) > MAX_SECTION_LABEL_BYTES:
            # 过长时保留最具体的末端标题。
            budget = MAX_SECTION_LABEL_BYTES - len("…".encode())
            tail: list[str] = []
            for character in reversed(label):
                budget -= len(character.encode())
                if budget < 0:
                    break
                tail.append(character)
            label = "…" + "".join(reversed(tail))
        return label or None


class KnowledgeDocumentParser:
    """确定性文本解析器；不调用 LLM、视觉模型或 OCR。Markdown/DOCX 按标题切分并记录标题路径。"""

    VERSION = "parser-v2"
    MAX_CHARACTERS = 2_000_000
    MAX_PAGES = 1000
    MAX_DOCX_ENTRIES = 5000
    MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
    MIME_SUFFIXES = {
        "text/plain": {".txt", ".text"},
        "text/markdown": {".md", ".markdown"},
        "text/csv": {".csv"},
        "application/pdf": {".pdf"},
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    }

    def parse(self, content: bytes, *, mimetype: str, filename: str) -> list[ParsedSection]:
        suffix = PurePath(filename).suffix.lower()
        allowed_suffixes = self.MIME_SUFFIXES.get(mimetype)
        if allowed_suffixes is None:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_UNSUPPORTED", "当前知识库版本不支持该文档格式")
        if suffix not in allowed_suffixes:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TYPE_MISMATCH", "文档 MIME 与扩展名不一致")
        if mimetype == "application/pdf" and not content.startswith(b"%PDF-"):
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TYPE_MISMATCH", "PDF 文件签名无效")
        if mimetype.endswith("wordprocessingml.document") and not content.startswith(b"PK"):
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TYPE_MISMATCH", "DOCX 文件签名无效")
        if mimetype == "text/markdown":
            sections = self._parse_markdown(self._decode_text(content))
        elif mimetype == "text/plain":
            sections = [ParsedSection(self._decode_text(content))]
        elif mimetype == "text/csv":
            sections = [ParsedSection(self._parse_csv(content))]
        elif mimetype == "application/pdf":
            sections = self._parse_pdf(content)
        elif mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            sections = self._parse_docx(content)
        else:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_UNSUPPORTED", "当前知识库版本不支持该文档格式")
        normalized = []
        for section in sections:
            if "\x00" in section.text:
                raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "文档正文包含不支持的 NUL 字符")
            text = self._normalize_text(section.text)
            if text:
                normalized.append(ParsedSection(text, section.page, section.section))
        total_characters = sum(len(section.text) for section in normalized)
        if not normalized or total_characters == 0:
            code = (
                "KNOWLEDGE_SCANNED_DOCUMENT_UNSUPPORTED"
                if mimetype == "application/pdf"
                else "KNOWLEDGE_DOCUMENT_EMPTY"
            )
            summary = (
                "PDF 未检测到文字层，知识库 v1 不支持 OCR" if mimetype == "application/pdf" else "文档未包含可索引文本"
            )
            raise KnowledgeParseError(code, summary)
        if total_characters > self.MAX_CHARACTERS:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TOO_LARGE", "文档解压后的文本内容超过处理上限")
        return normalized

    @staticmethod
    def _decode_text(content: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_ENCODING_UNSUPPORTED", "文本文件必须使用 UTF-8 编码")

    def _parse_csv(self, content: bytes) -> str:
        decoded = self._decode_text(content)
        try:
            rows = csv.reader(io.StringIO(decoded, newline=""))
            return "\n".join("\t".join(cell.strip() for cell in row) for row in rows)
        except csv.Error as exc:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "CSV 文档格式无效") from exc

    def _parse_pdf(self, content: bytes) -> list[ParsedSection]:
        import PyPDF2

        try:
            reader = PyPDF2.PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_ENCRYPTED", "不支持加密 PDF")
            if len(reader.pages) > self.MAX_PAGES:
                raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TOO_LARGE", "PDF 页数超过处理上限")
            return [ParsedSection(page.extract_text() or "", page=index) for index, page in enumerate(reader.pages, 1)]
        except KnowledgeParseError:
            raise
        except Exception as exc:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "PDF 文档无法解析") from exc

    @staticmethod
    def _parse_markdown(text: str) -> list[ParsedSection]:
        builder = _HeadingSections()
        fence: str | None = None
        for line in text.splitlines():
            fence_match = _MARKDOWN_FENCE.match(line)
            if fence_match:
                marker = fence_match.group(1)
                if fence is None:
                    fence = marker[0] * 3
                elif marker.startswith(fence):
                    fence = None
                builder.body(line)
                continue
            heading = None if fence is not None else _MARKDOWN_HEADING.match(line)
            if heading:
                builder.heading(len(heading.group(1)), heading.group(2), line)
            else:
                builder.body(line)
        return builder.finish()

    @classmethod
    def _parse_docx(cls, content: bytes) -> list[ParsedSection]:
        import docx
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        try:
            cls._validate_docx_archive(content)
            document = docx.Document(io.BytesIO(content))
            builder = _HeadingSections()
            # 按正文顺序遍历段落与表格，表格留在所属标题下。
            for element in document.element.body.iterchildren():
                if element.tag == qn("w:p"):
                    paragraph = Paragraph(element, document)
                    level = cls._docx_heading_level(paragraph)
                    if level is None:
                        builder.body(paragraph.text)
                    else:
                        builder.heading(level, paragraph.text, paragraph.text)
                elif element.tag == qn("w:tbl"):
                    for row in Table(element, document).rows:
                        # 制表符会被正文归一化折叠成空格，用竖线保留列边界。
                        builder.body(" | ".join(cell.text for cell in row.cells))
            return builder.finish()
        except KnowledgeParseError:
            raise
        except Exception as exc:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "DOCX 文档无法解析") from exc

    @staticmethod
    def _docx_heading_level(paragraph) -> int | None:
        style_name = str(getattr(paragraph.style, "name", "") or "").strip()
        if style_name.lower() == "title":
            return 0
        match = _DOCX_HEADING_STYLE.match(style_name)
        if match:
            return int(match.group(1))
        from docx.oxml.ns import qn

        properties = paragraph._p.pPr
        outline = properties.find(qn("w:outlineLvl")) if properties is not None else None
        if outline is not None:
            value = outline.get(qn("w:val"))
            # outlineLvl 0-8 是大纲级别，9 表示正文。
            if value is not None and value.isdigit() and int(value) < 9:
                return int(value) + 1
        return None

    @classmethod
    def _validate_docx_archive(cls, content: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > cls.MAX_DOCX_ENTRIES:
                    raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TOO_LARGE", "DOCX 文件条目数超过处理上限")
                if any(entry.flag_bits & 0x1 for entry in entries):
                    raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_ENCRYPTED", "不支持加密 DOCX")
                if sum(entry.file_size for entry in entries) > cls.MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_TOO_LARGE", "DOCX 解压大小超过处理上限")
        except KnowledgeParseError:
            raise
        except (zipfile.BadZipFile, OSError) as exc:
            raise KnowledgeParseError("KNOWLEDGE_DOCUMENT_INVALID", "DOCX 文档无法解析") from exc

    @staticmethod
    def _normalize_text(text: str) -> str:
        normalized = unicodedata.normalize("NFKC", text).replace("\r\n", "\n").replace("\r", "\n")
        lines = [" ".join(line.split()) for line in normalized.split("\n")]
        return "\n".join(lines).strip()
