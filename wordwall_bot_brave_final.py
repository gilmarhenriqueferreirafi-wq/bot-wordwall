import tkinter as tk
from tkinter import messagebox
import pyautogui
from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageGrab
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

# Dependência para localizar a janela do Brave.
try:
    import win32gui
    import win32con
except ImportError:
    win32gui = None
    win32con = None


# ============================================================
# WORDWALL BOT
# Captura SOMENTE a janela do Brave
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
BANCO_ARQUIVO = BASE_DIR / "perguntas.json"

# Tesseract
TESSERACT_PATH = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# Segurança
LIMITE_MATCH = 0.58
MARGEM_SEGURANCA = 0.10

# Tempo
INTERVALO_LOOP = 0.35
INTERVALO_APOS_CLIQUE = 0.60
INTERVALO_MUDANCA = 0.25
TIMEOUT_PROXIMA_PERGUNTA = 12.0


# ============================================================
# REGIÕES
#
# IMPORTANTE:
# Estas coordenadas continuam sendo as mesmas que você mediu,
# mas agora são tratadas como coordenadas RELATIVAS À JANELA
# DO FIREFOX.
#
# A ideia é:
#
# Brave
# └── região da pergunta
# └── região resposta 1
# └── região resposta 2
# ============================================================

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
    "width": 522,
    "height": 196,
}


# ============================================================
# ESTADO
# ============================================================

rodando = False
thread_bot = None
perguntas = {}


# ============================================================
# TEXTO
# ============================================================

def normalizar_texto(texto):
    if not texto:
        return ""

    texto = texto.replace("\n", " ")
    texto = texto.replace("\r", " ")

    substituicoes = {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "–": "-",
        "—": "-",
        "−": "-",
    }

    for antigo, novo in substituicoes.items():
        texto = texto.replace(antigo, novo)

    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(
        c for c in texto
        if not unicodedata.combining(c)
    )

    texto = texto.lower()

    # Alguns erros muito comuns do OCR.
    texto = re.sub(r"\s+", " ", texto)
    texto = re.sub(r"[^a-z0-9\s%.,:/+\-?]", " ", texto)
    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()


def similaridade(texto_a, texto_b):
    a = normalizar_texto(texto_a)
    b = normalizar_texto(texto_b)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    if a in b or b in a:
        menor = min(len(a), len(b))
        maior = max(len(a), len(b))

        if maior:
            return max(0.90, menor / maior)

    return difflib.SequenceMatcher(None, a, b).ratio()


def extrair_numeros(texto):
    """Extrai números do texto normalizado, preservando a ordem."""
    texto = normalizar_texto(texto)
    return re.findall(r"\d+(?:[.,]\d+)?", texto)


def extrair_palavras(texto):
    """Retorna palavras relevantes, ignorando números isolados."""
    texto = normalizar_texto(texto)
    palavras = re.findall(r"[a-z]+", texto)
    return palavras


def similaridade_palavras(texto_a, texto_b):
    """Compara palavras individualmente, tolerando pequenos erros de OCR."""
    a = extrair_palavras(texto_a)
    b = extrair_palavras(texto_b)

    if not a or not b:
        return 0.0

    # Para cada palavra esperada, procura a melhor palavra da tela.
    scores = []
    usadas = set()

    for palavra_a in a:
        melhor = 0.0
        melhor_indice = None

        for indice, palavra_b in enumerate(b):
            if indice in usadas:
                continue

            score = difflib.SequenceMatcher(
                None, palavra_a, palavra_b
            ).ratio()

            if score > melhor:
                melhor = score
                melhor_indice = indice

        if melhor_indice is not None:
            usadas.add(melhor_indice)
            scores.append(melhor)

    if not scores:
        return 0.0

    return sum(scores) / len(a)


def similaridade_numeros(texto_a, texto_b):
    """
    Compara números com peso alto, pois números são muito importantes
    para diferenciar respostas como '16 a 60 anos' e '18 a 70 anos'.
    """
    numeros_a = extrair_numeros(texto_a)
    numeros_b = extrair_numeros(texto_b)

    if not numeros_a:
        # Se a resposta correta não possui números, números extras na
        # alternativa não devem ser considerados evidência positiva.
        return 1.0 if not numeros_b else 0.0

    if not numeros_b:
        return 0.0

    # Correspondência exata mantendo a ordem.
    if numeros_a == numeros_b:
        return 1.0

    # Conta quantos números esperados aparecem exatamente na alternativa.
    usados = set()
    encontrados = 0

    for numero in numeros_a:
        for indice, candidato in enumerate(numeros_b):
            if indice in usados:
                continue
            if numero == candidato:
                encontrados += 1
                usados.add(indice)
                break

    # Correspondência parcial. Ex.: OCR pode ler '70' como '0'.
    # Um número correto ainda fornece alguma evidência, mas não recebe
    # a mesma pontuação de uma correspondência completa.
    if encontrados:
        return encontrados / max(len(numeros_a), len(numeros_b))

    return 0.0


