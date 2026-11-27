=========================================================
QUIZHELPER AI - TRỢ LÝ TRẮC NGHIỆM WINDOWS PORTABLE
=========================================================

1. CÁCH ĐÓNG GÓI RA FILE QuizHelper.exe (PORTABLE)
- Cách 1 (Tự động trên Cloud qua GitHub Actions - không cần cài gì trên máy):
  + Tạo một repository mới trên GitHub và đẩy toàn bộ thư mục này lên nhánh main.
  + Vào tab [Actions] -> Chọn workflow [Build QuizHelper Windows Portable] -> Bấm [Run workflow].
  + Sau ~3 phút, tải về file artifact [QuizHelper-Portable.zip] đã đóng gói sẵn QuizHelper.exe và Tesseract OCR tiếng Việt/Anh.

- Cách 2 (Build trực tiếp trên máy Windows 10/11):
  + Cài Python 3.11+ và Tesseract OCR (mặc định tại C:\Program Files\Tesseract-OCR).
  + Mở PowerShell tại thư mục này và chạy: .\build_portable.ps1
  + Kết quả xuất ra file QuizHelper-Portable.zip hoàn chỉnh.

2. SỬ DỤNG ỨNG DỤNG PORTABLE
- Giải nén QuizHelper-Portable.zip và chạy trực tiếp QuizHelper.exe.
- Mở tab [Cài đặt]: Nhập OpenAI API Key -> Bấm [Kiểm tra kết nối API] -> Bấm [Lưu cài đặt].
- Mở tab [Quản lý Tài liệu]: Import file PDF, DOCX, DOC, XLSX, XLS, TXT, MD.
- Mở bài kiểm tra trên trình duyệt và nhấn Ctrl + 1 để bắt đầu quét (Ctrl + 2 để dừng).
