#!/usr/bin/env python3
"""
Outbreak Server Setup  -  Linux & Windows
Interface gráfica para instalar, configurar e controlar o servidor local do
Resident Evil Outbreak (File #1 e #2) via Podman (Linux) ou Docker (Windows).
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import json
import os
import sys
import shutil
import time
import re

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
IS_WINDOWS = sys.platform == "win32"
IS_LINUX   = sys.platform.startswith("linux")

def _app_dir():
    """Pasta onde o executável/script realmente está (funciona tanto rodando
    como .py quanto como executável do PyInstaller, desde que o Dockerfile
    e a pasta config/ estejam ao lado do executável, ex: dentro do .zip)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

APP_DIR        = _app_dir()
CONFIG_FILE    = os.path.join(APP_DIR, "server-config.json")
DOCKERFILE_DIR = APP_DIR
IMAGE_NAME     = "bioserver-docker"
CONTAINER_NAME = "bioserver"
PORTS = [
    ("53",   "53",   "udp"), ("53",   "53",   "tcp"),
    ("80",   "80",   "tcp"), ("443",  "443",  "tcp"),
    ("8200", "8200", "tcp"), ("8300", "8300", "tcp"),
    ("8590", "8590", "tcp"), ("8690", "8690", "tcp"),
]

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                return {**{"server_ip": "", "sudo_password": ""}, **json.load(f)}
        except Exception:
            pass
    return {"server_ip": "", "sudo_password": ""}