def comparar_resposta_robusta(resposta_correta, resposta_tela):
    """
    Compara uma resposta do banco com uma alternativa OCR usando três
    sinais independentes: caracteres, palavras e números.

    Números recebem peso maior quando existem, evitando que duas respostas
    diferentes sejam consideradas iguais apenas porque compartilham palavras
    como 'a', 'de', 'anos', etc.
    """
    correta = normalizar_texto(resposta_correta)
    tela = normalizar_texto(resposta_tela)

    if not correta or not tela:
        return 0.0

    if correta == tela:
        return 1.0

    score_caracteres = difflib.SequenceMatcher(
        None, correta, tela
    ).ratio()

    score_palavras = similaridade_palavras(
        correta, tela
    )

    score_numeros = similaridade_numeros(
        correta, tela
    )

    numeros_correta = extrair_numeros(correta)

    if numeros_correta:
        # Número é a evidência mais forte para respostas numéricas.
        score = (
            score_caracteres * 0.25
            + score_palavras * 0.20
            + score_numeros * 0.55
        )

        # Se houver números na resposta correta e a alternativa tiver
        # números diferentes, aplica uma penalização adicional.
        numeros_tela = extrair_numeros(tela)
        if numeros_tela and score_numeros == 0.0:
            score *= 0.45

    else:
        score = (
            score_caracteres * 0.55
            + score_palavras * 0.45
        )

    return max(0.0, min(1.0, score))


# ============================================================
# BANCO
# ============================================================

def carregar_banco():
    global perguntas

    try:
        with open(BANCO_ARQUIVO, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)

        if isinstance(dados, dict):
            perguntas = {
                str(pergunta): str(resposta)
                for pergunta, resposta in dados.items()
            }
        elif isinstance(dados, list):
            perguntas = {}
            for item in dados:
                if not isinstance(item, dict):
                    continue
                pergunta = item.get("pergunta") or item.get("question")
                resposta = item.get("resposta") or item.get("answer")
                if pergunta and resposta:
                    perguntas[str(pergunta)] = str(resposta)
        else:
            raise ValueError(
                "perguntas.json precisa conter um objeto ou uma lista de perguntas."
            )

        return True, f"{len(perguntas)} pergunta(s) carregada(s)."

    except FileNotFoundError:
        perguntas = {}
        return False, (
            f"Arquivo {BANCO_ARQUIVO.name} não encontrado."
        )

    except json.JSONDecodeError as erro:
        perguntas = {}
        return False, (
            f"JSON inválido: linha {erro.lineno}, "
            f"coluna {erro.colno}."
        )

    except Exception as erro:
        perguntas = {}
        return False, f"Erro ao carregar banco: {erro}"


def encontrar_pergunta(pergunta_ocr):
    if not perguntas:
        return None, 0.0

    melhor_pergunta = None
    melhor_score = 0.0

    for pergunta in perguntas:
        score = similaridade(pergunta_ocr, pergunta)

        if score > melhor_score:
            melhor_score = score
            melhor_pergunta = pergunta

    if melhor_score >= LIMITE_MATCH:
        return melhor_pergunta, melhor_score

    return None, melhor_score


# ============================================================
# BRAVE
# ============================================================

def encontrar_brave():
    """Encontra a janela do Brave pelo título/classe."""
    if win32gui is None:
        raise RuntimeError("A biblioteca pywin32 não está instalada.")

    janelas = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindow(hwnd):
                return

            titulo = win32gui.GetWindowText(hwnd).strip()
            classe = win32gui.GetClassName(hwnd)

            if classe != "Chrome_WidgetWin_1":
                return

            if "wordwall bot" in titulo.lower():
                return

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            largura = right - left
            altura = bottom - top

            if largura <= 0 or altura <= 0:
                return

            # O Brave do usuário usa Chrome_WidgetWin_1 e o título termina em Brave.
            # Também aceitamos WordWall/Quiz para não depender do título exato.
            titulo_lower = titulo.lower()
            if (
                "brave" in titulo_lower
                or "wordwall" in titulo_lower
                or "quiz" in titulo_lower
                or (largura >= 700 and altura >= 400)
            ):
                janelas.append({
                    "hwnd": hwnd,
                    "titulo": titulo,
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "area": largura * altura,
                })
        except Exception as erro:
            print("Erro examinando janela:", erro)

    win32gui.EnumWindows(callback, None)

    if not janelas:
        raise RuntimeError("Não consegui localizar a janela do Brave.")

    ativa = win32gui.GetForegroundWindow()
    for janela in janelas:
        if janela["hwnd"] == ativa:
            return janela["hwnd"]

    # Se o bot estiver na frente, pega a maior janela candidata.
    return max(janelas, key=lambda j: j["area"])["hwnd"]


