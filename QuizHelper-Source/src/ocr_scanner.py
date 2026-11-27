import os
import re
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import mss
from PIL import Image
import pytesseract
from pytesseract import Output

logger = logging.getLogger("OCRScanner")


class OptionBox:
    def __init__(self, label: str, text: str, bbox: Tuple[int, int, int, int]):
        self.label = label.upper()
        self.text = text
        self.bbox = bbox

    @property
    def center(self) -> Tuple[int, int]:
        l, t, w, h = self.bbox
        return (l + min(25, w // 3), t + h // 2)


class ScreenQuizData:
    def __init__(self, question: str, options: List[OptionBox], question_type: str, content_hash: str):
        self.question = question
        self.options = options
        self.question_type = question_type
        self.content_hash = content_hash


class OCRScanner:
    def __init__(self, tesseract_dir: Path, lang: str = "vie+eng"):
        self.lang = lang
        exe_path = tesseract_dir / "tesseract.exe"
        tessdata_path = tesseract_dir / "tessdata"
        if exe_path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(exe_path)
        if tessdata_path.exists():
            os.environ["TESSDATA_PREFIX"] = str(tessdata_path)

    def capture_browser_region(self) -> Tuple[Image.Image, int, int]:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            top_crop = int(monitor["height"] * 0.11)
            bottom_crop = int(monitor["height"] * 0.05)
            region = {
                "left": monitor["left"],
                "top": monitor["top"] + top_crop,
                "width": monitor["width"],
                "height": monitor["height"] - top_crop - bottom_crop,
            }
            raw = sct.grab(region)
            img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
            return img, region["left"], region["top"]

    def scan_screen(self) -> Optional[ScreenQuizData]:
        img, offset_x, offset_y = self.capture_browser_region()
        data = pytesseract.image_to_data(img, lang=self.lang, output_type=Output.DICT)

        lines_map: Dict[Tuple[int, int, int], List[Dict]] = {}
        n = len(data["text"])
        for i in range(n):
            word = data["text"][i].strip()
            conf = int(data["conf"][i])
            if not word or conf < 40:
                continue
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines_map.setdefault(key, []).append({
                "text": word,
                "left": data["left"][i] + offset_x,
                "top": data["top"][i] + offset_y,
                "right": data["left"][i] + data["width"][i] + offset_x,
                "bottom": data["top"][i] + data["height"][i] + offset_y,
            })

        merged_lines = []
        for words in lines_map.values():
            words.sort(key=lambda w: w["left"])
            line_text = " ".join(w["text"] for w in words)
            left = min(w["left"] for w in words)
            top = min(w["top"] for w in words)
            right = max(w["right"] for w in words)
            bottom = max(w["bottom"] for w in words)
            merged_lines.append({
                "text": line_text,
                "bbox": (left, top, right - left, bottom - top),
                "top": top
            })
        merged_lines.sort(key=lambda x: x["top"])

        return self._parse_quiz_structure(merged_lines)

    def _parse_quiz_structure(self, lines: List[Dict]) -> Optional[ScreenQuizData]:
        option_pattern = re.compile(r"^[\(\[\{]?([A-Fa-f])[\)\]\.\:\-\s]+(.+)$")
        option_indices = []

        for idx, line in enumerate(lines):
            m = option_pattern.match(line["text"].strip())
            if m:
                option_indices.append((idx, m.group(1).upper(), m.group(2).strip(), line["bbox"]))

        if len(option_indices) < 2:
            return None

        first_opt_idx = option_indices[0][0]
        start_q_idx = max(0, first_opt_idx - 8)
        question_lines = [lines[i]["text"] for i in range(start_q_idx, first_opt_idx)]
        question_text = " ".join(question_lines).strip()

        if len(question_text) < 10:
            return None

        options: List[OptionBox] = []
        seen_labels = set()
        for _, label, opt_text, bbox in option_indices:
            if label not in seen_labels:
                seen_labels.add(label)
                options.append(OptionBox(label=label, text=opt_text, bbox=bbox))

        q_lower = question_text.lower()
        is_multi = any(kw in q_lower for kw in [
            "chọn nhiều", "những đáp án", "các đáp án", "multiple", "select all", "chọn tất cả"
        ])
        q_type = "multiple_choice" if is_multi else "single_choice"

        normalized = re.sub(r"\s+", " ", f"{question_text}|{'|'.join(o.label + ':' + o.text for o in options)}").lower()
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

        return ScreenQuizData(
            question=question_text,
            options=options,
            question_type=q_type,
            content_hash=content_hash
        )
