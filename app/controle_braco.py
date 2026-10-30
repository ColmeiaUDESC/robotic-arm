"""
Controle do braço robótico pelo PC.

Precisa de Python 3 e da biblioteca pyserial:
    pip install pyserial

Rode com:
    python controle_braco.py

- Conecta sozinho quando acha o Arduino no USB (e reconecta se o cabo soltar).
- Os arquivos .json da mesma pasta deste programa aparecem como botões em
  "Sequências salvas": clique para reproduzir na hora.
"""

import json
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Falta a biblioteca pyserial. Instale com:  pip install pyserial")
    raise SystemExit(1)

# Nome de cada servo na tela.
SERVOS = [
    "Base (D8)",
    "Ombro (D9)",
    "Cotovelo (D10)",
    "Punho (D11)",
    "Giro garra (D12)",
    "Garra (D13)",
]
INICIAL = [53, 23, 59, 114, 0, 10]          # igual ao braco_pc.ino (base, ombro, cotovelo, punho, giro, garra)
# (mínimo, máximo) de cada servo — mesmos valores de MINIMO/MAXIMO no braco_pc.ino
LIMITES = [
    (0, 180),    # base
    (0, 180),    # ombro
    (0, 180),    # cotovelo
    (0, 180),    # punho
    (0, 180),    # giro da garra
    (10, 110),   # garra: 10 = fechada, 110 = aberta
]
VELOCIDADES = [30, 24, 19, 15, 12, 9, 7, 5, 3, 2]   # ms por grau, do 1 (lento) ao 10 (rápido)
BAUD = 115200
N_SERVOS = len(SERVOS)

PASTA = Path(__file__).resolve().parent     # onde ficam os .json das sequências

# Fabricantes de chip USB usados em Arduino e clones (VID)
VIDS_ARDUINO = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4, 0x239A}


def portas_arduino():
    """Lista as portas que parecem ser um Arduino, a mais provável primeiro."""
    achadas = []
    for p in serial.tools.list_ports.comports():
        desc = f"{p.description or ''} {p.manufacturer or ''}".lower()
        nota = 0
        if p.vid in VIDS_ARDUINO:
            nota += 3
        if "arduino" in desc:
            nota += 3
        if any(k in desc for k in ("ch340", "usb serial", "usb-serial", "serial usb", "dispositivo serial")):
            nota += 2
        if "ttyacm" in p.device.lower() or "ttyusb" in p.device.lower():
            nota += 2
        if nota > 0:
            achadas.append((nota, p.device, p.description or ""))
    achadas.sort(reverse=True)
    return [(d, desc) for _, d, desc in achadas]


class Braco:
    """Conversa com o Arduino pela porta serial."""

    def __init__(self):
        self.porta = None
        self.trava = threading.Lock()
        self.chegou = threading.Event()
        self.recebido = queue.Queue()
        self._ler = False

    def conectar(self, nome):
        porta = serial.Serial(nome, BAUD, timeout=0.1)
        # Abrir a porta reinicia o Arduino; espera ele avisar que está pronto.
        fim = time.time() + 5
        while time.time() < fim:
            if porta.readline().decode(errors="ignore").strip() == "PRONTO":
                self.porta = porta
                self._ler = True
                threading.Thread(target=self._leitor, daemon=True).start()
                return True
        porta.close()
        return False

    def desconectar(self):
        self._ler = False
        if self.porta:
            try:
                self.porta.close()
            finally:
                self.porta = None

    @property
    def conectado(self):
        return self.porta is not None and self.porta.is_open

    def enviar(self, texto):
        if not self.conectado:
            return
        try:
            with self.trava:
                self.porta.write((texto + "\n").encode())
        except (serial.SerialException, OSError):
            self.recebido.put("ERRO")

    def _leitor(self):
        while self._ler and self.conectado:
            try:
                txt = self.porta.readline().decode(errors="ignore").strip()
            except (serial.SerialException, OSError, TypeError, AttributeError):
                self.recebido.put("ERRO")
                break
            if txt == "DONE":
                self.chegou.set()
            elif txt:
                self.recebido.put(txt)