def obter_rect_brave(hwnd):
    if not win32gui.IsWindow(hwnd):
        raise RuntimeError("A janela do Brave não é válida.")

    if win32gui.IsIconic(hwnd):
        raise RuntimeError("O Brave está minimizado.")

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)

    if right <= left or bottom <= top:
        raise RuntimeError("Não foi possível obter o tamanho do Brave.")

    return left, top, right, bottom


def capturar_janela_brave(hwnd):
    """Captura a janela do Brave diretamente, sem capturar o bot por cima."""
    left, top, right, bottom = obter_rect_brave(hwnd)
    largura = right - left
    altura = bottom - top

    # PrintWindow + PW_RENDERFULLCONTENT é útil para Chromium/Brave.
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

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
        _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]

    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise RuntimeError("Não consegui obter o DC do Brave.")

    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, largura, altura)

    if not mem_dc or not bitmap:
        if mem_dc:
            gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
        raise RuntimeError("Não consegui criar a área de captura.")

    old_bitmap = gdi32.SelectObject(mem_dc, bitmap)

    try:
        PW_RENDERFULLCONTENT = 0x00000002
        resultado = user32.PrintWindow(
            hwnd,
            mem_dc,
            PW_RENDERFULLCONTENT,
        )

        if resultado != 1:
            # Segunda tentativa sem flag.
            resultado = user32.PrintWindow(hwnd, mem_dc, 0)

        if resultado != 1:
            raise RuntimeError("PrintWindow não conseguiu capturar o Brave.")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = largura
        bmi.bmiHeader.biHeight = -altura
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        buffer_size = largura * altura * 4
        buffer = ctypes.create_string_buffer(buffer_size)

        linhas = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            altura,
            buffer,
            ctypes.byref(bmi),
            0,
        )

        if linhas == 0:
            raise RuntimeError("GetDIBits não conseguiu ler a captura.")

        imagem = Image.frombuffer(
            "RGBA",
            (largura, altura),
            buffer,
            "raw",
            "BGRA",
            0,
            1,
        ).convert("RGB")

        return imagem.copy()

    finally:
        gdi32.SelectObject(mem_dc, old_bitmap)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def recortar_regiao_brave(imagem_brave, regiao):
    left = regiao["left"]
    top = regiao["top"]
    right = left + regiao["width"]
    bottom = top + regiao["height"]

    largura, altura = imagem_brave.size

    if left >= largura or top >= altura:
        raise RuntimeError(
            f"Região fora da captura: {regiao} / captura {largura}x{altura}"
        )

    right = min(right, largura)
    bottom = min(bottom, altura)

    if right <= left or bottom <= top:
        raise RuntimeError("Região de OCR ficou vazia.")

    return imagem_brave.crop((left, top, right, bottom))


# ============================================================
# OCR
# ============================================================

def preparar_imagem(imagem, escala=3, limiar=None):
    imagem = imagem.convert("RGB")

    imagem = imagem.resize(
        (
            imagem.width * escala,
            imagem.height * escala,
        ),
        Image.Resampling.LANCZOS,
    )

    imagem = ImageOps.grayscale(imagem)
    imagem = ImageEnhance.Contrast(imagem).enhance(2.5)
    imagem = ImageEnhance.Sharpness(imagem).enhance(2.5)

    if limiar is not None:
        imagem = imagem.point(
            lambda pixel: 255 if pixel >= limiar else 0
        )

    return imagem


def executar_ocr(imagem, psm=6):
    imagem_preparada = preparar_imagem(imagem, escala=3)

    config = f"--psm {psm}"

    texto = pytesseract.image_to_string(
        imagem_preparada,
        lang="por",
        config=config,
    )

    return texto.strip()


