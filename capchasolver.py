"""
CAPTCHA Solver Module & CLI for EPFO Scraper
"""
from __future__ import annotations

import sys
from pathlib import Path


CHINESE_TO_ALPHANUM = {
    "工": "I",
    "了": "7",  # or J
    "。": "O",
    "〇": "O",
    "一": "1",
    "二": "2",
    "三": "3",
    "十": "T",
    "卜": "Y",
    "人": "A",
    "入": "A",
    "大": "A",
    "口": "O",
    "日": "D",
    "中": "O",
    "目": "8",
    "月": "H",
    "又": "Y",
    "上": "L",
    "下": "T",
    "么": "Y",
    "夕": "Y",
    "门": "M",
    "己": "2",
    "已": "2",
    "与": "Y",
    "才": "T",
    "广": "L",
    "小": "Y",
    "不": "T",
    "木": "T",
    "太": "A",
    "犬": "A",
    "尤": "Y",
    "止": "L",
    "少": "Y",
    "中": "O",
    "贝": "B",
    "见": "R",
    "内": "N",
    "几": "J",
    "风": "A",
    "丹": "D",
    "乌": "U",
    "勾": "6",
    "勿": "N",
    "匀": "9",
    "化": "H",
    "代": "A",
    "付": "F",
    "们": "M",
    "白": "B",
    "向": "H",
    "后": "H",
    "同": "0",
    "名": "N",
    "因": "O",
    "回": "O",
    "囡": "O",
    "团": "O",
    "国": "O",
    "困": "O",
    "四": "4",
    "圭": "E",
    "地": "D",
    "在": "A",
    "士": "T",
    "壬": "T",
    "壮": "H",
    "声": "R",
    "壳": "8",
    "处": "A",
    "备": "B",
    "复": "R",
    "外": "H",
    "多": "D",
    "夜": "Y",
    "天": "T",
    "夫": "F",
    "失": "Y",
    "头": "Y",
    "夷": "Y",
    "夺": "Y",
    "奇": "Y",
    "奈": "N",
    "奉": "F",
    "奎": "K",
    "奏": "Z",
    "套": "T",
    "奥": "A",
    "女": "N",
    "奴": "N",
    "奶": "N",
    "好": "H",
    "如": "R",
    "妥": "T",
    "妹": "M",
    "妻": "Q",
    "姑": "G",
    "姓": "X",
    "委": "W",
    "威": "W",
    "娱": "Y",
    "婆": "P",
    "媚": "M",
    "嫁": "J",
    "门": "M",
    "子": "2",
    "字": "2",
    "孙": "S",
    "学": "X",
    "孩": "H",
    "宇": "U",
    "守": "S",
    "安": "A",
    "宋": "S",
    "完": "W",
    "宏": "H",
    "官": "G",
    "宜": "E",
    "宝": "B",
    "实": "S",
    "审": "S",
    "客": "K",
    "室": "S",
    "宫": "G",
    "宰": "Z",
    "害": "H",
    "宴": "Y",
    "家": "J",
    "容": "R",
    "宽": "K",
    "宾": "B",
    "宿": "S",
    "富": "F",
    "寒": "H",
    "寓": "Y",
    "寸": "C",
    "对": "D",
    "寺": "S",
    "寻": "X",
    "导": "D",
    "寿": "S",
    "封": "F",
    "射": "S",
    "将": "J",
    "尊": "Z",
    "尔": "2",
    "尖": "J",
    "尚": "S",
    "尤": "Y",
    "尸": "S",
    "尹": "Y",
    "尺": "C",
    "尼": "N",
    "尽": "J",
    "尾": "W",
    "尿": "N",
    "局": "J",
    "屁": "P",
    "层": "C",
    "屋": "W",
    "展": "Z",
    "山": "W",
    "岂": "Q",
    "岩": "A",
    "岭": "L",
    "岛": "D",
    "崔": "C",
    "川": "I",
    "州": "Z",
    "巡": "X",
    "左": "Z",
    "巧": "Q",
    "巴": "B",
    "币": "B",
    "市": "S",
    "帅": "S",
    "帆": "F",
    "师": "S",
    "希": "X",
    "帝": "D",
    "带": "D",
    "帮": "B",
    "常": "C",
    "干": "F",
    "平": "P",
    "年": "N",
    "并": "B",
    "幸": "X",
    "庄": "Z",
    "庆": "Q",
    "床": "C",
    "序": "X",
    "库": "K",
    "应": "Y",
    "底": "D",
    "店": "D",
    "庙": "M",
    "府": "F",
    "度": "D",
    "座": "Z",
    "庭": "T",
    "康": "K",
    "庸": "Y",
    "廉": "L",
    "延": "Y",
    "建": "J",
    "开": "K",
    "弁": "B",
    "异": "Y",
    "弃": "Q",
    "弄": "N",
    "弊": "B",
    "式": "S",
    "弓": "3",
    "引": "Y",
    "弗": "F",
    "弘": "H",
    "弟": "D",
    "张": "Z",
    "弥": "M",
    "弯": "W",
    "弱": "R",
    "强": "Q",
    "归": "G",
    "当": "D",
    "录": "L",
    "形": "X",
    "彦": "Y",
    "彩": "C",
    "彭": "P",
    "彰": "Z",
    "影": "Y",
    "役": "Y",
    "彼": "B",
    "往": "W",
    "征": "Z",
    "待": "D",
    "律": "L",
    "后": "H",
    "徐": "X",
    "得": "D",
    "御": "Y",
    "循": "X",
    "微": "W",
    "德": "D",
    "心": "X",
    "必": "B",
    "意": "Y",
    "志": "Z",
    "忘": "W",
    "忙": "M",
    "忠": "Z",
    "快": "K",
    "怀": "H",
    "态": "T",
    "念": "N",
    "阳": "A",
}


