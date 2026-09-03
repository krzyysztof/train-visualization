"""Root application window."""
import tkinter as tk
from tkinter import ttk

from mapa_polski.map_canvas import MapCanvas

DEFAULT_STATUS = "Kliknij pociąg, aby zobaczyć jego numer i trasę — scroll: zoom, przeciąganie: przesuwanie mapy"
NO_SELECTION_TEXT = "Kliknij pociąg na mapie, aby zobaczyć szczegóły."


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Mapa Polski")
        self.geometry("1000x700")
        self.minsize(600, 400)

        self._selected_status = DEFAULT_STATUS
        self.status_var = tk.StringVar(value=DEFAULT_STATUS)

        status_bar = ttk.Label(self, textvariable=self.status_var, anchor="w", padding=(8, 4))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        self.sidebar = self._build_sidebar()
        self.sidebar.pack(fill=tk.Y, side=tk.LEFT)

        self.map_canvas = MapCanvas(self, on_select=self._on_select, on_hover=self._on_hover)
        self.map_canvas.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

    def _build_sidebar(self):
        sidebar = ttk.Frame(self, width=220, padding=12)
        sidebar.pack_propagate(False)

        ttk.Label(sidebar, text="Wybrany pociąg", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(0, 8))

        self._no_selection_label = ttk.Label(sidebar, text=NO_SELECTION_TEXT, wraplength=196, justify="left")
        self._no_selection_label.pack(anchor="w")

        self._detail_vars = {key: tk.StringVar() for key in ("num", "route", "origin", "dest")}
        self._detail_frame = ttk.Frame(sidebar)
        rows = [
            ("route", "Kategoria:"),
            ("num", "Numer:"),
            ("origin", "Skąd:"),
            ("dest", "Dokąd:"),
        ]
        for row_index, (key, caption) in enumerate(rows):
            ttk.Label(self._detail_frame, text=caption, font=("TkDefaultFont", 9, "bold")).grid(
                row=row_index, column=0, sticky="nw", pady=3
            )
            ttk.Label(self._detail_frame, textvariable=self._detail_vars[key], wraplength=140, justify="left").grid(
                row=row_index, column=1, sticky="nw", padx=(6, 0), pady=3
            )

        return sidebar

    def _on_hover(self, name):
        self.status_var.set(name if name else self._selected_status)

    def _on_select(self, train):
        for key, var in self._detail_vars.items():
            var.set(train[key])
        self._no_selection_label.pack_forget()
        self._detail_frame.pack(anchor="w")

        self._selected_status = f"Wybrano: {train['route']} {train['num']} → {train['dest']}".strip()
        self.status_var.set(self._selected_status)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
