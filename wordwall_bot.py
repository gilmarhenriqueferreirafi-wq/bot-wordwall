import tkinter as tk
from tkinter import messagebox
import pyautogui
from PIL import Image, ImageGrab, ImageOps, ImageEnhance, ImageFilter
import pytesseract
import json
import re
import unicodedata
import difflib
import threading
import time
from pathlib import Path
import ctypes
from ctypes import wintypes

try:
    import win32gui
except ImportError:
    win32gui = None


# ============================================================
# CONFIGURAÇÃO
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
BANCO_ARQUIVO = BASE_DIR / "perguntas.json"

TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

REGIAO_PERGUNTA = {
    "left": 6,
    "top": 114,
    "width": 1350,
    "height": 293,
}

REGIAO_RESPOSTA_1 = {
    "left": 148,
    "top": 443,
    "width": 527,
    "height": 198,
}

REGIAO_RESPOSTA_2 = {
    "left": 690,
    "top": 442,
    "width": 527,
    "height": 196,
}

LIMITE_MATCH = 0.72
MARGEM_SEGURANCA = 0.08

INTERVALO_LOOP = 0.35
INTERVALO_APOS_CLIQUE = 0.60
TIMEOUT_PROXIMA_PERGUNTA = 12.0


# ============================================================
# LOCALIZAR BRAVE
# ============================================================

def encontrar_brave():

    if win32gui is None:
        raise RuntimeError(
            "PyWin32 não está instalado."
        )

    janelas_chrome = []

    def callback(hwnd, _):

        try:

            if not win32gui.IsWindow(hwnd):
                return

            titulo = win32gui.GetWindowText(hwnd).strip()
            classe = win32gui.GetClassName(hwnd)

            if classe != "Chrome_WidgetWin_1":
                return

            left, top, right, bottom = (
                win32gui.GetWindowRect(hwnd)
            )

            largura = right - left
            altura = bottom - top

            janelas_chrome.append({
                "hwnd": hwnd,
                "titulo": titulo,
                "classe": classe,
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "largura": largura,
                "altura": altura
            })

        except Exception as erro:

            print(
                "Erro examinando janela:",
                erro
            )

    # Enumerar todas as janelas
    win32gui.EnumWindows(
        callback,
        None
    )

    print()
    print("=" * 60)
    print("JANELAS CHROME_WIDGETWIN_1")
    print("=" * 60)

    for janela in janelas_chrome:

        print(
            f"HWND: {janela['hwnd']}"
        )

        print(
            f"TÍTULO: {janela['titulo']}"
        )

        print(
            f"TAMANHO: "
            f"{janela['largura']} x "
            f"{janela['altura']}"
        )

        print(
            f"POSIÇÃO: "
            f"{janela['left']} "
            f"{janela['top']}"
        )

        print("-" * 60)

    # --------------------------------------------------------
    # PROCURAR BRAVE
    # --------------------------------------------------------

    candidatos = []

    for janela in janelas_chrome:

        titulo = janela["titulo"].lower()

        # Nunca selecionar o próprio bot
        if "wordwall bot" in titulo:
            continue

        # Títulos que podem indicar o Brave
        if (
            "brave" in titulo
            or
            "recurso de ensino" in titulo
            or
            "wordwall" in titulo
            or
            "quiz" in titulo
        ):

            candidatos.append(
                janela
            )

    # --------------------------------------------------------
    # SE NÃO ENCONTROU PELO TÍTULO
    # --------------------------------------------------------

    if not candidatos:

        # Pega as maiores janelas Chrome,
        # ignorando o próprio bot
        candidatos = [
            janela
            for janela in janelas_chrome
            if "wordwall bot"
            not in janela["titulo"].lower()
            and janela["largura"] >= 700
            and janela["altura"] >= 400
        ]

    # --------------------------------------------------------
    # NADA ENCONTRADO
    # --------------------------------------------------------

    if not candidatos:

        print()
        print("=" * 60)
        print("NENHUMA JANELA BRAVE ENCONTRADA")
        print("=" * 60)

        raise RuntimeError(
            "Não consegui localizar a janela do Brave."
        )

    # --------------------------------------------------------
    # PRIORIZAR JANELA ATIVA
    # --------------------------------------------------------

    janela_ativa = (
        win32gui.GetForegroundWindow()
    )

    for janela in candidatos:

        if janela["hwnd"] == janela_ativa:

            print()
            print(
                "Brave selecionado "
                "(janela ativa):"
            )

            print(
                janela["hwnd"]
            )

            print("=" * 60)

            return janela["hwnd"]

    # --------------------------------------------------------
    # CASO BRAVE ESTEJA EM SEGUNDO PLANO
    # --------------------------------------------------------

    janela = max(
        candidatos,
        key=lambda x:
        x["largura"] * x["altura"]
    )

    print()
    print(
        "Brave selecionado "
        "(maior janela):"
    )

    print(
        janela["hwnd"]
    )

    print("=" * 60)

    return janela["hwnd"]
