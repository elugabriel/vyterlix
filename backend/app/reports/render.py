"""Writing a stored report out as a PDF (made from the stored copy, so it never differs from it)."""

from datetime import datetime

from fpdf import FPDF
from fpdf.fonts import FontFace

from app.reports import rules

INK = (26, 31, 43)
MUTED = (91, 100, 117)
HEAD_FILL = (232, 236, 245)
ACCENT = (47, 91, 234)


class _Pdf(FPDF):
    def __init__(self, business: str, orientation: str):
        super().__init__(orientation=orientation, unit="mm", format="A4")
        self.business = rules.latin1(business)
        self.set_auto_page_break(True, margin=16)
        self.set_margins(14, 14, 14)
        self.alias_nb_pages()

    def header(self):
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, self.business, align="L", new_x="LMARGIN", new_y="NEXT")

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, f"Vyterlix - page {self.page_no()} of {{nb}}", align="C")


def _numeric(text: str) -> bool:
    for ch in (",", "%", "£", "-", ".", " points"):
        text = text.replace(ch, "")
    return text.strip().isdigit()


def _table(pdf: _Pdf, table: dict) -> None:
    columns = [rules.latin1(c) for c in table["columns"]]
    rows = [[rules.latin1(c) for c in row] for row in table["rows"]]
    widths = []
    for i, name in enumerate(columns):
        longest = max([len(name), *(len(r[i]) for r in rows)])
        widths.append(min(max(longest, 6), 45))
    total = sum(widths)
    usable = pdf.w - pdf.l_margin - pdf.r_margin
    col_widths = tuple(w / total * usable for w in widths)
    align = tuple(
        "RIGHT"
        if i > 0 and rows and all(_numeric(r[i]) or r[i] in ("", "-", "n/a") for r in rows)
        else "LEFT"
        for i in range(len(columns))
    )
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*INK)
    with pdf.table(
        col_widths=col_widths,
        text_align=align,
        line_height=4.6,
        headings_style=FontFace(emphasis="BOLD", fill_color=HEAD_FILL),
        borders_layout="HORIZONTAL_LINES",
        repeat_headings=1,
    ) as t:
        head = t.row()
        for name in columns:
            head.cell(name)
        for r in rows:
            row = t.row()
            for cell in r:
                row.cell(cell)


def to_pdf(content: dict) -> bytes:
    pdf = _Pdf(content["business"], "L" if rules.landscape(content) else "P")
    pdf.set_title(rules.latin1(content["title"]))
    pdf.set_author("Vyterlix")
    pdf.set_creator("Vyterlix")
    pdf.set_creation_date(datetime.fromisoformat(content["generated_at"]))
    pdf.add_page()
    pdf.set_text_color(*ACCENT)
    pdf.set_font("Helvetica", "B", 20)
    pdf.multi_cell(0, 9, rules.latin1(content["title"]), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 9)
    for fact in content.get("facts", []):
        pdf.multi_cell(0, 5, rules.latin1(fact), new_x="LMARGIN", new_y="NEXT")
    for section in content["sections"]:
        pdf.ln(4)
        pdf.set_text_color(*INK)
        pdf.set_font("Helvetica", "B", 13)
        pdf.multi_cell(0, 7, rules.latin1(section["heading"]), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        for line in section.get("paragraphs", []):
            pdf.multi_cell(0, 5.2, rules.latin1(line), new_x="LMARGIN", new_y="NEXT")
            pdf.ln(1)
        for line in section.get("bullets", []):
            pdf.set_x(pdf.l_margin + 3)
            pdf.multi_cell(0, 5.2, "- " + rules.latin1(line), new_x="LMARGIN", new_y="NEXT")
        if section.get("table"):
            pdf.ln(1)
            _table(pdf, section["table"])
    for note in content.get("notes", []):
        pdf.ln(3)
        pdf.set_text_color(*MUTED)
        pdf.set_font("Helvetica", "I", 8)
        pdf.multi_cell(0, 4.2, rules.latin1(note), new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())
