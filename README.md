# photoneo-zivid-gui
auto hotkey trigger image capture


cd C:\path\to\your\folder

*REM make a venv (one time only)*
python -m venv .venv

*REM activate it (every new terminal session)*
.venv\Scripts\activate

*REM install dependencies (one time only)*
pip install zivid harvesters keyboard numpy

*REM run it*
python capture_3d.py


cd C:\path\to\your\folder
.venv\Scripts\activate
python capture_3d.py

*zivid version match, must match*
pip uninstall zivid
pip install zivid==2.16.0

uv pip uninstall zivid
uv pip install zivid==2.16.0

uv pip install open3d