def _clean_ocr_result(text: str) -> str:
    # Map lookalike unicode/Chinese characters to alphanumeric
    translated = "".join(CHINESE_TO_ALPHANUM.get(ch, ch) for ch in text)
    # Filter non-alphanumeric and return uppercase
    return "".join(ch for ch in translated if ch.isascii() and ch.isalnum()).strip().upper()


def solve_captcha(image_data: bytes | str | Path) -> str:
    """
    Solves CAPTCHA from image bytes (in-memory) or image file path.
    Returns the solved 5-character uppercase text string.
    """
    if isinstance(image_data, (str, Path)):
        path = Path(image_data)
        if not path.exists():
            return ""
        try:
            image_bytes = path.read_bytes()
        except Exception:
            return ""
    elif isinstance(image_data, bytes):
        image_bytes = image_data
    else:
        return ""

    # Option 1: ddddocr (popular for simple numeric/alpha CAPTCHAs)
    try:
        import ddddocr  # type: ignore
        import io
        from PIL import Image  # type: ignore

        ocr = ddddocr.DdddOcr(show_ad=False)
        
        # 1. Try raw image first
        res = ocr.classification(image_bytes)
        if res and isinstance(res, str):
            clean_text = _clean_ocr_result(res)
            if len(clean_text) == 5:
                return clean_text

        # 2. Try thresholding (grayscale binarization) at multiple levels
        try:
            img = Image.open(io.BytesIO(image_bytes))
            for thresh in [120, 100, 140, 80, 160]:
                gray = img.convert('L')
                bin_img = gray.point(lambda p: 0 if p < thresh else 255, '1')
                buf = io.BytesIO()
                bin_img.save(buf, format='PNG')
                res = ocr.classification(buf.getvalue())
                if res and isinstance(res, str):
                    clean_text = _clean_ocr_result(res)
                    if len(clean_text) == 5:
                        return clean_text
        except Exception:
            pass
    except Exception:
        pass

    # Option 2: pytesseract (requires Tesseract-OCR installed)
    try:
        import io
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, config="--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
        clean_text = _clean_ocr_result(text)
        if len(clean_text) == 5:
            return clean_text
    except Exception:
        pass

    # Option 3: easyocr
    try:
        import io
        import easyocr  # type: ignore

        reader = easyocr.Reader(["en"], gpu=False)
        results = reader.readtext(image_bytes, detail=0)
        if results:
            clean_text = _clean_ocr_result(results[0])
            if len(clean_text) == 5:
                return clean_text
    except Exception:
        pass

    return ""



if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python capchasolver.py <path_to_captcha_image>")
        sys.exit(1)

    target_path = Path(sys.argv[1])
    result = solve_captcha(target_path)
    if result:
        print(f"Solved CAPTCHA: {result}")
    else:
        print("Could not automatically solve CAPTCHA. The image may be too noisy, the text length may not be exactly 5, or the OCR failed.")
