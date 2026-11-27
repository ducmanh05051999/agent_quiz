import os
import re
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import numpy as np
import mss
from PIL import Image
import pytesseract
from pytesseract import Output

logger = logging.getLogger("OCRScanner")


class OptionBox:
    def __init__(
        self,
        label: str,
        text: str,
        bbox: Tuple[int, int, int, int],
        click_point: Optional[Tuple[int, int]] = None,
    ):
        self.label = label.upper()
        self.text = text
        self.bbox = bbox
        self._click_point = click_point

    @property
    def center(self) -> Tuple[int, int]:
        if self._click_point:
            return self._click_point
        l, t, w, h = self.bbox
        return (l + min(25, w // 3), t + min(18, h // 2))


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

    def _detect_checkboxes(self, img: Image.Image, offset_x: int, offset_y: int) -> List[Tuple[int, int, int, int]]:
        """Tìm tọa độ (left, top, width, height) các ô checkbox hình vuông trên ảnh."""
        gray = np.asarray(img.convert("L"), dtype=np.int16)
        gx = np.abs(np.diff(gray, axis=1))
        gy = np.abs(np.diff(gray, axis=0))

        # Hỗ trợ cả viền checkbox tương phản thấp (như nền hồng nhạt / xám nhạt)
        edge_h = (gy[:, :-1] >= 6).astype(np.uint8)
        edge_v = (gx[:-1, :] >= 6).astype(np.uint8)

        boxes: List[Tuple[int, int, int, int]] = []
        h, w = edge_h.shape
        # Chỉ quét nửa trái màn hình nơi đặt checkbox đáp án
        scan_w = int(w * 0.45)

        # Tìm các đoạn nét ngang dài 11..28px đóng vai trò cạnh trên của checkbox
        for y in range(4, h - 30):
            row = edge_h[y, :scan_w]
            if row.sum() < 11:
                continue
            padded = np.concatenate(([0], row, [0]))
            diffs = np.diff(padded)
            starts = np.where(diffs == 1)[0]
            ends = np.where(diffs == -1)[0]

            for s, e in zip(starts, ends):
                bw = e - s
                if not (11 <= bw <= 28):
                    continue
                # Kiểm tra cạnh dưới cách y từ 11..28px và 2 cạnh bên trái/phải
                for bh in range(max(11, bw - 4), min(29, bw + 5)):
                    if y + bh >= h:
                        break
                    bottom_density = edge_h[y + bh, s:e].mean()
                    left_density = edge_v[y:y + bh, max(0, s - 1):s + 2].max(axis=1).mean()
                    right_density = edge_v[y:y + bh, max(0, e - 2):min(w, e + 1)].max(axis=1).mean()
                    if bottom_density >= 0.65 and left_density >= 0.65 and right_density >= 0.65:
                        boxes.append((int(s + offset_x), int(y + offset_y), int(bw), int(bh)))
                        break

        # Khử trùng lặp các box sát nhau
        filtered: List[Tuple[int, int, int, int]] = []
        for b in sorted(boxes, key=lambda item: (item[1], item[0])):
            if any(abs(b[0] - f[0]) < 12 and abs(b[1] - f[1]) < 12 for f in filtered):
                continue
            filtered.append(b)
        return filtered

    def scan_screen(self) -> Optional[ScreenQuizData]:
        img, offset_x, offset_y = self.capture_browser_region()
        checkboxes = self._detect_checkboxes(img, offset_x, offset_y)
        data = pytesseract.image_to_data(img, lang=self.lang, output_type=Output.DICT)

        words = []
        n = len(data["text"])
        for i in range(n):
            word = data["text"][i].strip()
            conf = int(data["conf"][i])
            if not word or conf < 35:
                continue
            l = data["left"][i] + offset_x
            t = data["top"][i] + offset_y
            w = data["width"][i]
            h = data["height"][i]
            words.append({
                "text": word,
                "left": l,
                "top": t,
                "right": l + w,
                "bottom": t + h,
                "cy": t + h / 2.0,
                "height": h,
            })

        if not words:
            return None

        # Gom các từ nằm cùng hàng ngang thành 1 dòng hoàn chỉnh (không bị tách bởi block_num)
        words.sort(key=lambda w: (w["cy"], w["left"]))
        visual_lines: List[Dict] = []
        for w in words:
            if not visual_lines:
                visual_lines.append([w])
                continue
            prev_line = visual_lines[-1]
            prev_cy = sum(item["cy"] for item in prev_line) / len(prev_line)
            tol = max(12.0, w["height"] * 0.65)
            if abs(w["cy"] - prev_cy) <= tol:
                prev_line.append(w)
            else:
                visual_lines.append([w])

        merged_lines = []
        noise_markers = (
            "quizhelper ai", "httpx2:", "[info]", "câu hỏi nhận diện từ màn hình",
            "đáp án đề xuất", "trạng thái:", "số vi phạm:", "tự động thu bài"
        )
        for line_words in visual_lines:
            line_words.sort(key=lambda item: item["left"])
            txt = " ".join(item["text"] for item in line_words).strip()
            if any(m in txt.lower() for m in noise_markers):
                continue
            left = min(item["left"] for item in line_words)
            top = min(item["top"] for item in line_words)
            right = max(item["right"] for item in line_words)
            bottom = max(item["bottom"] for item in line_words)
            merged_lines.append({
                "text": txt,
                "bbox": (left, top, right - left, bottom - top),
                "left": left,
                "top": top,
                "bottom": bottom,
                "height": max(14, bottom - top),
            })

        merged_lines.sort(key=lambda x: x["top"])
        result = self._parse_with_checkboxes_or_layout(merged_lines, checkboxes)

        if not result and merged_lines:
            sample = " | ".join(l["text"] for l in merged_lines[:5])[:180]
            if sample != self._last_debug_sig:
                self._last_debug_sig = sample
                logger.info(f"OCR đọc {len(merged_lines)} dòng, {len(checkboxes)} checkbox. Mẫu: {sample}")
        return result

    def _match_checkbox_for_line(
        self, line: Dict, checkboxes: List[Tuple[int, int, int, int]]
    ) -> Optional[Tuple[int, int, int, int]]:
        line_cy = line["top"] + line["height"] / 2.0
        for cb in checkboxes:
            cb_l, cb_t, cb_w, cb_h = cb
            cb_cy = cb_t + cb_h / 2.0
            # Checkbox nằm bên trái đầu dòng (cách 4..75px) và ngang hàng với dòng đầu tiên của đáp án
            if 4 <= (line["left"] - (cb_l + cb_w)) <= 75 and abs(cb_cy - line_cy) <= 18:
                return cb
        return None

    def _parse_with_checkboxes_or_layout(
        self, lines: List[Dict], checkboxes: List[Tuple[int, int, int, int]]
    ) -> Optional[ScreenQuizData]:
        if len(lines) < 3:
            return None

        # Gắn mốc checkbox (nếu tìm thấy) cho từng dòng
        for line in lines:
            line["checkbox"] = self._match_checkbox_for_line(line, checkboxes)

        # Gộp các dòng liên tiếp thành các khối đoạn văn (Paragraph Blocks)
        blocks: List[Dict] = []
        opt_label_re = re.compile(r"^[\(\[\{]?([A-Fa-f])[\)\]\.\:\-\s]+(.+)$")

        for line in lines:
            is_new_option_start = (
                line["checkbox"] is not None
                or opt_label_re.match(line["text"]) is not None
            )
            if not blocks:
                blocks.append({
                    "lines": [line],
                    "checkbox": line["checkbox"],
                    "left": line["left"],
                    "top": line["top"],
                    "bottom": line["bottom"],
                })
                continue

            prev = blocks[-1]
            v_gap = line["top"] - prev["bottom"]
            avg_h = prev["lines"][-1]["height"]

            # Tách đoạn mới nếu có checkbox mới, có nhãn A/B/C/D, hoặc khoảng cách dòng lớn hơn khoảng cách nội đoạn
            if is_new_option_start or v_gap > avg_h * 0.85:
                blocks.append({
                    "lines": [line],
                    "checkbox": line["checkbox"],
                    "left": line["left"],
                    "top": line["top"],
                    "bottom": line["bottom"],
                })
            else:
                prev["lines"].append(line)
                prev["bottom"] = line["bottom"]
                prev["left"] = min(prev["left"], line["left"])

        for b in blocks:
            b["text"] = " ".join(l["text"] for l in b["lines"]).strip()

        # Cách 1: Nhận diện theo các khối có gắn checkbox làm mốc đáp án
        cb_block_indices = [i for i, b in enumerate(blocks) if b["checkbox"] is not None]
        labels = ["A", "B", "C", "D", "E", "F"]

        if len(cb_block_indices) >= 2:
            first_cb_idx = cb_block_indices[0]
            q_blocks = [blocks[i]["text"] for i in range(max(0, first_cb_idx - 2), first_cb_idx)]
            question_text = " ".join(q_blocks).strip()

            options: List[OptionBox] = []
            for opt_i, b_idx in enumerate(cb_block_indices[:6]):
                blk = blocks[b_idx]
                cb_l, cb_t, cb_w, cb_h = blk["checkbox"]
                first_l = blk["lines"][0]
                bbox = (first_l["left"], first_l["top"], first_l["bbox"][2], blk["bottom"] - blk["top"])
                click_pt = (cb_l + cb_w // 2, cb_t + cb_h // 2)
                options.append(OptionBox(label=labels[opt_i], text=blk["text"], bbox=bbox, click_point=click_pt))

            if len(question_text) >= 10 and len(options) >= 2:
                return self._build_result(question_text, options)

        # Cách 2: Nhận diện theo độ thụt lề khối đáp án (khi checkbox quá mờ hoặc không có nhãn A/B/C/D)
        q_block_idx = -1
        for i, b in enumerate(blocks):
            txt = b["text"]
            if len(txt) >= 15 and (txt.endswith("?") or txt.endswith(":") or re.match(r"^(Câu|Question)\s*\d+", txt, re.I)):
                q_block_idx = i
                break

        if q_block_idx != -1 and len(blocks) - (q_block_idx + 1) >= 2:
            question_text = blocks[q_block_idx]["text"]
            q_left = blocks[q_block_idx]["left"]
            candidate_blocks = blocks[q_block_idx + 1: q_block_idx + 7]

            options = []
            for opt_i, blk in enumerate(candidate_blocks):
                if len(blk["text"]) < 3:
                    continue
                first_l = blk["lines"][0]
                bbox = (first_l["left"], first_l["top"], first_l["bbox"][2], blk["bottom"] - blk["top"])
                # Nếu khối đáp án thụt vào bên phải so với câu hỏi, suy ra tâm ô checkbox nằm ngay bên trái dòng đầu
                if blk["left"] - q_left >= 15:
                    click_pt = (max(15, blk["left"] - 22), first_l["top"] + first_l["height"] // 2)
                else:
                    click_pt = (first_l["left"] + 15, first_l["top"] + first_l["height"] // 2)
                options.append(OptionBox(label=labels[opt_i], text=blk["text"], bbox=bbox, click_point=click_pt))

            if len(options) >= 2:
                return self._build_result(question_text, options)

        return None

    def _build_result(self, question_text: str, options: List[OptionBox]) -> ScreenQuizData:
        q_lower = question_text.lower()
        is_multi = any(kw in q_lower for kw in [
            "chọn nhiều", "những đáp án", "các đáp án", "multiple", "select all", "chọn tất cả"
        ])
        q_type = "multiple_choice" if is_multi else "single_choice"

        normalized = re.sub(
            r"\s+", " ", f"{question_text}|{'|'.join(o.label + ':' + o.text for o in options)}"
        ).lower()
        content_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

        return ScreenQuizData(
            question=question_text,
            options=options,
            question_type=q_type,
            content_hash=content_hash,
        )
