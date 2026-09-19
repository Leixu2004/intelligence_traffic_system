import os, sys
sys.stdout.reconfigure(encoding='utf-8')
import docx

doc_path = '人工智能智慧交通实训报告_新.docx'
doc = docx.Document(doc_path)
print('Current paragraphs:', len(doc.paragraphs))
