import time
import logging
from typing import List
import pyautogui
from src.ocr_scanner import OptionBox

logger = logging.getLogger("AutoClicker")
pyautogui.FAILSAFE = True

FORBIDDEN_KEYWORDS = ["submit", "nộp bài", "hoàn thành", "finish", "kết thúc", "gửi bài"]


class AutoClicker:
    @staticmethod
    def select_options(selected_labels: List[str], option_boxes: List[OptionBox]) -> List[str]:
        clicked = []
        box_map = {opt.label.upper(): opt for opt in option_boxes}

        for label in selected_labels:
            lbl = label.strip().upper()
            box = box_map.get(lbl)
            if not box:
                logger.warning(f"Không tìm thấy tọa độ cho phương án {lbl}")
                continue

            opt_lower = box.text.lower()
            if any(kw in opt_lower for kw in FORBIDDEN_KEYWORDS):
                logger.warning(f"Chặn click vì phương án {lbl} chứa từ khóa nộp bài: {box.text}")
                continue

            x, y = box.center
            screen_w, screen_h = pyautogui.size()
            if not (10 <= x <= screen_w - 10 and 10 <= y <= screen_h - 10):
                logger.warning(f"Tọa độ ({x}, {y}) nằm ngoài vùng an toàn.")
                continue

            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click()
            clicked.append(lbl)
            logger.info(f"Đã tự động chọn đáp án {lbl} tại tọa độ ({x}, {y}).")
            time.sleep(0.25)

        return clicked
