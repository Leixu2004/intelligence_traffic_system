"""Keep the final report summary together after the appended 2026-09-10 section."""

from docx import Document


REPORT_PATH = r"D:\intelligent_transportation\人工智能智慧交通实训报告_新.docx"


def main() -> None:
    doc = Document(REPORT_PATH)
    for paragraph in doc.paragraphs:
        if paragraph.text.strip() == "三、 实训总结与心得体会":
            paragraph.paragraph_format.page_break_before = True
            break
    else:
        raise RuntimeError("Summary heading not found")
    doc.save(REPORT_PATH)
    print(REPORT_PATH)


if __name__ == "__main__":
    main()