# ============================================================
# RECT DA JANELA
# ============================================================

def obter_rect_brave(hwnd):

    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(
            "A janela do Brave não é válida."
        )

    if win32gui.IsIconic(hwnd):
        raise RuntimeError(
            "O Brave está minimizado."
        )

    left, top, right, bottom = (
        win32gui.GetWindowRect(hwnd)
    )

    largura = right - left
    altura = bottom - top

    if largura <= 0 or altura <= 0:

        raise RuntimeError(
            "Tamanho inválido da janela."
        )

    return (
        left,
        top,
        right,
        bottom
    )


# ============================================================
# PRINTWINDOW
# ============================================================

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

PW_RENDERFULLCONTENT = 0x00000002


class BITMAPINFOHEADER(ctypes.Structure):

    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):

    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


def capturar_printwindow(hwnd):

    left, top, right, bottom = (
        win32gui.GetWindowRect(hwnd)
    )

    largura = right - left
    altura = bottom - top

    if largura <= 0 or altura <= 0:

        raise RuntimeError(
            "Dimensões inválidas do Brave."
        )

    hdc_screen = user32.GetDC(hwnd)

    if not hdc_screen:

        raise RuntimeError(
            "Não foi possível obter HDC."
        )

    hdc_mem = None
    hbmp = None
    old_obj = None

    try:

        hdc_mem = gdi32.CreateCompatibleDC(
            hdc_screen
        )

        if not hdc_mem:

            raise RuntimeError(
                "CreateCompatibleDC falhou."
            )

        hbmp = gdi32.CreateCompatibleBitmap(
            hdc_screen,
            largura,
            altura
        )

        if not hbmp:

            raise RuntimeError(
                "CreateCompatibleBitmap falhou."
            )

        old_obj = gdi32.SelectObject(
            hdc_mem,
            hbmp
        )

        resultado = user32.PrintWindow(
            hwnd,
            hdc_mem,
            PW_RENDERFULLCONTENT
        )

        if not resultado:

            resultado = user32.PrintWindow(
                hwnd,
                hdc_mem,
                0
            )

        if not resultado:

            raise RuntimeError(
                "PrintWindow falhou."
            )

        bmi = BITMAPINFO()

        bmi.bmiHeader.biSize = (
            ctypes.sizeof(
                BITMAPINFOHEADER
            )
        )

        bmi.bmiHeader.biWidth = largura
        bmi.bmiHeader.biHeight = -altura
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        tamanho = (
            largura *
            altura *
            4
        )

        buffer = ctypes.create_string_buffer(
            tamanho
        )

        resultado = gdi32.GetDIBits(
            hdc_mem,
            hbmp,
            0,
            altura,
            buffer,
            ctypes.byref(bmi),
            0
        )

        if resultado == 0:

            raise RuntimeError(
                "GetDIBits falhou."
            )

        imagem = Image.frombuffer(
            "RGBA",
            (
                largura,
                altura
            ),
            buffer,
            "raw",
            "BGRA",
            0,
            1
        )

        return imagem.convert("RGB")

    finally:

        if old_obj is not None:

            gdi32.SelectObject(
                hdc_mem,
                old_obj
            )

        if hbmp:

            gdi32.DeleteObject(
                hbmp
            )

        if hdc_mem:

            gdi32.DeleteDC(
                hdc_mem
            )

        user32.ReleaseDC(
            hwnd,
            hdc_screen
        )


# ============================================================
# CAPTURA DO BRAVE
# ============================================================

