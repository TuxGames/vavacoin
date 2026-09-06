"""URL de arquivo estático com a versão do conteúdo dentro.

## O problema que isto resolve

No plano grátis do PythonAnywhere há **um worker Python**, e o mapeamento de
arquivos estáticos do painel está vazio — então ``base.css``, ``menu.js`` e
companhia passam pelo Flask a cada visita. Sem cabeçalho de cache, o navegador
não guarda nada com confiança e revalida: **cada página abre uma rodada de
requisições condicionais**, e cada uma ocupa o worker único para devolver um
304 sem corpo. Com uma turma inteira clicando, a fila é isso.

O ``base.css`` sozinho tem 44 KB. Mandado de novo (ou revalidado) a cada tela,
em rede de celular, é exatamente o "lag" que se sente.

## Por que a versão vai na URL

Cache longo sem versão é a troca de um problema por outro pior: o arquivo
muda no deploy e o navegador continua com o velho por um ano. Com o hash do
conteúdo na URL, mudar o arquivo **muda o endereço**, e o navegador busca o
novo sem que ninguém precise limpar nada. É o que torna seguro pedir
``max-age`` de um ano.

O hash é do **conteúdo**, não do ``mtime``: um ``git pull`` que reescreve a
data sem mudar o byte não deve invalidar o cache de ninguém.

## O cache do cálculo

Ler e resumir o arquivo a cada renderização seria trocar rede por disco. O
resultado fica em memória, chaveado pelo caminho, e é refeito quando o
``mtime`` **ou o tamanho** mudam — em produção nada disso muda com o processo
no ar; em desenvolvimento, muda a cada edição, que é justamente quando se quer
ver a mudança na hora.

**O limite dessa chave, dito na cara:** duas escritas dentro do mesmo tique do
relógio do sistema, com exatamente o mesmo tamanho em bytes, reusariam o
resumo antigo. No Windows o ``mtime`` tem granularidade grossa o bastante para
isso acontecer num teste que escreve duas vezes seguidas — foi assim que
apareceu. Em deploy não acontece: o ``git pull`` carimba a hora corrente e o
processo reinicia logo depois. Se um dia isso deixar de bastar, a saída é
resumir o conteúdo sempre e aceitar a leitura de disco.
"""

import hashlib
import threading
from pathlib import Path

from flask import url_for

#: Um ano. Só é seguro porque a URL carrega o hash do conteúdo.
UM_ANO = 31_536_000

#: ``{caminho: (mtime, versão)}``. Pequeno e limitado ao punhado de arquivos
#: em ``static/`` — não cresce com o uso.
_memoria = {}
_trava = threading.Lock()


def versao(caminho):
    """Hash curto do conteúdo, ou ``None`` se o arquivo não existe.

    ``None`` em vez de estourar: um arquivo estático faltando é uma tela feia,
    não uma página de erro 500. O ``url_for`` sem versão continua funcionando.
    """
    caminho = Path(caminho)
    try:
        estado = caminho.stat()
    except OSError:
        return None
    marca = (estado.st_mtime_ns, estado.st_size)

    with _trava:
        gravado = _memoria.get(caminho)
        if gravado is not None and gravado[0] == marca:
            return gravado[1]

    resumo = hashlib.sha256(caminho.read_bytes()).hexdigest()[:10]
    with _trava:
        _memoria[caminho] = (marca, resumo)
    return resumo


def registrar(app):
    """Instala o ``estatico()`` do Jinja e o cache longo dos estáticos."""
    app.config.setdefault("SEND_FILE_MAX_AGE_DEFAULT", UM_ANO)
    raiz = Path(app.static_folder)

    def estatico(nome):
        marca = versao(raiz / nome)
        if marca is None:
            return url_for("static", filename=nome)
        return url_for("static", filename=nome, v=marca)

    app.jinja_env.globals["estatico"] = estatico
