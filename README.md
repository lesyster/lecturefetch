# 🎓 LectureFetch — Automated Blackboard Lecture Extractor

<p align="center">
  <img src="social-card.png" alt="LectureFetch Social Card" width="100%">
</p>

<p align="center">
  <a href="https://github.com/lesyster/lecturefetch/releases/latest/download/LectureFetch.exe">
    <img src="https://img.shields.io/badge/Download-LectureFetch.exe%20(Windows)-FF6B00?style=for-the-badge&logo=windows&logoColor=white" alt="Download for Windows">
  </a>
  <a href="https://lesyster.github.io/lecturefetch/">
    <img src="https://img.shields.io/badge/Website-lesyster.github.io-0F172A?style=for-the-badge&logo=googlechrome&logoColor=white" alt="Official Website">
  </a>
  <a href="https://github.com/lesyster/lecturefetch/releases">
    <img src="https://img.shields.io/badge/Release-v1.1.0-10B981?style=for-the-badge" alt="Release v1.1.0">
  </a>
</p>

---

## ⚡ What is LectureFetch?
**LectureFetch** is a lightweight, 100% free open-source desktop application for university students running **Blackboard Learn Ultra**. Instead of clicking through 50 individual PDF and slide links the night before an exam, LectureFetch discovers your enrolled courses and extracts all lecture slides, syllabi, and problem sets into cleanly organized folders on your computer in **one click**.

---

## 🚀 Download & Installation

1. Click the button below to download the latest Windows standalone executable:
   👉 **[Download LectureFetch.exe (v1.1.0)](https://github.com/lesyster/lecturefetch/releases/latest/download/LectureFetch.exe)**
2. Double-click **`LectureFetch.exe`** on your Desktop or Downloads folder.
3. No installation or setup required — it runs immediately!

---

## 🛡️ Privacy & Security (How It Works)

- **Zero Credentials Stored:** You never enter your student ID or password into LectureFetch.
- **Official Browser Launch:** Clicking **"Connect Account"** launches your real Microsoft Edge (or Chrome/Brave) directly to the official university portal with Duo 2FA.
- **DPAPI Encryption:** Your temporary session ticket is encrypted on your machine using **Windows DPAPI** (`CryptProtectData`).
- **100% Local:** Files are saved directly to your chosen desktop folder (`Desktop\Lectures`). Zero files or data are uploaded anywhere.

---

## 📂 Features

- **⚡ One-Click Bulk Extraction:** Recursively finds all course content and downloads them simultaneously.
- **🔄 Smart Incremental Sync:** Automatically skips files you already have. Never downloads duplicates.
- **📁 Collision-Safe Folders:** Defaults to `Desktop\Lectures` (or `Desktop\Extracted Lectures` if an archive already exists).
- **🎓 Works for Any Major:** Dynamically discovers registered courses across Engineering, Business, Arts, Sciences, and Medicine.

---

## 🛠️ Running from Source (Developers)

If you have Python installed and want to inspect or run directly from source:

```bash
# 1. Clone the repository
git clone https://github.com/lesyster/lecturefetch.git
cd lecturefetch

# 2. Install dependencies
pip install -r requirements.txt

# 3. Launch application
python app.py
```

### 📦 Building the Standalone `.exe` with PyInstaller

```bash
pip install pyinstaller
python -m PyInstaller --clean LectureFetch.spec
```

---

## 📂 Repository Structure

- `app.py` — CustomTkinter modern desktop GUI application.
- `core_engine.py` — Background engine, CDP attach, and Blackboard REST extraction pipeline.
- `LectureFetch.spec` — PyInstaller standalone binary packaging specification.
- `requirements.txt` — Python dependencies (`customtkinter`, `pillow`, `playwright`).
- `index.html` — Official landing page hosted via GitHub Pages.
- `social-card.png` — High-DPI GitHub Social Card image.

---

## 🧡 Support the Project

Like this tool? Feel free to help out:
- **Wish Money:** `+961 70 007 193` *(Jad Mehtar)*

---

## 👤 Author

Developed with 🧡 by **Jad Mehtar** ([@lesyster](https://github.com/lesyster))  
Computer Engineering student.