def capturar_janela_brave(hwnd):

    print(
        "Capturando Brave..."
    )

    try:

        imagem = capturar_printwindow(
            hwnd
        )

        if imagem:

            print(
                "Captura direta:",
                imagem.width,
                "x",
                imagem.height
            )

            return imagem

    except Exception as erro:

        print(
            "PrintWindow falhou:",
            erro
        )

    # Fallback para captura da tela
    left, top, right, bottom = (
        obter_rect_brave(hwnd)
    )

    print(
        f"Captura fallback: "
        f"X={left} "
        f"Y={top} "
        f"L={right-left} "
        f"A={bottom-top}"
    )

    imagem = ImageGrab.grab(
        bbox=(
            left,
            top,
            right,
            bottom
        ),
        include_layered_windows=True
    )

    return imagem


# ============================================================
# RECORTE
# ============================================================

def recortar_regiao_brave(
    imagem,
    regiao
):

    left = int(
        regiao["left"]
    )

    top = int(
        regiao["top"]
    )

    right = (
        left +
        int(regiao["width"])
    )

    bottom = (
        top +
        int(regiao["height"])
    )

    if (
        left < 0
        or top < 0
        or right > imagem.width
        or bottom > imagem.height
    ):

        raise RuntimeError(
            "A região de OCR não cabe "
            "na captura do Brave.\n\n"
            f"Imagem: "
            f"{imagem.width}x"
            f"{imagem.height}\n"
            f"Região: "
            f"{left},{top},"
            f"{right},{bottom}"
        )

    return imagem.crop(
        (
            left,
            top,
            right,
            bottom
        )
    )


# ============================================================
# OCR
# ============================================================

def preparar_imagem(
    imagem,
    escala=2
):

    imagem = imagem.convert(
        "RGB"
    )

    imagem = imagem.resize(
        (
            imagem.width * escala,
            imagem.height * escala
        )
    )

    imagem = ImageOps.grayscale(
        imagem
    )

    imagem = ImageEnhance.Contrast(
        imagem
    ).enhance(2.0)

    imagem = ImageEnhance.Sharpness(
        imagem
    ).enhance(2.0)

    imagem = imagem.filter(
        ImageFilter.SHARPEN
    )

    return imagem


def executar_ocr(
    imagem,
    psm=6
):

    imagem = preparar_imagem(
        imagem
    )

    return pytesseract.image_to_string(
        imagem,
        lang="por",
        config=f"--psm {psm}"
    ).strip()


def ler_tela_brave():

    hwnd = encontrar_brave()

    imagem = capturar_janela_brave(
        hwnd
    )

    pergunta_img = recortar_regiao_brave(
        imagem,
        REGIAO_PERGUNTA
    )

    resposta1_img = recortar_regiao_brave(
        imagem,
        REGIAO_RESPOSTA_1
    )

    resposta2_img = recortar_regiao_brave(
        imagem,
        REGIAO_RESPOSTA_2
    )

    pergunta = executar_ocr(
        pergunta_img
    )

    resposta1 = executar_ocr(
        resposta1_img
    )

    resposta2 = executar_ocr(
        resposta2_img
    )

    return (
        pergunta,
        resposta1,
        resposta2
    )


# ============================================================
# BANCO
# ============================================================

def normalizar_texto(
    texto
):

    if not texto:
        return ""

    texto = str(
        texto
    )

    texto = unicodedata.normalize(
        "NFD",
        texto
    )

    texto = "".join(
        c
        for c in texto
        if unicodedata.category(
            c
        ) != "Mn"
    )

    texto = texto.lower()

    texto = re.sub(
        r"[^a-z0-9\s]",
        " ",
        texto
    )

    texto = re.sub(
        r"\s+",
        " ",
        texto
    )

    return texto.strip()


def similaridade(
    texto1,
    texto2
):

    a = normalizar_texto(
        texto1
    )

    b = normalizar_texto(
        texto2
    )

    if not a or not b:
        return 0.0

    return difflib.SequenceMatcher(
        None,
        a,
        b
    ).ratio()


def carregar_banco():

    if not BANCO_ARQUIVO.exists():
        return []

    try:

        with open(
            BANCO_ARQUIVO,
            "r",
            encoding="utf-8"
        ) as arquivo:

            dados = json.load(
                arquivo
            )

        if isinstance(
            dados,
            dict
        ):

            dados = dados.get(
                "perguntas",
                []
            )

        if not isinstance(
            dados,
            list
        ):

            return []

        return dados

    except Exception as erro:

        print(
            "Erro no banco:",
            erro
        )

        return []


