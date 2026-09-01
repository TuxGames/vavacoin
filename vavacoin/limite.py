"""Freio de tentativas de login.

Guarda as falhas recentes em memória. É deliberadamente simples e tem dois
limites conhecidos, escritos aqui para ninguém confundir com garantia: some
quando o processo reinicia, e não é compartilhado entre processos. Para uma
turma numa conta grátis do PythonAnywhere, com um worker, isso resolve o que
precisa resolver — impedir que alguém teste senha em rajada.

Se um dia houver mais de um worker, isto vira tabela no banco. O que não pode
é não existir nada.
"""

import threading
import time


class LimitadorDeTentativas:
    """Janela deslizante: N falhas em ``janela`` segundos bloqueiam a chave."""

    def __init__(self, tentativas=5, janela=300):
        self.tentativas = tentativas
        self.janela = janela
        self._falhas = {}
        self._trava = threading.Lock()

    def _limpar(self, chave, agora):
        recentes = [t for t in self._falhas.get(chave, []) if agora - t < self.janela]
        if recentes:
            self._falhas[chave] = recentes
        else:
            self._falhas.pop(chave, None)
        return recentes

    def segundos_de_bloqueio(self, chave):
        """Quanto falta para a chave poder tentar de novo (0 se liberada)."""
        agora = time.monotonic()
        with self._trava:
            recentes = self._limpar(chave, agora)
            if len(recentes) < self.tentativas:
                return 0
            return int(self.janela - (agora - recentes[0])) + 1

    def registrar_falha(self, chave):
        agora = time.monotonic()
        with self._trava:
            self._limpar(chave, agora)
            self._falhas.setdefault(chave, []).append(agora)

    def limpar(self, chave):
        """Chamado no login bem-sucedido: acertou, zera o contador."""
        with self._trava:
            self._falhas.pop(chave, None)


#: Instância usada pela tela de login.
limitador_login = LimitadorDeTentativas()