class App:
    def __init__(self, raiz):
        self.raiz = raiz
        self.braco = Braco()
        self.valores = [tk.IntVar(value=a) for a in INICIAL]
        self.enviados = list(INICIAL)
        self.poses = []
        self.parar = threading.Event()
        self.tocando = False
        self.auto = True            # conectar sozinho quando achar o Arduino
        self.conectando = False
        self.ignoradas = {}         # portas que não responderam: porta -> hora para tentar de novo
        self.avisou_permissao = False

        raiz.title("Controle do Braço Robótico")
        raiz.minsize(560, 680)
        raiz.columnconfigure(0, weight=1)

        self._monta_conexao()
        self._monta_sliders()
        self._monta_biblioteca()
        self._monta_sequencia()

        self._atualiza_estado()
        self._atualiza_biblioteca()
        self.raiz.after(30, self._enviar_mudancas)
        self.raiz.after(100, self._processa_recebidos)
        self.raiz.after(300, self._procura_arduino)
        raiz.protocol("WM_DELETE_WINDOW", self._fechar)

    # ---------- interface ----------
    def _monta_conexao(self):
        f = ttk.LabelFrame(self.raiz, text="Conexão", padding=8)
        f.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        f.columnconfigure(0, weight=1)
        self.status = ttk.Label(f, text="Procurando o Arduino…", foreground="#a70")
        self.status.grid(row=0, column=0, sticky="w")
        self.bt_con = ttk.Button(f, text="Desconectar", command=self._botao_conexao)
        self.bt_con.grid(row=0, column=1, padx=(5, 0))

    def _monta_sliders(self):
        f = ttk.LabelFrame(self.raiz, text="Servos", padding=8)
        f.grid(row=1, column=0, sticky="ew", padx=10, pady=5)
        f.columnconfigure(1, weight=1)
        for i, nome in enumerate(SERVOS):
            ttk.Label(f, text=nome, width=16).grid(row=i, column=0, sticky="w")
            tk.Scale(f, from_=LIMITES[i][0], to=LIMITES[i][1], orient="horizontal", variable=self.valores[i],
                     showvalue=False, length=300).grid(row=i, column=1, sticky="ew", padx=5)
            ttk.Label(f, textvariable=self.valores[i], width=4).grid(row=i, column=2)

        linha = ttk.Frame(f)
        linha.grid(row=N_SERVOS, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(linha, text="Velocidade:").pack(side="left")
        self.vel = tk.IntVar(value=4)
        tk.Scale(linha, from_=1, to=10, orient="horizontal", variable=self.vel,
                 length=140, command=lambda _v: self._manda_velocidade()).pack(side="left", padx=5)
        ttk.Button(linha, text="Posição inicial", command=self._home).pack(side="right")

    def _monta_biblioteca(self):
        f = ttk.LabelFrame(self.raiz, text="Sequências salvas (clique para reproduzir)", padding=8)
        f.grid(row=2, column=0, sticky="ew", padx=10, pady=5)
        f.columnconfigure(0, weight=1)
        self.area_botoes = ttk.Frame(f)
        self.area_botoes.grid(row=0, column=0, sticky="ew")
        ttk.Button(f, text="Atualizar lista", command=self._atualiza_biblioteca).grid(row=0, column=1, sticky="ne",
                                                                                     padx=(8, 0))

    def _monta_sequencia(self):
        f = ttk.LabelFrame(self.raiz, text="Sequência atual", padding=8)
        f.grid(row=3, column=0, sticky="nsew", padx=10, pady=(5, 10))
        self.raiz.rowconfigure(3, weight=1)
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)

        self.lista = tk.Listbox(f, height=7, font=("Consolas", 10))
        self.lista.grid(row=0, column=0, sticky="nsew")
        self.lista.bind("<Double-Button-1>", lambda _e: self._ir_para_pose())

        b = ttk.Frame(f)
        b.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        ttk.Button(b, text="Gravar posição", command=self._gravar).pack(fill="x")
        ttk.Button(b, text="Apagar selecionada", command=self._apagar).pack(fill="x", pady=2)
        ttk.Button(b, text="Limpar tudo", command=self._limpar).pack(fill="x")
        ttk.Separator(b).pack(fill="x", pady=6)
        ttk.Button(b, text="Salvar como…", command=self._salvar).pack(fill="x")
        ttk.Button(b, text="Abrir de outra pasta", command=self._abrir).pack(fill="x", pady=2)

        p = ttk.Frame(f)
        p.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(p, text="Repetir:").pack(side="left")
        self.reps = tk.IntVar(value=1)
        ttk.Spinbox(p, from_=1, to=99, width=4, textvariable=self.reps).pack(side="left", padx=4)
        ttk.Label(p, text="Pausa (s):").pack(side="left", padx=(10, 0))
        self.pausa = tk.DoubleVar(value=0.3)
        ttk.Spinbox(p, from_=0, to=10, increment=0.1, width=5, textvariable=self.pausa).pack(side="left", padx=4)
        ttk.Button(p, text="Parar", command=self.parar.set).pack(side="right")
        self.bt_tocar = ttk.Button(p, text="Reproduzir", command=self._tocar)
        self.bt_tocar.pack(side="right", padx=5)
        ttk.Label(f, text="Dica: clique duas vezes numa posição da lista para ir até ela.",
                  foreground="#666").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    # ---------- conexão automática ----------
    def _procura_arduino(self):
        if self.auto and not self.braco.conectado and not self.conectando:
            agora = time.time()
            for porta, desc in portas_arduino():
                if self.ignoradas.get(porta, 0) > agora:
                    continue
                self._conectar_em(porta, desc)
                break
            else:
                self.status.config(text="Procurando o Arduino… (ligue o cabo USB)", foreground="#a70")
        self.raiz.after(1500, self._procura_arduino)

    def _conectar_em(self, porta, desc):
        self.conectando = True
        self.status.config(text=f"Conectando em {porta}… (o Arduino reinicia)", foreground="#a70")

        def tarefa():
            try:
                ok, erro = self.braco.conectar(porta), None
            except serial.SerialException as e:
                ok, erro = False, str(e)
            self.raiz.after(0, self._fim_conexao, porta, ok, erro)

        threading.Thread(target=tarefa, daemon=True).start()

    def _fim_conexao(self, porta, ok, erro):
        self.conectando = False
        if ok:
            self.ignoradas.pop(porta, None)
            self.enviados = list(INICIAL)
            for v, a in zip(self.valores, INICIAL):
                v.set(a)
            self._manda_velocidade()
        else:
            self.ignoradas[porta] = time.time() + 10      # tenta essa porta de novo daqui a 10 s
            if erro and ("ermission" in erro or "ermiss" in erro) and not self.avisou_permissao:
                self.avisou_permissao = True
                messagebox.showerror(
                    "Sem permissão na porta",
                    f"O Linux não deixou abrir {porta}.\n\nRode no terminal:\n"
                    "    sudo usermod -a -G dialout $USER\n\ne depois saia e entre de novo na sessão.")
            elif erro and "busy" in erro.lower():
                self.status.config(text=f"{porta} ocupada — feche o Monitor Serial da IDE", foreground="#a33")
                return
        self._atualiza_estado()

    def _botao_conexao(self):
        if self.braco.conectado or self.auto:
            self.auto = False
            self.parar.set()
            self.braco.desconectar()
        else:
            self.auto = True
            self.ignoradas.clear()
        self._atualiza_estado()

    def _atualiza_estado(self):
        if self.braco.conectado:
            self.status.config(text=f"Conectado em {self.braco.porta.port}", foreground="#2a7")
            self.bt_con.config(text="Desconectar")
        elif self.auto:
            self.bt_con.config(text="Parar busca")
            if not self.conectando:
                self.status.config(text="Procurando o Arduino… (ligue o cabo USB)", foreground="#a70")
        else:
            self.status.config(text="Desconectado", foreground="#a33")
            self.bt_con.config(text="Conectar")

    # ---------- envio ----------
    def _enviar_mudancas(self):
        # Manda só o que mudou, no máximo a cada 30 ms, para não entupir a serial
        if not self.tocando:
            for i, v in enumerate(self.valores):
                a = v.get()
                if a != self.enviados[i]:
                    self.braco.enviar(f"S {i + 1} {a}")
                    self.enviados[i] = a
        self.raiz.after(30, self._enviar_mudancas)

    def _manda_velocidade(self):
        self.braco.enviar(f"V {VELOCIDADES[self.vel.get() - 1]}")

    def _processa_recebidos(self):
        while not self.braco.recebido.empty():
            if self.braco.recebido.get() == "ERRO":
                self.parar.set()
                self.braco.desconectar()
                self._atualiza_estado()       # continua procurando e reconecta sozinho
        self.raiz.after(100, self._processa_recebidos)

    def _mostra_pose(self, pose):
        for v, a in zip(self.valores, pose):
            v.set(a)
        self.enviados = list(pose)

    def _home(self):
        self._mostra_pose(INICIAL)
        self.braco.enviar("H")

    # ---------- biblioteca de sequências ----------
    def _atualiza_biblioteca(self):
        for w in self.area_botoes.winfo_children():
            w.destroy()
        arquivos = sorted(PASTA.glob("*.json"), key=lambda p: p.stem.lower())
        if not arquivos:
            ttk.Label(self.area_botoes, text=f"Nenhum .json em {PASTA}", foreground="#666").grid(row=0, column=0)
            return
        for n, arq in enumerate(arquivos):
            ttk.Button(self.area_botoes, text="▶ " + arq.stem, width=16,
                       command=lambda a=arq: self._tocar_arquivo(a)).grid(row=n // 3, column=n % 3,
                                                                         sticky="ew", padx=2, pady=2)

    def _ler_arquivo(self, caminho):
        with open(caminho, encoding="utf-8") as f:
            dados = json.load(f)
        if isinstance(dados, dict):                 # formato novo: {"pausa": .., "poses": [..]}
            if "pausa" in dados:
                self.pausa.set(float(dados["pausa"]))
            dados = dados.get("poses", [])
        poses = []
        for p in dados:
            pose = [int(a) for a in p][:N_SERVOS]
            pose = [min(max(a, LIMITES[i][0]), LIMITES[i][1]) for i, a in enumerate(pose)]
            poses.append(pose)
        return poses

    def _tocar_arquivo(self, caminho):
        if self.tocando:
            self.parar.set()
            return
        try:
            self.poses = self._ler_arquivo(caminho)
        except (OSError, ValueError, TypeError) as e:
            messagebox.showerror("Arquivo inválido", f"Não deu para ler {caminho.name}.\n\n{e}")
            return
        self._redesenha_lista()
        self._tocar()

    # ---------- sequência atual ----------
    def _texto_pose(self, n, pose):
        return f"{n:>2}.  " + "  ".join(f"{a:>3}" for a in pose)

    def _redesenha_lista(self):
        self.lista.delete(0, "end")
        for n, pose in enumerate(self.poses, 1):
            self.lista.insert("end", self._texto_pose(n, pose))

    def _gravar(self):
        self.poses.append([v.get() for v in self.valores])
        self._redesenha_lista()
        self.lista.see("end")

    def _apagar(self):
        sel = self.lista.curselection()
        if sel:
            del self.poses[sel[0]]
            self._redesenha_lista()

    def _limpar(self):
        self.poses.clear()
        self._redesenha_lista()

    def _ir_para_pose(self):
        sel = self.lista.curselection()
        if sel and not self.tocando:
            pose = self.poses[sel[0]]
            self._mostra_pose(pose)
            self.braco.enviar("M " + " ".join(map(str, pose)))

    def _salvar(self):
        if not self.poses:
            messagebox.showinfo("Nada para salvar", "Grave pelo menos uma posição antes.")
            return
        nome = simpledialog.askstring("Salvar sequência", "Nome da sequência (ex.: acenar):", parent=self.raiz)
        if not nome:
            return
        nome = "".join(c for c in nome.strip() if c.isalnum() or c in " _-").strip() or "sequencia"
        caminho = PASTA / f"{nome}.json"
        if caminho.exists() and not messagebox.askyesno("Substituir?", f"Já existe '{nome}'. Substituir?"):
            return
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump({"pausa": round(self.pausa.get(), 2), "poses": self.poses}, f)
        self._atualiza_biblioteca()

    def _abrir(self):
        nome = filedialog.askopenfilename(initialdir=PASTA, filetypes=[("Sequência", "*.json")])
        if nome:
            try:
                self.poses = self._ler_arquivo(nome)
            except (OSError, ValueError, TypeError) as e:
                messagebox.showerror("Arquivo inválido", str(e))
                return
            self._redesenha_lista()

    def _tocar(self):
        if self.tocando or not self.poses:
            return
        if not self.braco.conectado:
            messagebox.showwarning("Sem Arduino", "O Arduino ainda não está conectado.")
            return
        self.parar.clear()
        self.tocando = True
        self.bt_tocar.state(["disabled"])
        threading.Thread(target=self._tocar_thread, args=(list(self.poses), self.reps.get(), self.pausa.get()),
                         daemon=True).start()

    def _tocar_thread(self, poses, reps, pausa):
        try:
            for _ in range(reps):
                for n, pose in enumerate(poses):
                    if self.parar.is_set() or not self.braco.conectado:
                        return
                    self.raiz.after(0, self._marca, n, pose)
                    self.braco.chegou.clear()
                    self.braco.enviar("M " + " ".join(map(str, pose)))
                    self.braco.chegou.wait(timeout=20)
                    time.sleep(pausa)
        finally:
            self.raiz.after(0, self._fim_tocar)

    def _marca(self, n, pose):
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(n)
        self.lista.see(n)
        self._mostra_pose(pose)

    def _fim_tocar(self):
        self.tocando = False
        self.enviados = [v.get() for v in self.valores]
        self.bt_tocar.state(["!disabled"])

    def _fechar(self):
        self.parar.set()
        self.braco.desconectar()
        self.raiz.destroy()


if __name__ == "__main__":
    raiz = tk.Tk()
    App(raiz)
    raiz.mainloop()
