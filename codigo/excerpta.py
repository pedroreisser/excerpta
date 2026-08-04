"""Ponto de entrada do Excerpta.

Toda a interface está em gui.py; a lógica, nos módulos de negócio.
"""

from gui import ExcerptaApp


if __name__ == '__main__':
    app = ExcerptaApp()
    app.mainloop()
