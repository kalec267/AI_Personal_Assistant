# Build on Windows to produce a Windows executable.
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

project = Path(SPECPATH)
voice_datas, voice_binaries, voice_imports = collect_all("sherpa_onnx")
analysis = Analysis(
    [str(project / "launch.py")],
    pathex=[str(project)],
    binaries=voice_binaries,
    datas=[(str(project / "assistant" / "volume.ps1"), "assistant"),
           (str(project / "assistant" / "speech.ps1"), "assistant"),
           (str(project / "assistant" / "document.ps1"), "assistant")] + voice_datas + collect_data_files("docx") + collect_data_files("pptx"),
    hiddenimports=["speech_recognition", "pyaudio", "numpy"] + voice_imports,
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