def encontrar_pergunta(
    pergunta_ocr,
    banco
):

    if not pergunta_ocr:
        return None, 0.0

    if not banco:
        return None, 0.0

    melhor_item = None
    melhor_score = 0.0

    for item in banco:

        if not isinstance(
            item,
            dict
        ):
            continue

        pergunta_banco = (
            item.get("pergunta")
            or
            item.get("question")
            or
            ""
        )

        score = similaridade(
            pergunta_ocr,
            pergunta_banco
        )

        if score > melhor_score:

            melhor_score = score
            melhor_item = item

    if (
        melhor_item is None
        or
        melhor_score < LIMITE_MATCH
    ):

        return (
            None,
            melhor_score
        )

    return (
        melhor_item,
        melhor_score
    )


def obter_resposta_do_item(item):

    if not isinstance(item, dict):
        return None

    resposta = (
        item.get("resposta")
        or item.get("answer")
        or ""
    )

    resposta = str(resposta).strip()

    if not resposta:
        return None

    return resposta

# ============================================================
# CLIQUE
# ============================================================

def centro_regiao(
    regiao
):

    return (
        regiao["left"]
        + regiao["width"] // 2,

        regiao["top"]
        + regiao["height"] // 2
    )


def clicar_resposta(numero):

    hwnd = encontrar_brave()

    if not win32gui.IsWindow(hwnd):
        raise RuntimeError(
            "A janela do Brave não é mais válida."
        )

    left, top, right, bottom = (
        win32gui.GetWindowRect(hwnd)
    )

    if numero == 1:

        rel_x = (
            REGIAO_RESPOSTA_1["left"]
            +
            REGIAO_RESPOSTA_1["width"] // 2
        )

        rel_y = (
            REGIAO_RESPOSTA_1["top"]
            +
            REGIAO_RESPOSTA_1["height"] // 2
        )

    elif numero == 2:

        rel_x = (
            REGIAO_RESPOSTA_2["left"]
            +
            REGIAO_RESPOSTA_2["width"] // 2
        )

        rel_y = (
            REGIAO_RESPOSTA_2["top"]
            +
            REGIAO_RESPOSTA_2["height"] // 2
        )

    else:

        raise ValueError(
            "Número da resposta inválido."
        )

    # Coordenada absoluta na tela
    x = left + rel_x
    y = top + rel_y

    print()
    print("=" * 60)
    print("CLIQUE AUTOMÁTICO")
    print("=" * 60)
    print("Janela Brave:", hwnd)
    print("Posição da janela:", left, top)
    print("Resposta escolhida:", numero)
    print("Posição relativa:", rel_x, rel_y)
    print("Posição na tela:", x, y)
    print("=" * 60)

    # Coloca o Brave em primeiro plano
    try:

        win32gui.ShowWindow(
            hwnd,
            3  # SW_RESTORE
        )

        win32gui.SetForegroundWindow(
            hwnd
        )

        time.sleep(0.15)

    except Exception as erro:

        print(
            "Aviso ao ativar Brave:",
            erro
        )

    # Move e clica
    pyautogui.moveTo(
        x,
        y,
        duration=0.10
    )

    time.sleep(0.05)

    pyautogui.click(
        x,
        y
    )

    print(
        "CLIQUE REALIZADO!"
    )
# ============================================================
# INTERFACE
# ============================================================

