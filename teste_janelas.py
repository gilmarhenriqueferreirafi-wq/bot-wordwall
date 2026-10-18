import win32gui
import win32process
import psutil


print("=" * 70)
print("JANELAS VISÍVEIS DO WINDOWS")
print("=" * 70)


def listar_janelas(hwnd, _):

    try:
        if not win32gui.IsWindowVisible(hwnd):
            return

        titulo = win32gui.GetWindowText(hwnd).strip()
        classe = win32gui.GetClassName(hwnd)

        if not titulo:
            return

        left, top, right, bottom = win32gui.GetWindowRect(hwnd)

        largura = right - left
        altura = bottom - top

        _, pid = win32process.GetWindowThreadProcessId(hwnd)

        try:
            processo = psutil.Process(pid).name()
        except:
            processo = "desconhecido"

        print()
        print("-" * 70)
        print("HWND:", hwnd)
        print("TÍTULO:", titulo)
        print("CLASSE:", classe)
        print("PROCESSO:", processo)
        print("PID:", pid)
        print("TAMANHO:", largura, "x", altura)
        print("POSIÇÃO:", left, top)

    except Exception:
        pass


win32gui.EnumWindows(listar_janelas, None)

print()
print("=" * 70)
print("FIM")
print("=" * 70)

input("\nPressione ENTER para fechar...")