def save_config(cfg):
    data = {k: v for k, v in cfg.items() if k != "sudo_password"}
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ---------------------------------------------------------------------------
# Detecção de IP (cross-platform)
# ---------------------------------------------------------------------------
def detect_ips():
    """Retorna dict {label: ip} com IPs disponíveis."""
    ips = {}
    if IS_WINDOWS:
        try:
            out = subprocess.check_output("ipconfig", text=True, stderr=subprocess.DEVNULL)
            current_adapter = ""
            for line in out.splitlines():
                if "adapter" in line.lower() or "adaptador" in line.lower():
                    current_adapter = line.strip().rstrip(":")
                if "ipv4" in line.lower() or "endereço ip" in line.lower():
                    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", line)
                    if match:
                        ip = match.group(1)
                        if ip.startswith("25.") or ip.startswith("5."):
                            ips["Hamachi (VPN)"] = ip
                        elif not ip.startswith("127.") and not ip.startswith("169."):
                            ips.setdefault("Rede Local (LAN)", ip)
        except Exception:
            pass
    else:
        # Hamachi
        try:
            out = subprocess.check_output(
                ["ip", "-4", "addr", "show", "ham0"],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if line.strip().startswith("inet "):
                    ips["Hamachi (VPN)"] = line.strip().split()[1].split("/")[0]
        except Exception:
            pass
        # LAN / gateway default
        try:
            out = subprocess.check_output(
                ["ip", "-4", "route", "get", "8.8.8.8"],
                text=True, stderr=subprocess.DEVNULL
            )
            for line in out.splitlines():
                if "src" in line:
                    parts = line.split()
                    ip = parts[parts.index("src") + 1]
                    if not ip.startswith("25."):
                        ips.setdefault("Rede Local (LAN)", ip)
        except Exception:
            pass
    return ips

# ---------------------------------------------------------------------------
# Backend de container (abstrai podman/docker e sudo)
# ---------------------------------------------------------------------------
class Backend:
    """
    No Linux usa 'sudo podman' (rootful, necessário pra porta 53).
    No Windows usa 'docker' (Docker Desktop, já roda elevado como serviço).
    """

    def __init__(self, sudo_pw=""):
        self.sudo_pw = sudo_pw
        if IS_LINUX:
            self.cmd_prefix = ["sudo", "-S", "podman"]
        else:
            self.cmd_prefix = ["docker"]

    def _run(self, args, log_func=None, extra_input=None, **kwargs):
        cmd = self.cmd_prefix + args
        stdin_data = None
        if IS_LINUX and self.sudo_pw:
            stdin_data = (self.sudo_pw + "\n").encode()
        if extra_input:
            stdin_data = (stdin_data or b"") + extra_input

        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            **kwargs
        )
        try:
            out, _ = proc.communicate(input=stdin_data, timeout=900)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
        output = out.decode(errors="replace")
        for line in output.splitlines():
            l = line.rstrip()
            if l and "password" not in l.lower():
                if log_func:
                    log_func(l)
        return proc.returncode, output

    def image_exists(self):
        rc, _ = self._run(["image", "exists", IMAGE_NAME])
        return rc == 0

    def container_exists(self):
        rc, _ = self._run(["container", "exists", CONTAINER_NAME])
        return rc == 0

    def container_running(self):
        rc, out = self._run(
            ["ps", "-q", "--filter", f"name={CONTAINER_NAME}"]
        )
        return bool(out.strip())

    def start(self, ip, log_func=None):
        if self.container_exists():
            return self._run(["container", "start", CONTAINER_NAME], log_func)
        # cria novo container
        port_flags = []
        for h, c, proto in PORTS:
            port_flags += ["-p", f"{ip}:{h}:{c}/{proto}"]
        rc, out = self._run(
            ["container", "create", "--name", CONTAINER_NAME]
            + port_flags + [f"{IMAGE_NAME}:latest"],
            log_func
        )
        if rc != 0:
            return rc, out
        return self._run(["container", "start", CONTAINER_NAME], log_func)

    def stop(self, log_func=None):
        return self._run(["container", "stop", CONTAINER_NAME], log_func)

    def build_image(self, log_func=None):
        """Constrói a imagem. No Linux: build rootless + transfere pra root."""
        if IS_LINUX:
            # build rootless
            proc = subprocess.Popen(
                ["podman", "build", "-t", IMAGE_NAME, DOCKERFILE_DIR],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=DOCKERFILE_DIR
            )
            for line in iter(proc.stdout.readline, ""):
                l = line.rstrip()
                if l and log_func:
                    log_func(l)
            proc.wait()
            if proc.returncode != 0:
                return proc.returncode, "Build falhou"
            # transfere pra storage root
            if log_func:
                log_func("📤 Transferindo imagem pro Podman root...")
            save_proc = subprocess.Popen(
                ["podman", "save", f"{IMAGE_NAME}:latest"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            load_cmd = ["sudo", "-S", "podman", "load"]
            load_proc = subprocess.Popen(
                load_cmd, stdin=save_proc.stdout,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            if self.sudo_pw:
                load_proc.stdin  # já fechado por Popen via pipe
            out, _ = load_proc.communicate()
            save_proc.wait()
            if log_func:
                for l in out.decode(errors="replace").splitlines():
                    if l.strip() and "password" not in l.lower():
                        log_func(l)
            return load_proc.returncode, out.decode(errors="replace")
        else:
            # Windows: docker build direto
            proc = subprocess.Popen(
                ["docker", "build", "-t", IMAGE_NAME, DOCKERFILE_DIR],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=DOCKERFILE_DIR
            )
            for line in iter(proc.stdout.readline, ""):
                l = line.rstrip()
                if l and log_func:
                    log_func(l)
            proc.wait()
            return proc.returncode, ""

# ---------------------------------------------------------------------------
# AppArmor (Fedora/Ubuntu/Debian costumam ter um perfil php-fpm que bloqueia
# o servidor mesmo rodando dentro do container — corrige automaticamente)
# ---------------------------------------------------------------------------
APPARMOR_OVERRIDE = """  # Allow access to the Outbreak DNAS server web root (auto-added by setup app)
  /var/www/dnas/ r,
  /var/www/dnas/** rmix,
  /var/www/bhof1/ r,
  /var/www/bhof1/** rmix,
  /var/www/bhof2/ r,
  /var/www/bhof2/** rmix,
"""

def fix_apparmor_if_needed(sudo_pw, log_func=None):
    """Se o host tiver AppArmor com um perfil php-fpm, libera os caminhos
    do servidor automaticamente (sem isso o login do jogo trava com 403)."""
    if not IS_LINUX:
        return
    if not shutil.which("aa-status") and not os.path.isdir("/etc/apparmor.d"):
        return  # AppArmor não instalado nesse sistema, nada a fazer
    profile_path = "/etc/apparmor.d/php-fpm"
    found = os.path.exists(profile_path)
    if not found:
        for alt in ["/etc/apparmor.d/usr.sbin.php-fpm7.4", "/etc/apparmor.d/usr.sbin.php-fpm"]:
            if os.path.exists(alt):
                profile_path = alt
                found = True
                break
    if not found:
        return  # sem perfil php-fpm confinando nada, não precisa de override

    override_path = "/etc/apparmor.d/local/php-fpm"
    try:
        with open("/tmp/_outbreak_apparmor_override", "w") as f:
            f.write(APPARMOR_OVERRIDE)
    except Exception:
        return

    if log_func:
        log_func("🛡  Detectado AppArmor com perfil php-fpm — liberando acesso automaticamente...")

    cmd = (
        "mkdir -p /etc/apparmor.d/local && "
        f"cp /tmp/_outbreak_apparmor_override {override_path} && "
        f"apparmor_parser -r {profile_path}"
    )
    proc = subprocess.Popen(
        ["sudo", "-S", "bash", "-c", cmd],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    out, _ = proc.communicate(input=(sudo_pw + "\n") if sudo_pw else None)
    if log_func:
        for line in out.splitlines():
            if line.strip() and "password" not in line.lower():
                log_func(line.strip())
    if proc.returncode == 0 and log_func:
        log_func("✅ AppArmor configurado — login do jogo não vai travar com erro 403.")

# ---------------------------------------------------------------------------
# Instalação de dependências
# ---------------------------------------------------------------------------
def detect_package_manager():
    for pm in ["dnf", "apt-get", "pacman", "zypper"]:
        if shutil.which(pm):
            return pm
    return None

def install_podman_linux(sudo_pw, log_func=None):
    pm = detect_package_manager()
    if not pm:
        if log_func:
            log_func("❌ Gerenciador de pacotes não encontrado.")
        return False
    cmd_map = {
        "dnf":     ["dnf", "install", "-y", "podman"],
        "apt-get": ["apt-get", "install", "-y", "podman"],
        "pacman":  ["pacman", "-S", "--noconfirm", "podman"],
        "zypper":  ["zypper", "--non-interactive", "install", "podman"],
    }
    full_cmd = ["sudo", "-S"] + cmd_map[pm]
    proc = subprocess.Popen(
        full_cmd, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    if sudo_pw:
        proc.stdin.write(sudo_pw + "\n")
        proc.stdin.flush()
    for line in iter(proc.stdout.readline, ""):
        l = line.rstrip()
        if l and "password" not in l.lower() and log_func:
            log_func(l)
    proc.wait()
    return proc.returncode == 0

def install_docker_windows(log_func=None):
    """Tenta instalar Docker Desktop via winget."""
    if shutil.which("winget"):
        if log_func:
            log_func("📦 Instalando Docker Desktop via winget...")
        proc = subprocess.Popen(
            ["winget", "install", "--id", "Docker.DockerDesktop",
             "-e", "--accept-source-agreements", "--accept-package-agreements"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        for line in iter(proc.stdout.readline, ""):
            l = line.rstrip()
            if l and log_func:
                log_func(l)
        proc.wait()
        return proc.returncode == 0
    else:
        if log_func:
            log_func("⚠  winget não disponível.")
            log_func("   Baixe e instale o Docker Desktop manualmente:")
            log_func("   https://www.docker.com/products/docker-desktop/")
        return False

def is_runtime_installed():
    """Verifica se podman (Linux) ou docker (Windows) está disponível."""
    if IS_LINUX:
        return shutil.which("podman") is not None
    else:
        return shutil.which("docker") is not None

# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class OutbreakServerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Servidor Resident Evil Outbreak")
        self.resizable(False, False)
        self.cfg = load_config()
        self._backend = None
        self._build_ui()
        self._detect_ip_if_missing()
        self._refresh_status()

    def _get_backend(self):
        pw = self.pw_var.get()
        if self._backend is None or self._backend.sudo_pw != pw:
            self._backend = Backend(sudo_pw=pw)
        return self._backend

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        P = 10
        self.configure(bg="#1a1a2e")

        # cabeçalho
        hdr = tk.Frame(self, bg="#16213e")
        hdr.pack(fill="x")
        tk.Label(hdr, text="🎮  OUTBREAK SERVER SETUP",
                 font=("Consolas", 14, "bold"), fg="#e94560",
                 bg="#16213e", pady=8).pack()
        platform_label = "Linux  •  Podman" if IS_LINUX else "Windows  •  Docker Desktop"
        tk.Label(hdr, text=f"Resident Evil Outbreak File #1 e #2  —  {platform_label}",
                 font=("Consolas", 9), fg="#a0a0c0", bg="#16213e", pady=2).pack()
        tk.Label(hdr, text="Criado por Wesley Menegoni",
                 font=("Consolas", 8), fg="#606080", bg="#16213e", pady=(0, 6)).pack()

        # status
        sf = tk.LabelFrame(self, text="Status", bg="#1a1a2e", fg="#a0a0c0",
                           font=("Consolas", 9), padx=P, pady=6)
        sf.pack(fill="x", padx=P, pady=(P, 0))
        self.status_label = tk.Label(sf, text="⏳ Verificando...",
                                     font=("Consolas", 11, "bold"),
                                     fg="#f0c040", bg="#1a1a2e")
        self.status_label.pack(anchor="w")
        self.runtime_label = tk.Label(sf, text="", font=("Consolas", 9),
                                      fg="#808080", bg="#1a1a2e")
        self.runtime_label.pack(anchor="w")
        self.image_label = tk.Label(sf, text="", font=("Consolas", 9),
                                    fg="#808080", bg="#1a1a2e")
        self.image_label.pack(anchor="w")

        # configuração
        cf = tk.LabelFrame(self, text="Configuração", bg="#1a1a2e", fg="#a0a0c0",
                           font=("Consolas", 9), padx=P, pady=6)
        cf.pack(fill="x", padx=P, pady=(8, 0))

        tk.Label(cf, text="IP do servidor:", font=("Consolas", 10),
                 fg="#c0c0e0", bg="#1a1a2e").grid(row=0, column=0, sticky="w", pady=2)
        self.ip_var = tk.StringVar(value=self.cfg.get("server_ip", ""))
        tk.Entry(cf, textvariable=self.ip_var, font=("Consolas", 11),
                 width=17, bg="#0f3460", fg="#e0e0ff",
                 insertbackground="white", relief="flat", bd=4
                 ).grid(row=0, column=1, sticky="w", padx=6, pady=2)
        tk.Button(cf, text="🔍 Detectar", command=self._detect_ip,
                  font=("Consolas", 9), bg="#0f3460", fg="#a0c0ff",
                  relief="flat", cursor="hand2"
                  ).grid(row=0, column=2, padx=4)
        tk.Label(cf,
                 text="Hamachi/VPN (amigos via VPN),  LAN (mesma rede),  ou IP público (porta aberta).",
                 font=("Consolas", 8), fg="#606080", bg="#1a1a2e", justify="left"
                 ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 4))

        if IS_LINUX:
            tk.Label(cf, text="Senha sudo:", font=("Consolas", 10),
                     fg="#c0c0e0", bg="#1a1a2e").grid(row=2, column=0, sticky="w", pady=2)
            self.pw_var = tk.StringVar()
            tk.Entry(cf, textvariable=self.pw_var, show="*",
                     font=("Consolas", 11), width=17, bg="#0f3460", fg="#e0e0ff",
                     insertbackground="white", relief="flat", bd=4
                     ).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        else:
            self.pw_var = tk.StringVar()  # não usado no Windows

        # botões de setup
        bf = tk.Frame(self, bg="#1a1a2e")
        bf.pack(fill="x", padx=P, pady=8)
        runtime_name = "Podman" if IS_LINUX else "Docker Desktop"
        self.btn_install = tk.Button(bf, text=f"📦  Instalar {runtime_name}",
                                     command=self._install_runtime,
                                     font=("Consolas", 10, "bold"),
                                     bg="#0f3460", fg="#60a0ff",
                                     relief="flat", cursor="hand2", pady=6, padx=8)
        self.btn_install.pack(side="left", padx=(0, 6))
        self.btn_build = tk.Button(bf, text="🔨  Construir Imagem",
                                   command=self._build_image,
                                   font=("Consolas", 10, "bold"),
                                   bg="#1a3a1a", fg="#60d060",
                                   relief="flat", cursor="hand2", pady=6, padx=8)
        self.btn_build.pack(side="left", padx=6)

        # ligar / desligar
        ctrl = tk.Frame(self, bg="#1a1a2e")
        ctrl.pack(fill="x", padx=P, pady=(0, 4))
        self.btn_start = tk.Button(ctrl, text="▶  LIGAR SERVIDOR",
                                   command=self._start,
                                   font=("Consolas", 12, "bold"),
                                   bg="#1a4020", fg="#40e060",
                                   relief="flat", cursor="hand2", pady=8, state="disabled")
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_stop = tk.Button(ctrl, text="■  DESLIGAR",
                                  command=self._stop,
                                  font=("Consolas", 12, "bold"),
                                  bg="#3a1010", fg="#e04040",
                                  relief="flat", cursor="hand2", pady=8, state="disabled")
        self.btn_stop.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ajuda rápida
        hf = tk.LabelFrame(self, text="Como usar — passo a passo", bg="#1a1a2e", fg="#a0a0c0",
                           font=("Consolas", 9), padx=P, pady=4)
        hf.pack(fill="x", padx=P, pady=(0, 4))
        steps = (
            "1️⃣  Instale o Docker/Podman se o botão acima estiver disponível.\n"
            "2️⃣  Clique em Construir Imagem (só precisa fazer isso UMA vez).\n"
            "3️⃣  Escolha seu IP (Hamachi, rede local ou IP público).\n"
            "4️⃣  Clique em LIGAR SERVIDOR antes de jogar.\n"
            "5️⃣  Cada jogador configura DNS1 = seu IP no PCSX2 e dentro do jogo.\n"
            "6️⃣  Clique em DESLIGAR quando terminar — não consome PC parado."
        )
        tk.Label(hf, text=steps, font=("Consolas", 8), fg="#8090b0",
                 bg="#1a1a2e", justify="left").pack(anchor="w")

        # log
        lf = tk.LabelFrame(self, text="Log", bg="#1a1a2e", fg="#a0a0c0",
                            font=("Consolas", 9), padx=4, pady=4)
        lf.pack(fill="both", expand=True, padx=P, pady=(0, P))
        self.log_box = scrolledtext.ScrolledText(
            lf, height=12, font=("Consolas", 9),
            bg="#0d0d1a", fg="#90d090", insertbackground="white",
            relief="flat", state="disabled"
        )
        self.log_box.pack(fill="both", expand=True)
        tk.Button(lf, text="Limpar log", command=self._clear_log,
                  font=("Consolas", 8), bg="#1a1a2e", fg="#606080",
                  relief="flat", cursor="hand2").pack(anchor="e", pady=(2, 0))

    # --------------------------------------------------------------- helpers
    def _log(self, msg, color="#90d090"):
        def _do():
            self.log_box.configure(state="normal")
            ts = time.strftime("%H:%M:%S")
            self.log_box.insert("end", f"[{ts}] {msg}\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(0, _do)

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _set_status(self, text, color="#f0c040"):
        self.after(0, lambda: self.status_label.configure(text=text, fg=color))

    def _run_bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _detect_ip_if_missing(self):
        if not self.ip_var.get():
            ips = detect_ips()
            if ips:
                self.ip_var.set(next(iter(ips.values())))

    def _detect_ip(self):
        ips = detect_ips()
        if not ips:
            messagebox.showinfo(
                "Nenhum IP detectado",
                "Não encontrei IP automático.\n"
                "Digite manualmente o IP que seus amigos vão usar pra conectar."
            )
            return
        win = tk.Toplevel(self)
        win.title("Selecionar IP")
        win.configure(bg="#1a1a2e")
        win.resizable(False, False)
        tk.Label(win, text="Escolha o IP do servidor:",
                 font=("Consolas", 10), fg="#c0c0e0", bg="#1a1a2e"
                 ).pack(padx=16, pady=(12, 4))
        desc_map = {
            "Hamachi (VPN)":    "Amigos precisam estar na mesma rede Hamachi/VPN",
            "Rede Local (LAN)": "Funciona só em casa (mesma rede Wi-Fi / cabo)",
        }
        for label, ip in ips.items():
            desc = desc_map.get(label, "")
            f = tk.Frame(win, bg="#0f3460", cursor="hand2")
            f.pack(fill="x", padx=12, pady=3)
            tk.Label(f, text=f"{label}:  {ip}",
                     font=("Consolas", 11, "bold"), fg="#e0e0ff", bg="#0f3460"
                     ).pack(anchor="w", padx=8, pady=(6, 0))
            tk.Label(f, text=desc, font=("Consolas", 8),
                     fg="#8090b0", bg="#0f3460"
                     ).pack(anchor="w", padx=8, pady=(0, 6))
            def click(e, i=ip, w=win):
                self.ip_var.set(i)
                self._log(f"IP selecionado: {i}")
                w.destroy()
            f.bind("<Button-1>", click)
            for ch in f.winfo_children():
                ch.bind("<Button-1>", click)
        tk.Label(win, text="— ou —", font=("Consolas", 8),
                 fg="#505070", bg="#1a1a2e").pack()
        tk.Button(win, text="Digitar manualmente →", command=win.destroy,
                  font=("Consolas", 9), bg="#1a1a2e", fg="#606080",
                  relief="flat", cursor="hand2").pack(pady=(0, 10))

    def _get_ip(self):
        ip = self.ip_var.get().strip()
        if not ip:
            messagebox.showerror("IP necessário",
                                 "Configure o IP do servidor antes de continuar.")
            return None
        return ip

    # --------------------------------------------------------------- refresh
    def _refresh_status(self):
        self._run_bg(self._refresh_bg)

    def _refresh_bg(self):
        b = self._get_backend()
        has_rt   = is_runtime_installed()
        has_img  = b.image_exists() if has_rt else False
        running  = b.container_running() if has_img else False
        rt_name  = "Podman" if IS_LINUX else "Docker Desktop"

        rt_txt   = f"✅ {rt_name} instalado" if has_rt else f"❌ {rt_name} não encontrado"
        img_txt  = ("✅ Imagem construída" if has_img
                    else ("⚠  Imagem não construída" if has_rt else ""))
        rt_color  = "#60d060" if has_rt  else "#e04040"
        img_color = "#60d060" if has_img else "#f0c040"

        self.after(0, lambda: self.runtime_label.configure(text=rt_txt, fg=rt_color))
        self.after(0, lambda: self.image_label.configure(text=img_txt, fg=img_color))
        self.after(0, lambda: self.btn_install.configure(
            state="normal" if not has_rt else "disabled"))
        self.after(0, lambda: self.btn_build.configure(
            state="normal" if has_rt else "disabled"))
        self.after(0, lambda: self.btn_start.configure(
            state="normal" if (has_img and not running) else "disabled"))
        self.after(0, lambda: self.btn_stop.configure(
            state="normal" if running else "disabled"))

        if running:
            self._set_status("🟢  SERVIDOR LIGADO", "#40e060")
        elif has_img:
            self._set_status("🔴  Servidor desligado", "#e06060")
        elif has_rt:
            self._set_status("⚠  Imagem não construída", "#f0c040")
        else:
            self._set_status(f"❌  {rt_name} não instalado", "#e04040")

    # --------------------------------------------------------------- instalar runtime
    def _install_runtime(self):
        if IS_LINUX and not self.pw_var.get():
            messagebox.showerror("Senha necessária",
                                 "Informe a senha sudo pra instalar o Podman.")
            return
        self._run_bg(self._install_runtime_bg)

    def _install_runtime_bg(self):
        self.after(0, lambda: self.btn_install.configure(state="disabled"))
        if IS_LINUX:
            self._log("📦 Instalando Podman...")
            ok = install_podman_linux(self.pw_var.get(), self._log)
        else:
            self._log("📦 Instalando Docker Desktop...")
            ok = install_docker_windows(self._log)
            if ok:
                self._log("⚠  Docker Desktop pode precisar de reiniciar o PC.", "#f0c040")
        self._log("✅ Instalação concluída!" if ok else "❌ Falha na instalação", "#40e060" if ok else "#e04040")
        self._refresh_status()

    # --------------------------------------------------------------- build
    def _build_image(self):
        if IS_LINUX and not self.pw_var.get():
            messagebox.showerror("Senha necessária",
                                 "Informe a senha sudo pra construir a imagem.")
            return
        if not os.path.exists(os.path.join(DOCKERFILE_DIR, "Dockerfile")):
            messagebox.showerror("Dockerfile não encontrado",
                                 f"Coloque o app na mesma pasta do Dockerfile:\n{DOCKERFILE_DIR}")
            return
        self._run_bg(self._build_image_bg)

    def _build_image_bg(self):
        self.after(0, lambda: self.btn_build.configure(state="disabled"))
        self._log("🔨 Construindo imagem (primeira vez pode demorar ~15 minutos)...")
        b = self._get_backend()
        rc, _ = b.build_image(self._log)
        if rc == 0:
            self._log("✅ Imagem construída com sucesso!", "#40e060")
            if IS_LINUX:
                fix_apparmor_if_needed(self.pw_var.get(), self._log)
        else:
            self._log(f"❌ Build falhou (código {rc})", "#e04040")
        self._refresh_status()

    # --------------------------------------------------------------- start/stop
    def _start(self):
        ip = self._get_ip()
        if not ip:
            return
        if IS_LINUX and not self.pw_var.get():
            messagebox.showerror("Senha necessária", "Informe a senha sudo.")
            return
        self.cfg["server_ip"] = ip
        save_config(self.cfg)
        self._run_bg(self._start_bg, ip)

    def _start_bg(self, ip):
        self.after(0, lambda: self.btn_start.configure(state="disabled"))
        self.after(0, lambda: self.btn_stop.configure(state="disabled"))
        self._log(f"▶ Ligando servidor com IP: {ip}")
        b = self._get_backend()
        rc, _ = b.start(ip, self._log)
        if rc == 0:
            self._log(f"✅ Servidor ligado!", "#40e060")
            self._log(f"   DNS1 e DNS2 dos jogadores = {ip}")
            self._log("   (configurar na tela de rede do PCSX2 e dentro do jogo)")
        else:
            self._log(f"❌ Falha ao ligar servidor (código {rc})", "#e04040")
        time.sleep(1)
        self._refresh_status()

    def _stop(self):
        if IS_LINUX and not self.pw_var.get():
            messagebox.showerror("Senha necessária", "Informe a senha sudo.")
            return
        self._run_bg(self._stop_bg)

    def _stop_bg(self):
        self.after(0, lambda: self.btn_stop.configure(state="disabled"))
        self._log("■ Desligando servidor...")
        b = self._get_backend()
        rc, _ = b.stop(self._log)
        self._log("✅ Servidor desligado." if rc == 0 else f"❌ Falha ao desligar (código {rc})",
                  "#e06060" if rc == 0 else "#e04040")
        time.sleep(1)
        self._refresh_status()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = OutbreakServerApp()
    app.geometry("580x700")
    app.mainloop()