def executar_ocr_multiplas_passagens(imagem):
    """OCR específico para cartões de resposta do WordWall.

    Faz várias leituras com diferentes segmentações e escolhe uma leitura
    mais consistente, dando preferência a textos com palavras e números.
    """
    # Remove uma pequena margem para reduzir bordas/sombras dos cartões.
    w, h = imagem.size
    margem_x = max(4, int(w * 0.025))
    margem_y = max(4, int(h * 0.08))

    if w > 2 * margem_x and h > 2 * margem_y:
        imagem = imagem.crop((
            margem_x,
            margem_y,
            w - margem_x,
            h - margem_y,
        ))

    leituras = []

    # PSM 7: uma linha/bloco único. PSM 11: texto esparso.
    for psm in (7, 11, 6):
        try:
            texto = executar_ocr(imagem, psm=psm)
            if texto:
                leituras.append(texto)
        except Exception as erro:
            print("Aviso OCR resposta:", erro)

    # Leitura binarizada adicional, útil para texto claro sobre cartão colorido.
    try:
        binaria = preparar_imagem(imagem, escala=3, limiar=175)
        texto = pytesseract.image_to_string(
            binaria,
            lang="por",
            config="--psm 11",
        ).strip()
        if texto:
            leituras.append(texto)
    except Exception as erro:
        print("Aviso OCR binário:", erro)

    if not leituras:
        return ""

    def pontuacao_leitura(texto):
        normalizado = normalizar_texto(texto)
        palavras = extrair_palavras(normalizado)
        numeros = extrair_numeros(normalizado)

        # Penaliza lixo de uma única letra/símbolo e favorece conteúdo real.
        palavras_validas = [p for p in palavras if len(p) >= 2]
        score = len(palavras_validas) * 2.0
        score += len(numeros) * 3.0
        score += min(len(normalizado), 40) * 0.03

        if len(normalizado) <= 2:
            score -= 3

        return score

    # Se duas leituras forem praticamente iguais, mantém a mais completa.
    melhor = max(leituras, key=pontuacao_leitura)
    return melhor.strip()


def ler_numeros_da_imagem(imagem):
    """Faz uma OCR auxiliar somente para números.

    Essa leitura ajuda principalmente em casos como 120 -> 90 ou 70 -> 0.
    """
    w, h = imagem.size
    margem_x = max(4, int(w * 0.02))
    margem_y = max(4, int(h * 0.05))

    if w > 2 * margem_x and h > 2 * margem_y:
        imagem = imagem.crop((
            margem_x,
            margem_y,
            w - margem_x,
            h - margem_y,
        ))

    imagem = preparar_imagem(imagem, escala=4, limiar=180)

    try:
        texto = pytesseract.image_to_string(
            imagem,
            lang="por",
            config=(
                "--psm 11 "
                "-c tessedit_char_whitelist=0123456789"
            ),
        )
    except Exception:
        return []

    return extrair_numeros(texto)


def ler_tela_brave():
    """Captura somente o Brave e lê pergunta e duas respostas."""
    hwnd = encontrar_brave()
    imagem_brave = capturar_janela_brave(hwnd)

    imagem_pergunta = recortar_regiao_brave(
        imagem_brave,
        REGIAO_PERGUNTA,
    )

    imagem_resposta1 = recortar_regiao_brave(
        imagem_brave,
        REGIAO_RESPOSTA_1,
    )

    imagem_resposta2 = recortar_regiao_brave(
        imagem_brave,
        REGIAO_RESPOSTA_2,
    )

    pergunta = executar_ocr(
        imagem_pergunta,
        psm=6,
    )

    resposta1 = executar_ocr_multiplas_passagens(
        imagem_resposta1,
    )

    resposta2 = executar_ocr_multiplas_passagens(
        imagem_resposta2,
    )

    # Se a leitura normal não trouxe números, tenta a leitura exclusiva de dígitos.
    # Se trouxe números diferentes, também guarda a leitura auxiliar para diagnóstico.
    numeros1 = ler_numeros_da_imagem(imagem_resposta1)
    numeros2 = ler_numeros_da_imagem(imagem_resposta2)

    print("Números OCR auxiliar 1:", numeros1)
    print("Números OCR auxiliar 2:", numeros2)

    # Corrige números quando a leitura de dígitos é mais confiável que a OCR
    # geral. Ex.: "E 90 dias" pode ser reconhecido como "120 dias" na passada
    # exclusiva de números.
    def aplicar_numeros_auxiliares(texto, numeros):
        if not numeros:
            return texto

        numeros_atuais = extrair_numeros(texto)

        # Se a OCR geral já acertou exatamente os números, não altera.
        if numeros_atuais == numeros:
            return texto

        posicoes = list(re.finditer(r"\d+(?:[.,]\d+)?", texto))

        if posicoes and len(posicoes) == len(numeros):
            partes = []
            inicio = 0
            for match, numero in zip(posicoes, numeros):
                partes.append(texto[inicio:match.start()])
                partes.append(str(numero))
                inicio = match.end()
            partes.append(texto[inicio:])
            return "".join(partes)

        # Se a OCR geral não encontrou nenhum número, acrescenta os dígitos
        # auxiliares ao texto para que a comparação possa utilizá-los.
        if not posicoes:
            return (texto + " " + " ".join(numeros)).strip()

        # Quantidade incompatível: mantém a OCR geral e deixa a comparação
        # conservadora, evitando inventar números.
        return texto

    resposta1 = aplicar_numeros_auxiliares(
        resposta1, numeros1
    )

    resposta2 = aplicar_numeros_auxiliares(
        resposta2, numeros2
    )

    print("Resposta 1 após correção numérica:", resposta1)
    print("Resposta 2 após correção numérica:", resposta2)

    return (
        pergunta,
        resposta1,
        resposta2,
    )