class WordwallBotApp:

    def __init__(
        self,
        root
    ):

        self.root = root

        self.root.title(
            "WordWall Bot - Brave"
        )

        self.root.geometry(
            "330x320"
        )

        self.root.resizable(
            False,
            False
        )

        self.root.attributes(
            "-topmost",
            True
        )

        self.executando = False

        self.thread = None

        self.pergunta_atual = ""

        self.status_var = tk.StringVar(
            value="PARADO"
        )

        self.pergunta_var = tk.StringVar(
            value="Pergunta: -"
        )

        self.resposta_var = tk.StringVar(
            value="Resposta: -"
        )

        self.score_var = tk.StringVar(
            value="Similaridade: -"
        )

        self.criar_interface()


    def criar_interface(
        self
    ):

        tk.Label(
            self.root,
            text="WORDWALL BOT",
            font=(
                "Arial",
                16,
                "bold"
            )
        ).pack(
            pady=(12, 4)
        )

        tk.Label(
            self.root,
            text="OCR + banco de perguntas + Brave"
        ).pack()

        frame = tk.Frame(
            self.root
        )

        frame.pack(
            pady=10
        )

        self.botao_iniciar = tk.Button(
            frame,
            text="INICIAR",
            width=12,
            command=self.iniciar
        )

        self.botao_iniciar.grid(
            row=0,
            column=0,
            padx=4
        )

        self.botao_parar = tk.Button(
            frame,
            text="PARAR",
            width=12,
            command=self.parar,
            state=tk.DISABLED
        )

        self.botao_parar.grid(
            row=0,
            column=1,
            padx=4
        )

        tk.Button(
            self.root,
            text="LER AGORA",
            width=26,
            command=self.ler_agora
        ).pack(
            pady=5
        )

        tk.Label(
            self.root,
            textvariable=self.status_var,
            font=(
                "Arial",
                10,
                "bold"
            )
        ).pack(
            pady=5
        )

        tk.Label(
            self.root,
            textvariable=self.pergunta_var,
            wraplength=300,
            justify="left"
        ).pack(
            padx=10,
            pady=3
        )

        tk.Label(
            self.root,
            textvariable=self.resposta_var
        ).pack()

        tk.Label(
            self.root,
            textvariable=self.score_var
        ).pack()


    # ========================================================
    # INICIAR
    # ========================================================

    def iniciar(self):

        if self.executando:
            return

        if not Path(
            TESSERACT_PATH
        ).exists():

            messagebox.showerror(
                "Tesseract",
                "Tesseract não encontrado em:\n\n"
                + TESSERACT_PATH
            )

            return

        pytesseract.pytesseract.tesseract_cmd = (
            TESSERACT_PATH
        )

        try:

            encontrar_brave()

        except Exception as erro:

            messagebox.showerror(
                "Brave",
                str(erro)
            )

            return

        self.executando = True

        self.status_var.set(
            "EXECUTANDO"
        )

        self.botao_iniciar.config(
            state=tk.DISABLED
        )

        self.botao_parar.config(
            state=tk.NORMAL
        )

        self.thread = threading.Thread(
            target=self.loop_bot,
            daemon=True
        )

        self.thread.start()


    # ========================================================
    # PARAR
    # ========================================================

    def parar(self):

        self.executando = False

        self.status_var.set(
            "PARADO"
        )

        self.botao_iniciar.config(
            state=tk.NORMAL
        )

        self.botao_parar.config(
            state=tk.DISABLED
        )


    # ========================================================
    # LER AGORA
    # ========================================================

    def ler_agora(self):

        threading.Thread(
            target=self._ler_agora_thread,
            daemon=True
        ).start()


    def _ler_agora_thread(
        self
    ):

        try:

            if not Path(
                TESSERACT_PATH
            ).exists():

                raise RuntimeError(
                    "Tesseract não encontrado em:\n"
                    + TESSERACT_PATH
                )

            pytesseract.pytesseract.tesseract_cmd = (
                TESSERACT_PATH
            )

            (
                pergunta,
                resposta1,
                resposta2
            ) = ler_tela_brave()

            banco = carregar_banco()

            item, score = encontrar_pergunta(
                pergunta,
                banco
            )

            resposta = (
                obter_resposta_do_item(
                    item
                )
                if item
                else None
            )

            self.atualizar_interface(
                pergunta,
                resposta,
                score
            )

            print()
            print("=" * 60)
            print("PERGUNTA:")
            print(pergunta)

            print()
            print("RESPOSTA 1:")
            print(resposta1)

            print()
            print("RESPOSTA 2:")
            print(resposta2)

            print()
            print("RESPOSTA DO BANCO:")
            print(resposta)

            print()
            print(
                "SIMILARIDADE:",
                round(
                    score,
                    3
                )
            )

            print("=" * 60)

        except Exception as erro:

            self.atualizar_status(
                "ERRO: " + str(erro)
            )

            print(
                "Erro ao ler:",
                erro
            )


