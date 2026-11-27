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
        self._last_debug_sig = ""
        exe_path = tesseract_dir / "tesseract.exe"
        tessdata_path = tesseract_dir / "tessdata"
        if exe_path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(exe_path)
        if tessdata_path.exists():
            os.environ["TESSDATA_PREFIX"] = str(tessdata_path)

    def capture_browser_region(self) -> Tuple[Image.Image, int, int]:
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            top_crop = int(monitor["height"] * 0.10)
            bottom_crop = int(monitor["height"] * 0.04)
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
            if not word or conf < 35:
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
                "top": top,
                "left": left,
            })
        merged_lines.sort(key=lambda x: (x["top"], x["left"]))

        # Gộp các nhãn A/B/C/D đứng riêng 1 khối bên trái với nội dung đáp án ngang hàng
        merged_lines = self._merge_split_option_labels(merged_lines)

        result = self._parse_quiz_structure(merged_lines)
        if not result and merged_lines:
            sample = " | ".join(l["text"] for l in merged_lines[:6])[:180]
            if sample != self._last_debug_sig:
                self._last_debug_sig = sample
                logger.info(f"OCR đã đọc {len(merged_lines)} dòng nhưng chưa khớp mẫu câu hỏi. Mẫu: {sample}")
        return result

    def _merge_split_option_labels(self, lines: List[Dict]) -> List[Dict]:
        label_only = re.compile(r"^[\(\[\{]?([A-Fa-f])[\)\]\.\:\-]?$")
        used = set()
        combined = []
        for i, line in enumerate(lines):
            if i in used:
                continue
            m = label_only.match(line["text"].strip())
            if m and i + 1 < len(lines):
                nxt = lines[i + 1]
                if abs(nxt["top"] - line["top"]) <= 28 and nxt["left"] > line["left"]:
                    l = min(line["bbox"][0], nxt["bbox"][0])
                    t = min(line["bbox"][1], nxt["bbox"][1])
                    r = max(line["bbox"][0] + line["bbox"][2], nxt["bbox"][0] + nxt["bbox"][2])
                    b = max(line["bbox"][1] + line["bbox"][3], nxt["bbox"][1] + nxt["bbox"][3])
                    combined.append({
                        "text": f"{m.group(1).upper()}. {nxt['text']}",
                        "bbox": (l, t, r - l, b - t),
                        "top": t,
                        "left": l,
                    })
                    used.add(i + 1)
                    continue
            combined.append(line)
        return combined

    def _parse_quiz_structure(self, lines: List[Dict]) -> Optional[ScreenQuizData]:
        option_pattern = re.compile(r"^[\(\[\{]?([A-Fa-f])[\)\]\.\:\-\s]+(.+)$")
        option_indices = []

        for idx, line in enumerate(lines):
            m = option_pattern.match(line["text"].strip())
            if m:
                option_indices.append((idx, m.group(1).upper(), m.group(2).strip(), line["bbox"]))

        options: List[OptionBox] = []
        question_text = ""

        # Trường hợp 1: Có nhãn A/B/C/D rõ ràng
        if len(option_indices) >= 2:
            first_opt_idx = option_indices[0][0]
            start_q_idx = max(0, first_opt_idx - 8)
            question_lines = [lines[i]["text"] for i in range(start_q_idx, first_opt_idx)]
            question_text = " ".join(question_lines).strip()

            seen_labels = set()
            for _, label, opt_text, bbox in option_indices:
                if label not in seen_labels:
                    seen_labels.add(label)
                    options.append(OptionBox(label=label, text=opt_text, bbox=bbox))

        # Trường hợp 2 (Fallback): Giao diện web không có chữ A/B/C/D (chỉ có nút tròn/ô vuông)
        else:
            q_idx = -1
            for idx, line in enumerate(lines):
                txt = line["text"].strip()
                if len(txt) >= 15 and (txt.endswith("?") or txt.endswith(":") or re.match(r"^(Câu|Question)\s*\d+", txt, re.I)):
                    q_idx = idx
                    break
            if q_idx != -1 and len(lines) - (q_idx + 1) >= 2:
                question_text = lines[q_idx]["text"].strip()
                candidate_lines = lines[q_idx + 1: q_idx + 6]
                labels = ["A", "B", "C", "D", "E"]
                for i, cand in enumerate(candidate_lines):
                    if len(cand["text"].strip()) >= 2:
                        options.append(OptionBox(label=labels[i], text=cand["text"].strip(), bbox=cand["bbox"]))

        if len(question_text) < 10 or len(options) < 2:
            return None

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