# ============================================================
# CLIQUE
# ============================================================

def centro_regiao(regiao):
    return (
        regiao["left"] + regiao["width"] // 2,
        regiao["top"] + regiao["height"] // 2,
    )


def clicar_resposta(numero):
    """Clica no cartão escolhido usando coordenadas relativas ao Brave."""
    hwnd = encontrar_brave()
    left, top, _, _ = obter_rect_brave(hwnd)

    if numero == 1:
        rel_x, rel_y = centro_regiao(REGIAO_RESPOSTA_1)
    elif numero == 2:
        rel_x, rel_y = centro_regiao(REGIAO_RESPOSTA_2)
    else:
        raise ValueError("Resposta deve ser 1 ou 2.")

    x = left + rel_x
    y = top + rel_y

    print()
    print("=" * 60)
    print("CLIQUE AUTOMÁTICO")
    print("=" * 60)
    print("HWND:", hwnd)
    print("Resposta escolhida:", numero)
    print("Coordenada relativa:", rel_x, rel_y)
    print("Coordenada na tela:", x, y)
    print("=" * 60)

    try:
        win32gui.ShowWindow(hwnd, 3)
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.12)
    except Exception as erro:
        print("Aviso ao ativar Brave:", erro)

    pyautogui.moveTo(x, y, duration=0.08)
    time.sleep(0.05)
    pyautogui.click()
    print("CLIQUE ENVIADO!")


# ============================================================
# INTERFACE
# ============================================================