# ========================================================
# LOOP
# ========================================================

    def loop_bot(self):

    banco = carregar_banco()

    if not banco:
        self.atualizar_status("Banco vazio")
        return

    print()
    print("=" * 60)
    print("BOT AUTOMÁTICO INICIADO")
    print("=" * 60)

    while self.executando:

        try:

            # Ler tela
            pergunta, resposta1, resposta2 = ler_tela_brave()

            print()
            print("=" * 60)
            print("NOVA LEITURA")
            print("=" * 60)

            print("PERGUNTA:", pergunta)
            print("RESPOSTA 1:", resposta1)
            print("RESPOSTA 2:", resposta2)

            # Normalizar pergunta
            pergunta_normalizada = normalizar_texto(pergunta)

            if not pergunta_normalizada:
                time.sleep(INTERVALO_LOOP)
                continue

            # Não processar a mesma pergunta novamente
            if (
                self.pergunta_atual
                and
                pergunta_normalizada == self.pergunta_atual
            ):
                time.sleep(INTERVALO_LOOP)
                continue

            # Procurar pergunta no banco
            item, score_pergunta = encontrar_pergunta(
                pergunta,
                banco
            )

            if item is None:

                print("Pergunta não encontrada.")

                self.atualizar_status(
                    "Pergunta não encontrada"
                )

                self.pergunta_atual = pergunta_normalizada

                time.sleep(INTERVALO_LOOP)
                continue

            # Pegar resposta correta
            resposta_correta = obter_resposta_do_item(item)

            if not resposta_correta:

                print("Resposta não existe no banco.")

                self.atualizar_status(
                    "Resposta não encontrada"
                )

                self.pergunta_atual = pergunta_normalizada

                time.sleep(INTERVALO_LOOP)
                continue

            print()
            print("PERGUNTA ENCONTRADA!")
            print(
                "Similaridade da pergunta:",
                f"{score_pergunta:.1%}"
            )

            print(
                "RESPOSTA CORRETA:",
                resposta_correta
            )

            # Comparar resposta correta com a resposta 1
            score_resposta_1 = similaridade(
                resposta_correta,
                resposta1
            )

            # Comparar resposta correta com a resposta 2
            score_resposta_2 = similaridade(
                resposta_correta,
                resposta2
            )

            print()
            print("COMPARAÇÃO DAS RESPOSTAS")

            print(
                f"Resposta 1: {resposta1} "
                f"-> {score_resposta_1:.1%}"
            )

            print(
                f"Resposta 2: {resposta2} "
                f"-> {score_resposta_2:.1%}"
            )

            # Limite mínimo
            LIMITE_RESPOSTA = 0.55

            if (
                score_resposta_1 < LIMITE_RESPOSTA
                and
                score_resposta_2 < LIMITE_RESPOSTA
            ):

                print(
                    "Nenhuma resposta corresponde ao banco."
                )

                self.atualizar_status(
                    "Resposta não identificada"
                )

                self.pergunta_atual = pergunta_normalizada

                time.sleep(INTERVALO_LOOP)
                continue

            # Escolher lado
            if score_resposta_1 >= score_resposta_2:

                lado = 1
                score_final = score_resposta_1

            else:

                lado = 2
                score_final = score_resposta_2

            print()
            print("RESPOSTA CORRETA ENCONTRADA!")
            print("LADO:", lado)
            print(
                "SIMILARIDADE:",
                f"{score_final:.1%}"
            )

            # Atualizar interface
            self.atualizar_interface(
                pergunta,
                resposta_correta,
                score_final
            )

            self.atualizar_status(
                f"Clicando resposta {lado}"
            )

            # Marcar pergunta como processada
            self.pergunta_atual = pergunta_normalizada

            # Clicar
            print()
            print("VOU CLICAR AGORA...")

            clicar_resposta(lado)

            print("CLIQUE ENVIADO!")

            # Esperar WordWall mudar
            time.sleep(INTERVALO_APOS_CLIQUE)

            inicio = time.time()

            while (
                self.executando
                and
                time.time() - inicio < TIMEOUT_PROXIMA_PERGUNTA
            ):

                try:

                    nova_pergunta, _, _ = ler_tela_brave()

                    nova_normalizada = normalizar_texto(
                        nova_pergunta
                    )

                    if (
                        nova_normalizada
                        and
                        nova_normalizada != pergunta_normalizada
                    ):

                        print(
                            "Nova pergunta detectada!"
                        )

                        break

                except Exception as erro:

                    print(
                        "Erro aguardando próxima:",
                        erro
                    )

                time.sleep(INTERVALO_LOOP)

        except Exception as erro:

            print()
            print("ERRO NO LOOP:")
            print(erro)

            self.atualizar_status(
                "Erro: " + str(erro)
            )

            time.sleep(1)

# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print("WORDWALL BOT - BRAVE")
    print("=" * 60)

    print(
        "Procurando aplicativo Brave..."
    )

    print()

    root = tk.Tk()

    WordwallBotApp(
        root
    )

    root.mainloop()


if __name__ == "__main__":
    main()