$ErrorActionPreference = "Stop"

Write-Host "=== 1. Tạo môi trường ảo & cài đặt thư viện ===" -ForegroundColor Cyan
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

Write-Host "=== 2. Đóng gói QuizHelper.exe bằng PyInstaller ===" -ForegroundColor Cyan
pyinstaller --clean --noconfirm quizhelper.spec

Write-Host "=== 3. Khởi tạo cấu trúc thư mục QuizHelper-Portable ===" -ForegroundColor Cyan
$PortableDir = "QuizHelper-Portable"
if (Test-Path $PortableDir) { Remove-Item $PortableDir -Recurse -Force }
New-Item -ItemType Directory -Path "$PortableDir\documents" | Out-Null
New-Item -ItemType Directory -Path "$PortableDir\data" | Out-Null
New-Item -ItemType Directory -Path "$PortableDir\logs" | Out-Null
New-Item -ItemType Directory -Path "$PortableDir\config" | Out-Null
New-Item -ItemType Directory -Path "$PortableDir\tesseract\tessdata" | Out-Null

Copy-Item "dist\QuizHelper.exe" -Destination "$PortableDir\QuizHelper.exe"

Write-Host "=== 4. Tải dữ liệu ngôn ngữ OCR Tiếng Việt & Tiếng Anh ===" -ForegroundColor Cyan
Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/vie.traineddata" -OutFile "$PortableDir\tesseract\tessdata\vie.traineddata"
Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/eng.traineddata" -OutFile "$PortableDir\tesseract\tessdata\eng.traineddata"

$SystemTesseract = "C:\Program Files\Tesseract-OCR"
if (Test-Path $SystemTesseract) {
    Copy-Item "$SystemTesseract\*.exe" -Destination "$PortableDir\tesseract\"
    Copy-Item "$SystemTesseract\*.dll" -Destination "$PortableDir\tesseract\"
} else {
    Write-Warning "Chưa thấy Tesseract tại C:\Program Files\Tesseract-OCR. Hãy copy tesseract.exe và các file .dll vào $PortableDir\tesseract\ trước khi nén."
}

Copy-Item "README.txt" -Destination "$PortableDir\README.txt"

Write-Host "=== 5. Đóng gói QuizHelper-Portable.zip ===" -ForegroundColor Cyan
if (Test-Path "QuizHelper-Portable.zip") { Remove-Item "QuizHelper-Portable.zip" -Force }
Compress-Archive -Path $PortableDir -DestinationPath "QuizHelper-Portable.zip"

Write-Host "HOÀN TẤT! File bàn giao nằm tại: QuizHelper-Portable.zip" -ForegroundColor Green
