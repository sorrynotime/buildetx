"""EdgeTX Builder entry point.

The GUI implementation lives in :mod:`edgetx_builder_app`; this small module
keeps the original command line entry point compatible.
"""

import tkinter as tk

from edgetx_builder_app import App


if __name__ == "__main__":
    app_root = tk.Tk()
    App(app_root)
    app_root.mainloop()
