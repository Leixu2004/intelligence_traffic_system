import docx

doc = docx.Document('D:/实训报告以及相关/实训报告.docx')
for i, p in enumerate(doc.paragraphs):
    if p.text.strip():
        print(f"[{i}] {p.text.strip()}")
