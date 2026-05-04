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

*test*
mkdir C:\temp\photoneo_test 2>nul
"C:\path\to\launcher\.venv\Scripts\python.exe" "C:\path\to\launcher\capture_worker_cli.py" "{\"cameras\":[\"photoneo\"],\"out_dir\":\"C:\\temp\\photoneo_test\",\"phoxi_dir\":\"C:\\Program Files\\Photoneo\\PhoXiControl\",\"zivid_settings\":null}"