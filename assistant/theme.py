"""Shared white surfaces and violet accents for the native desktop UI."""
BG = PAPER = "#ffffff"
SURFACE = "#f8f9fd"
INK = "#23263b"
MUTED = "#737b91"
BLUE = "#6955df"
LINE = "#e7eaf2"
TINT = "#f0edff"


def apply_theme(root):
    from tkinter import ttk
    root.option_add("*Font", ("Malgun Gothic", 10))
    root.option_add("*TCombobox*Listbox.background", PAPER)
    root.option_add("*TCombobox*Listbox.foreground", INK)
    root.option_add("*TCombobox*Listbox.selectBackground", TINT)
    root.option_add("*TCombobox*Listbox.selectForeground", BLUE)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=INK, font=("Malgun Gothic", 10))
    style.configure("TFrame", background=PAPER)
    style.configure("TLabel", background=PAPER, foreground=INK)
    style.configure("TButton", background=SURFACE, foreground=BLUE, padding=(13, 8),
                    borderwidth=0, relief="flat", font=("Malgun Gothic", 9, "bold"))
    style.map("TButton", background=[("disabled", SURFACE), ("active", TINT)], foreground=[("disabled", "#a1a8bb")])
    style.configure("Accent.TButton", background=BLUE, foreground="white")
    style.map("Accent.TButton", background=[("active", "#5945c9"), ("disabled", "#b8afe8")], foreground=[("disabled", "white")])
    style.configure("TEntry", fieldbackground=SURFACE, padding=7, bordercolor=LINE, lightcolor=LINE, darkcolor=LINE)
    style.map("TEntry", bordercolor=[("focus", BLUE)])
    style.configure("TCombobox", padding=6, fieldbackground=SURFACE, background=PAPER, bordercolor=LINE, arrowsize=12)
    style.map("TCombobox", fieldbackground=[("readonly", SURFACE)], selectbackground=[("readonly", SURFACE)], selectforeground=[("readonly", INK)])
    style.configure("TNotebook", background=PAPER, borderwidth=0, tabmargins=(0, 0, 0, 10), bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.configure("TNotebook.Tab", background=PAPER, foreground=MUTED, padding=(14, 10), borderwidth=0, bordercolor=PAPER, lightcolor=PAPER, darkcolor=PAPER)
    style.map("TNotebook.Tab", background=[("selected", TINT), ("active", SURFACE)], foreground=[("selected", BLUE)])
    style.configure("TCheckbutton", background=PAPER, foreground=INK, padding=(0, 3))
    style.map("TCheckbutton", background=[("active", PAPER)])
    style.configure("TScale", background=PAPER, troughcolor=LINE, borderwidth=0)
    style.configure("TSeparator", background=LINE)
    style.configure("TScrollbar", background=LINE, troughcolor=PAPER, bordercolor=PAPER, arrowcolor=MUTED, borderwidth=0, arrowsize=12)
    style.map("TScrollbar", background=[("active", "#d1caef")])
    style.configure("Treeview", background=PAPER, fieldbackground=PAPER, foreground=INK, rowheight=39,
                    borderwidth=0, font=("Malgun Gothic", 10))
    style.configure("Treeview.Heading", background=SURFACE, foreground=MUTED, font=("Malgun Gothic", 9, "bold"), padding=10, relief="flat")
    style.map("Treeview", background=[("selected", TINT)], foreground=[("selected", BLUE)])
