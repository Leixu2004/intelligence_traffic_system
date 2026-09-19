import re

class PlateValidator:
    def __init__(self):
        """初始化车牌校验模块"""
        # 放宽校验：匹配中国大陆车牌，或者纯字母数字组成的测试车牌（5到8位）
        self.pattern = re.compile(
            r'(?:[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领][A-Z][A-Z0-9]{5,6}|[A-Z0-9]{5,8})',
            re.IGNORECASE,
        )
        
    def is_valid(self, plate_text):
        """
        校验识别出的车牌文本是否符合标准格式
        :param plate_text: OCR识别出的文本
        :return: bool 是否合法
        """
        if not plate_text:
            return False
            
        # 去除空格等特殊符号，以防OCR带有杂讯
        clean_text = "".join(plate_text.split())
        
        # 使用正则表达式匹配
        return bool(self.pattern.fullmatch(clean_text))
