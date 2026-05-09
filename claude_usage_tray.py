"""
Claude Usage Tray
=================

Mostra um ícone na bandeja do sistema com a porcentagem de uso da janela
de 5 horas do Claude.ai (e também 7 dias / Opus 7 dias no menu).

Reutiliza o OAuth token que o Claude Code armazena localmente:
  - Windows: Credential Manager (via keyring, service "Claude Code-credentials")
  - macOS:   Keychain  (security find-generic-password -s "Claude Code-credentials")
  - Linux:   ~/.claude/.credentials.json
  - Fallback: variável de ambiente CLAUDE_OAUTH_TOKEN

Endpoint consultado (mesmo que o Claude Code usa internamente):
  GET https://api.anthropic.com/api/oauth/usage
       Authorization: Bearer <token>
       anthropic-beta: oauth-2025-04-20

Dependências:
  pip install requests pillow pystray
  # Windows: + pip install keyring
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont
import pystray

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

USAGE_URL          = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA     = "oauth-2025-04-20"
REFRESH_SECONDS    = 60
ICON_SIZE          = 64               # tamanho do ícone gerado
KEYRING_SERVICE    = "Claude Code-credentials"
KEYRING_USERNAMES  = ("default", "user", "claude", "")  # tentativas comuns

# Limiares (em %) para notificações desktop. Cada limiar dispara uma única
# vez por janela de 5h — o estado é resetado quando o uso cai abaixo do
# limiar OU quando a janela é resetada (resets_at muda).
NOTIFY_THRESHOLDS  = (80, 90, 100)

# Cores em RGBA (para anti-alias bonito sobre fundo escuro/claro da tray)
COLOR_GREEN  = (52, 199, 89, 255)
COLOR_YELLOW = (255, 204, 0, 255)
COLOR_RED    = (255, 69, 58, 255)
COLOR_GRAY   = (142, 142, 147, 255)
COLOR_TEXT   = (255, 255, 255, 255)


# ---------------------------------------------------------------------------
# Token discovery
# ---------------------------------------------------------------------------

def _from_env() -> Optional[str]:
    tok = os.environ.get("CLAUDE_OAUTH_TOKEN")
    return tok.strip() if tok else None


def _from_macos_keychain() -> Optional[str]:
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYRING_SERVICE, "-w"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            raw = out.stdout.strip()
            return _extract_access_token(raw)
    except Exception:
        pass
    return None


def _from_linux_file() -> Optional[str]:
    path = Path.home() / ".claude" / ".credentials.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _extract_access_token(data)
    except Exception:
        return None


def _from_windows_credential_manager() -> Optional[str]:
    try:
        import keyring  # type: ignore
    except ImportError:
        return None
    for username in KEYRING_USERNAMES:
        try:
            raw = keyring.get_password(KEYRING_SERVICE, username) if username else None
            if not raw:
                # alguns backends ignoram username e armazenam só por service
                try:
                    raw = keyring.get_credential(KEYRING_SERVICE, None)
                    raw = raw.password if raw else None
                except Exception:
                    raw = None
            if raw:
                tok = _extract_access_token(raw)
                if tok:
                    return tok
        except Exception:
            continue
    return None


def _extract_access_token(raw) -> Optional[str]:
    """Aceita string JSON, dict ou string crua e devolve o access_token."""
    if raw is None:
        return None
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("{"):
            try:
                raw = json.loads(s)
            except Exception:
                return s  # talvez já seja o token cru
        else:
            return s
    if isinstance(raw, dict):
        # formatos vistos: {claudeAiOauth: {accessToken: ...}} ou {access_token: ...}
        for key in ("accessToken", "access_token"):
            if key in raw and isinstance(raw[key], str):
                return raw[key]
        for sub in raw.values():
            if isinstance(sub, dict):
                for key in ("accessToken", "access_token"):
                    if key in sub and isinstance(sub[key], str):
                        return sub[key]
    return None


def discover_token() -> Optional[str]:
    sysname = platform.system()
    candidates = [_from_env]
    if sysname == "Darwin":
        candidates.append(_from_macos_keychain)
        candidates.append(_from_linux_file)  # fallback do dotfile
    elif sysname == "Windows":
        candidates.append(_from_windows_credential_manager)
        candidates.append(_from_linux_file)  # fallback se houver dotfile
    else:
        candidates.append(_from_linux_file)
        candidates.append(_from_windows_credential_manager)

    for fn in candidates:
        tok = fn()
        if tok:
            return tok
    return None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

class UsageError(Exception):
    pass


def fetch_usage(token: str) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "anthropic-beta": ANTHROPIC_BETA,
        "User-Agent": "claude-usage-tray/1.0",
    }
    try:
        r = requests.get(USAGE_URL, headers=headers, timeout=10)
    except requests.RequestException as e:
        raise UsageError(f"Network error: {e}") from e
    if r.status_code == 401:
        raise UsageError("401 Unauthorized — token inválido/expirado")
    if r.status_code != 200:
        raise UsageError(f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json()
    except ValueError as e:
        raise UsageError(f"Resposta não-JSON: {e}") from e


# ---------------------------------------------------------------------------
# Icon rendering
# ---------------------------------------------------------------------------

def _color_for(pct: Optional[float]):
    if pct is None:
        return COLOR_GRAY
    if pct < 50:
        return COLOR_GREEN
    if pct < 80:
        return COLOR_YELLOW
    return COLOR_RED


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/segoeuib.ttf",   # Segoe UI Bold
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_icon(pct: Optional[float]) -> Image.Image:
    """Desenha um círculo cheio com a % no centro."""
    size = ICON_SIZE
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # círculo de fundo (cor depende da %)
    color = _color_for(pct)
    pad = 2
    d.ellipse((pad, pad, size - pad, size - pad), fill=color)

    # texto: número inteiro
    if pct is None:
        text = "?"
    else:
        text = str(int(round(pct)))

    # fonte: tenta encaixar
    font_size = 34 if len(text) <= 2 else 26
    font = _load_font(font_size)
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    d.text((tx, ty), text, fill=COLOR_TEXT, font=font)
    return img


# ---------------------------------------------------------------------------
# Helpers de formatação
# ---------------------------------------------------------------------------

def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        # Aceita "2026-05-09T18:00:00Z" e variações
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _fmt_eta(ts: Optional[str]) -> str:
    dt = _parse_iso(ts)
    if not dt:
        return "—"
    now = datetime.now(timezone.utc)
    delta = dt - now
    total = int(delta.total_seconds())
    if total <= 0:
        return "agora"
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m}m"


def _fmt_pct(v) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return str(v)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

class App:
    def __init__(self):
        self.token: Optional[str] = discover_token()
        self.last_data: Optional[dict] = None
        self.last_error: Optional[str] = None
        self.last_update: Optional[datetime] = None
        self.icon: Optional[pystray.Icon] = None
        self._stop = threading.Event()
        # estado de notificações: limiares já disparados nesta janela de 5h
        self._notified: set[int] = set()
        self._last_resets_at: Optional[str] = None

    # -- menu callbacks --------------------------------------------------

    def _on_refresh(self, icon=None, item=None):
        threading.Thread(target=self._refresh_once, daemon=True).start()

    def _on_quit(self, icon, item):
        self._stop.set()
        if self.icon:
            self.icon.stop()

    # -- core ------------------------------------------------------------

    def _refresh_once(self):
        if not self.token:
            self.token = discover_token()
        if not self.token:
            self.last_error = "Token não encontrado. Logue no Claude Code ou defina CLAUDE_OAUTH_TOKEN."
            self.last_data = None
            self._update_icon()
            return
        try:
            data = fetch_usage(self.token)
            self.last_data = data
            self.last_error = None
            self._maybe_notify()
        except UsageError as e:
            self.last_error = str(e)
        self.last_update = datetime.now()
        self._update_icon()

    def _maybe_notify(self):
        """Dispara notificações desktop quando o uso cruza os limiares."""
        five = (self.last_data or {}).get("five_hour", {}) or {}
        try:
            pct = float(five.get("utilization"))
        except (TypeError, ValueError):
            return
        resets_at = five.get("resets_at")
        # nova janela de 5h -> zera o estado de notificações
        if resets_at != self._last_resets_at:
            self._notified.clear()
            self._last_resets_at = resets_at
        # também limpa limiares se o uso caiu abaixo deles (caso raro,
        # mas evita ficar travado se a API reportar oscilação)
        for t in list(self._notified):
            if pct < t - 1:  # 1% de histerese
                self._notified.discard(t)
        # dispara cada limiar uma vez por janela
        for t in NOTIFY_THRESHOLDS:
            if pct >= t and t not in self._notified:
                self._notified.add(t)
                self._notify(t, pct, _fmt_eta(resets_at))

    def _notify(self, threshold: int, pct: float, eta: str):
        if not self.icon:
            return
        if threshold >= 100:
            title = "Claude — limite da sessão atingido"
            msg   = f"Você está em {pct:.0f}%. Reset em {eta}."
        else:
            title = f"Claude — {threshold}% da sessão"
            msg   = f"Uso atual: {pct:.0f}%. Reset em {eta}."
        try:
            self.icon.notify(msg, title)
        except Exception:
            pass

    def _session_pct(self) -> Optional[float]:
        if not self.last_data:
            return None
        try:
            return float(self.last_data["five_hour"]["utilization"])
        except (KeyError, TypeError, ValueError):
            return None

    def _update_icon(self):
        if not self.icon:
            return
        pct = self._session_pct()
        self.icon.icon = render_icon(pct)
        self.icon.title = self._tooltip()
        # força o menu a re-renderizar
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _tooltip(self) -> str:
        if self.last_error:
            return f"Claude — erro: {self.last_error[:80]}"
        if not self.last_data:
            return "Claude — carregando…"
        d = self.last_data
        five = d.get("five_hour", {}) or {}
        return (
            f"Sessão (5h): {_fmt_pct(five.get('utilization'))}  "
            f"reset em {_fmt_eta(five.get('resets_at'))}"
        )

    # -- menu ------------------------------------------------------------

    def _build_menu(self) -> pystray.Menu:
        def label_session(_):
            d = (self.last_data or {}).get("five_hour", {}) or {}
            return f"Sessão (5h): {_fmt_pct(d.get('utilization'))} · reset em {_fmt_eta(d.get('resets_at'))}"

        def label_week(_):
            d = (self.last_data or {}).get("seven_day", {}) or {}
            return f"Semana (7d): {_fmt_pct(d.get('utilization'))} · reset em {_fmt_eta(d.get('resets_at'))}"

        def label_opus(_):
            d = (self.last_data or {}).get("seven_day_opus", {}) or {}
            return f"Opus (7d): {_fmt_pct(d.get('utilization'))} · reset em {_fmt_eta(d.get('resets_at'))}"

        def label_status(_):
            if self.last_error:
                return f"⚠ {self.last_error[:60]}"
            if self.last_update:
                return f"Atualizado {self.last_update.strftime('%H:%M:%S')}"
            return "Carregando…"

        return pystray.Menu(
            pystray.MenuItem(label_session, None, enabled=False),
            pystray.MenuItem(label_week,    None, enabled=False),
            pystray.MenuItem(label_opus,    None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(label_status,  None, enabled=False),
            pystray.MenuItem("Atualizar agora", self._on_refresh, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._on_quit),
        )

    # -- runner ----------------------------------------------------------

    def _refresh_loop(self):
        while not self._stop.is_set():
            self._refresh_once()
            self._stop.wait(REFRESH_SECONDS)

    def run(self):
        self.icon = pystray.Icon(
            "claude_usage",
            render_icon(None),
            "Claude — carregando…",
            menu=self._build_menu(),
        )
        # primeira atualização logo após o ícone iniciar
        threading.Thread(target=self._refresh_loop, daemon=True).start()
        self.icon.run()


def main():
    try:
        App().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
