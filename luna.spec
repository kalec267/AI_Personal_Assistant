# Build on Windows to produce a Windows executable.
from pathlib import Path

project = Path(SPECPATH)
analysis = Analysis(
    [str(project / "launch.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[(str(project / "assistant" / "volume.ps1"), "assistant"),
           (str(project / "assistant" / "speech.ps1"), "assistant")],
    hiddenimports=["speech_recognition", "pyaudio"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="Luna",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False,
)
collection = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="Luna")