class WordwallBotApp:

    def __init__(self, root):
        self.root = root

        self.root.title("WORDWALL BOT")
        self.root.geometry("390x590")
        self.root.resizable(False, False)

        # Continua sempre por cima.
        # Isso não interfere na captura direta do Brave.
        self.root.attributes("-topmost", True)

        self.status_var = tk.StringVar(
            value="○ PARADO"
        )

        self.pergunta_var = tk.StringVar(
            value="-"
        )

        self.banco_var = tk.StringVar(
            value="-"
        )

        self.resposta1_var = tk.StringVar(
            value="-"
        )

        self.resposta2_var = tk.StringVar(
            value="-"
        )

        self.match1_var = tk.StringVar(
            value="0%"
        )

        self.match2_var = tk.StringVar(
            value="0%"
        )

        self.info_var = tk.StringVar(
            value="Pronto."
        )

        self.criar_interface()

        sucesso, mensagem = carregar_banco()

        self.info_var.set(mensagem)

        self.configurar_tesseract()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.fechar
        )

    def configurar_tesseract(self):

        caminho = Path(TESSERACT_PATH)

        if caminho.exists():
            pytesseract.pytesseract.tesseract_cmd = str(
                caminho
            )
            return

        try:
            pytesseract.get_tesseract_version()

        except Exception:
            self.info_var.set(
                "Tesseract não encontrado. "
                "Ajuste TESSERACT_PATH."
            )

    def criar_interface(self):

        # ----------------------------------------------------
        # CABEÇALHO FIXO
        # ----------------------------------------------------
        tk.Label(
            self.root,
            text="WORDWALL BOT",
            font=("Segoe UI", 18, "bold"),
        ).pack(pady=(12, 0))

        tk.Label(
            self.root,
            text="OCR + banco de perguntas",
            font=("Segoe UI", 9),
        ).pack(pady=(0, 10))

        tk.Frame(
            self.root,
            height=1,
        ).pack(
            fill="x",
            padx=15,
            pady=3,
        )

        tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Segoe UI", 14, "bold"),
        ).pack(pady=8)

        # ----------------------------------------------------
        # ÁREA ROLÁVEL
        # Os campos podem crescer sem esconder os botões.
        # ----------------------------------------------------
        area = tk.Frame(self.root)
        area.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(0, 4),
        )

        self.scroll_canvas = tk.Canvas(
            area,
            highlightthickness=0,
            borderwidth=0,
        )

        scrollbar = tk.Scrollbar(
            area,
            orient="vertical",
            command=self.scroll_canvas.yview,
        )

        self.scroll_canvas.configure(
            yscrollcommand=scrollbar.set
        )

        scrollbar.pack(
            side="right",
            fill="y",
        )

        self.scroll_canvas.pack(
            side="left",
            fill="both",
            expand=True,
        )

        self.conteudo_scroll = tk.Frame(
            self.scroll_canvas
        )

        janela_scroll = self.scroll_canvas.create_window(
            (0, 0),
            window=self.conteudo_scroll,
            anchor="nw",
        )

        def atualizar_scroll(_event=None):
            self.scroll_canvas.configure(
                scrollregion=self.scroll_canvas.bbox("all")
            )

        def ajustar_largura(event):
            self.scroll_canvas.itemconfigure(
                janela_scroll,
                width=event.width,
            )

        self.conteudo_scroll.bind(
            "<Configure>",
            atualizar_scroll,
        )

        self.scroll_canvas.bind(
            "<Configure>",
            ajustar_largura,
        )

        # Rodinha do mouse para rolar a interface.
        self.scroll_canvas.bind_all(
            "<MouseWheel>",
            lambda event: self.scroll_canvas.yview_scroll(
                int(-event.delta / 120),
                "units",
            ),
        )

        frame = self.conteudo_scroll
        frame.configure(padx=7)

        self.criar_campo(
            frame,
            "Pergunta encontrada:",
            self.pergunta_var,
        )

        self.criar_campo(
            frame,
            "Resposta do banco:",
            self.banco_var,
        )

        self.criar_campo_match(
            frame,
            "Resposta 1:",
            self.resposta1_var,
            self.match1_var,
        )

        self.criar_campo_match(
            frame,
            "Resposta 2:",
            self.resposta2_var,
            self.match2_var,
        )

        tk.Label(
            frame,
            text="Status:",
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(8, 0))

        tk.Label(
            frame,
            textvariable=self.info_var,
            justify="left",
            wraplength=350,
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        # ----------------------------------------------------
        # BOTÕES FIXOS
        # Ficam sempre visíveis mesmo quando a área de texto
        # fica grande.
        # ----------------------------------------------------
        botoes = tk.Frame(self.root)
        botoes.pack(pady=(3, 5))

        self.btn_iniciar = tk.Button(
            botoes,
            text="▶ INICIAR",
            width=14,
            height=2,
            command=self.iniciar,
        )
        self.btn_iniciar.grid(
            row=0,
            column=0,
            padx=4,
        )

        self.btn_parar = tk.Button(
            botoes,
            text="■ PARAR",
            width=14,
            height=2,
            command=self.parar,
        )
        self.btn_parar.grid(
            row=0,
            column=1,
            padx=4,
        )

        self.btn_ler = tk.Button(
            self.root,
            text="🔎 LER AGORA (não clica)",
            width=30,
            height=2,
            command=self.ler_agora,
        )
        self.btn_ler.pack(pady=(0, 10))

    def criar_campo(
        self,
        parent,
        titulo,
        variavel,
    ):

        frame = tk.Frame(parent)
        frame.pack(
            fill="x",
            pady=4,
        )

        tk.Label(
            frame,
            text=titulo,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")

        tk.Label(
            frame,
            textvariable=variavel,
            justify="left",
            anchor="w",
            wraplength=350,
            relief="groove",
            padx=7,
            pady=5,
        ).pack(fill="x")

    def criar_campo_match(
        self,
        parent,
        titulo,
        variavel,
        match_variavel,
    ):

        frame = tk.Frame(parent)
        frame.pack(
            fill="x",
            pady=4,
        )

        linha = tk.Frame(frame)
        linha.pack(fill="x")

        tk.Label(
            linha,
            text=titulo,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        tk.Label(
            linha,
            textvariable=match_variavel,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        tk.Label(
            frame,
            textvariable=variavel,
            justify="left",
            anchor="w",
            wraplength=350,
            relief="groove",
            padx=7,
            pady=5,
        ).pack(fill="x")

    def atualizar_ui(
        self,
        pergunta=None,
        banco=None,
        resposta1=None,
        resposta2=None,
        match1=None,
        match2=None,
        info=None,
        status=None,
    ):

        def atualizar():

            if pergunta is not None:
                self.pergunta_var.set(
                    pergunta or "-"
                )

            if banco is not None:
                self.banco_var.set(
                    banco or "-"
                )

            if resposta1 is not None:
                self.resposta1_var.set(
                    resposta1 or "-"
                )

            if resposta2 is not None:
                self.resposta2_var.set(
                    resposta2 or "-"
                )

            if match1 is not None:
                self.match1_var.set(
                    f"{match1 * 100:.0f}%"
                )

            if match2 is not None:
                self.match2_var.set(
                    f"{match2 * 100:.0f}%"
                )

            if info is not None:
                self.info_var.set(info)

            if status is not None:
                self.status_var.set(status)

        try:
            self.root.after(
                0,
                atualizar,
            )
        except tk.TclError:
            pass

    # ========================================================
    # INICIAR
    # ========================================================

    def iniciar(self):

        global rodando, thread_bot

        if rodando:
            return

        if not perguntas:
            sucesso, mensagem = carregar_banco()

            if not sucesso:
                messagebox.showerror(
                    "Banco de perguntas",
                    mensagem,
                )
                return

        try:
            pytesseract.get_tesseract_version()

        except Exception as erro:
            messagebox.showerror(
                "Tesseract OCR",
                "Não foi possível executar o Tesseract.\n\n"
                f"{erro}",
            )
            return

        if win32gui is None:
            messagebox.showerror(
                "Dependência",
                "Instale pywin32 com:\n\n"
                "py -m pip install pywin32",
            )
            return

        try:
            encontrar_brave()

        except Exception as erro:
            messagebox.showerror(
                "Brave",
                str(erro),
            )
            return

        rodando = True

        self.atualizar_ui(
            status="● ATIVO",
            info="Bot iniciado. Procurando pergunta...",
        )

        self.btn_iniciar.config(
            state="disabled"
        )

        self.btn_ler.config(
            state="disabled"
        )

        thread_bot = threading.Thread(
            target=self.loop_bot,
            daemon=True,
        )

        thread_bot.start()

    # ========================================================
    # PARAR
    # ========================================================

    def parar(self):

        global rodando

        rodando = False

        self.atualizar_ui(
            status="○ PARADO",
            info="Bot parado.",
        )

        self.btn_iniciar.config(
            state="normal"
        )

        self.btn_ler.config(
            state="normal"
        )

    # ========================================================
    # LER AGORA
    # ========================================================

    def ler_agora(self):

        if rodando:
            return

        self.btn_ler.config(
            state="disabled"
        )

        self.atualizar_ui(
            info="Capturando somente o Brave...",
        )

        threading.Thread(
            target=self._ler_agora_thread,
            daemon=True,
        ).start()

    def _ler_agora_thread(self):

        try:

            pergunta, resposta1, resposta2 = (
                ler_tela_brave()
            )

            pergunta_encontrada, score_pergunta = (
                encontrar_pergunta(pergunta)
            )

            resposta_banco = ""

            if pergunta_encontrada:
                resposta_banco = perguntas[
                    pergunta_encontrada
                ]

            match1 = similaridade(
                resposta_banco,
                resposta1,
            )

            match2 = similaridade(
                resposta_banco,
                resposta2,
            )

            if pergunta_encontrada:

                info = (
                    "Pergunta encontrada no banco: "
                    f"{score_pergunta * 100:.0f}%\n"
                    "Alternativa 1: "
                    f"{match1 * 100:.0f}%\n"
                    "Alternativa 2: "
                    f"{match2 * 100:.0f}%"
                )

            else:

                info = (
                    "⚠ Pergunta não encontrada no banco.\n"
                    f"Melhor correspondência: "
                    f"{score_pergunta * 100:.0f}%"
                )

            self.atualizar_ui(
                pergunta=pergunta,
                banco=resposta_banco,
                resposta1=resposta1,
                resposta2=resposta2,
                match1=match1,
                match2=match2,
                info=info,
            )

        except Exception as erro:

            self.atualizar_ui(
                info=f"⚠ Erro: {erro}",
            )

        finally:

            self.root.after(
                0,
                lambda: self.btn_ler.config(
                    state="normal"
                ),
            )

    # ========================================================
    # LOOP AUTOMÁTICO
    # ========================================================

    def loop_bot(self):

        global rodando

        pergunta_anterior = ""

        while rodando:

            try:

                pergunta, resposta1, resposta2 = (
                    ler_tela_brave()
                )

                pergunta_limpa = normalizar_texto(
                    pergunta
                )

                if len(pergunta_limpa) < 3:

                    self.atualizar_ui(
                        info="Aguardando pergunta..."
                    )

                    time.sleep(
                        INTERVALO_LOOP
                    )

                    continue

                # Evita processar a mesma questão.
                if pergunta_anterior:

                    semelhanca = similaridade(
                        pergunta,
                        pergunta_anterior,
                    )

                    if semelhanca >= 0.94:

                        self.atualizar_ui(
                            pergunta=pergunta,
                            info=(
                                "Aguardando mudança "
                                "da pergunta..."
                            ),
                        )

                        time.sleep(
                            INTERVALO_MUDANCA
                        )

                        continue

                pergunta_encontrada, score_pergunta = (
                    encontrar_pergunta(pergunta)
                )

                if not pergunta_encontrada:

                    self.atualizar_ui(
                        pergunta=pergunta,
                        banco="-",
                        resposta1=resposta1,
                        resposta2=resposta2,
                        match1=0.0,
                        match2=0.0,
                        info=(
                            "⚠ Pergunta não encontrada "
                            "no banco."
                        ),
                    )

                    time.sleep(
                        INTERVALO_LOOP
                    )

                    continue

                resposta_banco = perguntas[
                    pergunta_encontrada
                ]

                match1 = comparar_resposta_robusta(
                    resposta_banco,
                    resposta1,
                )

                match2 = comparar_resposta_robusta(
                    resposta_banco,
                    resposta2,
                )

                print()
                print("COMPARAÇÃO ROBUSTA")
                print(
                    f"Resposta 1: {resposta1!r} -> "
                    f"{match1 * 100:.1f}%"
                )
                print(
                    f"Resposta 2: {resposta2!r} -> "
                    f"{match2 * 100:.1f}%"
                )
                print(
                    "Números esperados:",
                    extrair_numeros(resposta_banco)
                )
                print(
                    "Números OCR 1:",
                    extrair_numeros(resposta1)
                )
                print(
                    "Números OCR 2:",
                    extrair_numeros(resposta2)
                )

                self.atualizar_ui(
                    pergunta=pergunta,
                    banco=resposta_banco,
                    resposta1=resposta1,
                    resposta2=resposta2,
                    match1=match1,
                    match2=match2,
                    info=(
                        "Pergunta encontrada: "
                        f"{score_pergunta * 100:.0f}%"
                    ),
                )

                if match1 >= match2:

                    melhor = match1
                    segundo = match2
                    escolha = 1

                else:

                    melhor = match2
                    segundo = match1
                    escolha = 2

                pode_clicar = (
                    melhor >= LIMITE_MATCH
                    and (melhor - segundo)
                    >= MARGEM_SEGURANCA
                    and bool(
                        normalizar_texto(
                            resposta_banco
                        )
                    )
                )

                if not pode_clicar:

                    self.atualizar_ui(
                        info=(
                            "⚠ Não encontrei a "
                            "resposta com segurança.\n"
                            f"Melhor: "
                            f"{melhor * 100:.0f}% | "
                            f"Segunda: "
                            f"{segundo * 100:.0f}%"
                        ),
                    )

                    time.sleep(
                        INTERVALO_LOOP
                    )

                    continue

                pergunta_anterior = pergunta

                self.atualizar_ui(
                    info=(
                        f"✓ Resposta {escolha} "
                        f"({melhor * 100:.0f}%). "
                        "Clicando..."
                    ),
                )

                if not rodando:
                    break

                clicar_resposta(
                    escolha
                )

                time.sleep(
                    INTERVALO_APOS_CLIQUE
                )

                # Espera a pergunta mudar.
                inicio = time.time()

                while rodando:

                    if (
                        time.time() - inicio
                        >= TIMEOUT_PROXIMA_PERGUNTA
                    ):

                        self.atualizar_ui(
                            info=(
                                "Tempo limite esperando "
                                "a próxima pergunta."
                            )
                        )

                        break

                    nova_pergunta, _, _ = (
                        ler_tela_brave()
                    )

                    if not nova_pergunta.strip():

                        time.sleep(
                            INTERVALO_MUDANCA
                        )

                        continue

                    mudanca = similaridade(
                        nova_pergunta,
                        pergunta_anterior,
                    )

                    if mudanca < 0.94:
                        break

                    time.sleep(
                        INTERVALO_MUDANCA
                    )

            except Exception as erro:

                self.atualizar_ui(
                    info=f"⚠ Erro: {erro}",
                )

                time.sleep(1)

        self.atualizar_ui(
            status="○ PARADO",
            info="Bot parado.",
        )

        try:

            self.root.after(
                0,
                lambda: self.btn_iniciar.config(
                    state="normal"
                ),
            )

            self.root.after(
                0,
                lambda: self.btn_ler.config(
                    state="normal"
                ),
            )

        except tk.TclError:
            pass

    # ========================================================
    # FECHAR
    # ========================================================

    def fechar(self):

        global rodando

        rodando = False

        try:
            self.root.destroy()
        except tk.TclError:
            pass


# ============================================================
# MAIN
# ============================================================

def main():

    pyautogui.PAUSE = 0.05

    root = tk.Tk()

    WordwallBotApp(root)

    root.mainloop()


if __name__ == "__main__":
    main()
