@echo off
REM Windows helper to create venv (first time) and run the app
if not exist venv (
  python -m venv venv
  call venv\Scripts\activate
  python -m pip install --upgrade pip
  pip install -r requirements.txt
)
call venv\Scripts\activate
python lively_marketplace_app.py
pause
