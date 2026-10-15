"""Scrollable native review of complete, executable Windows command plans."""
import tkinter as tk
from tkinter import ttk

from .windows_control import is_admin
from .theme import PAPER, SURFACE, INK


class CommandDialog(tk.Toplevel):
    def __init__(self, parent, summary):
        super().__init__(parent)
        self.approved = False
        self.title("Windows 명령 실행 확인")
        self.configure(bg=PAPER)
        self.geometry("780x580")
        self.minsize(640, 440)
        self.transient(parent)
        footer = ttk.Frame(self, padding=16)
        footer.pack(side="bottom", fill="x")
        ttk.Button(footer, text="위 명령 실행", command=self.approve, style="Accent.TButton").pack(side="right")
        cancel = ttk.Button(footer, text="취소", command=self.destroy)
        cancel.pack(side="right", padx=10)
        state = "관리자" if is_admin() else "일반 사용자"
        ttk.Label(self, text=f"현재 {state} 권한으로 실행합니다. 파일 삭제·시스템 변경은 되돌리지 못할 수 있습니다.\n"
                  "아래 명령 전체를 확인하세요. AI의 설명과 실제 명령이 다르면 취소하세요.",
                  wraplength=720, padding=16, justify="left").pack(fill="x")
        body = ttk.Frame(self, padding=(16, 0))
        body.pack(fill="both", expand=True)
        self.command_text = tk.Text(body, wrap="word", font=("Consolas", 11), padx=12, pady=12,
                                    bg=SURFACE, fg=INK, relief="flat")
        scrollbar = ttk.Scrollbar(body, command=self.command_text.yview)
        self.command_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.command_text.pack(fill="both", expand=True)
        self.command_text.insert("1.0", summary)
        self.command_text.configure(state="disabled")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.grab_set()
        cancel.focus_set()

    def approve(self):
        self.approved = True
        self.destroy()
