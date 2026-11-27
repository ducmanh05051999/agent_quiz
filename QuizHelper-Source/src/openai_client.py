import time
import logging
from typing import List, Dict, Literal
from pydantic import BaseModel, Field
from openai import OpenAI, AuthenticationError, RateLimitError, APITimeoutError, APIConnectionError

logger = logging.getLogger("OpenAIClient")


class QuizAnalysisResult(BaseModel):
    question: str = Field(description="Nội dung câu hỏi được phân tích")
    selected_options: List[str] = Field(
        description="Danh sách ký tự đáp án được chọn (ví dụ ['A'] hoặc ['A', 'C']). Rỗng nếu không đủ căn cứ."
    )
    explanation: str = Field(description="Giải thích chi tiết bằng tiếng Việt dựa trên tài liệu.")
    source_document: str = Field(description="Tên file tài liệu nguồn chứa căn cứ, hoặc 'Không tìm thấy'.")
    source_location: str = Field(description="Trang, sheet hoặc đoạn chứa thông tin nguồn.")
    confidence: int = Field(ge=0, le=100, description="Mức độ tin cậy từ 0 đến 100.")
    evidence_status: Literal["SUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"] = Field(
        description="Trạng thái đủ căn cứ hay không đủ căn cứ từ tài liệu."
    )


class QuizOpenAIClient:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", max_tokens: int = 1200):
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.client = OpenAI(api_key=api_key, timeout=25.0)

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Vui lòng nhập OpenAI API Key."
        try:
            self.client.models.retrieve(self.model)
            return True, f"Kết nối thành công! Model '{self.model}' khả dụng."
        except AuthenticationError:
            return False, "API Key không hợp lệ hoặc đã bị thu hồi."
        except RateLimitError:
            return False, "Tài khoản đã hết quota hoặc vượt giới hạn gọi API."
        except Exception as e:
            return False, f"Lỗi kết nối API: {str(e)}"

    def analyze_quiz(
        self,
        question: str,
        options: List[Dict[str, str]],
        question_type: str,
        retrieved_chunks: List[Dict]
    ) -> QuizAnalysisResult:
        if not retrieved_chunks:
            return QuizAnalysisResult(
                question=question,
                selected_options=[],
                explanation="Không có tài liệu nào được lập chỉ mục hoặc không tìm thấy đoạn văn bản liên quan.",
                source_document="Không có",
                source_location="N/A",
                confidence=0,
                evidence_status="INSUFFICIENT_EVIDENCE"
            )

        context_blocks = []
        for idx, ch in enumerate(retrieved_chunks, 1):
            context_blocks.append(
                f"[Nguồn {idx}: {ch['source']} | Vị trí: {ch['location']}]\n{ch['text']}"
            )
        context_str = "\n\n".join(context_blocks)
        options_str = "\n".join(f"{opt['label']}. {opt['text']}" for opt in options)

        system_prompt = (
            "Bạn là trợ lý phân tích câu hỏi trắc nghiệm chính xác tuyệt đối dựa trên tài liệu được cung cấp.\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. Chỉ sử dụng thông tin có trong phần TÀI LIỆU THAM KHẢO để trả lời.\n"
            "2. Tuyệt đối KHÔNG tự suy diễn hoặc bịa đặt thông tin ngoài tài liệu.\n"
            "3. Nếu tài liệu không chứa thông tin để kết luận đáp án, bắt buộc đặt evidence_status = 'INSUFFICIENT_EVIDENCE', "
            "selected_options = [] và confidence <= 30.\n"
            "4. Trích dẫn chính xác tên file và vị trí (Trang/Sheet/Đoạn) từ nguồn tham khảo."
        )

        user_prompt = (
            f"LOẠI CÂU HỎI: {question_type}\n"
            f"CÂU HỎI:\n{question}\n\n"
            f"CÁC PHƯƠNG ÁN:\n{options_str}\n\n"
            f"TÀI LIỆU THAM KHẢO:\n{context_str}"
        )

        last_err = None
        for attempt in range(3):
            try:
                completion = self.client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format=QuizAnalysisResult,
                    temperature=0.0,
                    max_tokens=self.max_tokens,
                )
                parsed = completion.choices[0].message.parsed
                if parsed:
                    return parsed
            except AuthenticationError:
                raise RuntimeError("OpenAI API Key không hợp lệ.")
            except RateLimitError:
                raise RuntimeError("Tài khoản OpenAI hết quota hoặc bị giới hạn tốc độ (Rate Limit).")
            except (APITimeoutError, APIConnectionError) as e:
                last_err = e
                logger.warning(f"Mạng chậm/timeout khi gọi OpenAI (lần {attempt + 1}/3). Đang thử lại...")
                time.sleep(1.5 * (attempt + 1))
            except Exception as e:
                last_err = e
                break

        raise RuntimeError(f"Không thể nhận phản hồi từ OpenAI API: {last_err}")
