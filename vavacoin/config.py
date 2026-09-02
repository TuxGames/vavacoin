"""Configuração da aplicação."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


#: Valor de fallback do SECRET_KEY. É público — está no repositório —, então
#: a fábrica se recusa a subir com ele fora de teste ou debug.
CHAVE_DE_DESENVOLVIMENTO = "dev-inseguro-trocar-ao-publicar"


class Config:
    SECRET_KEY = os.environ.get("VAVACOIN_SECRET_KEY", CHAVE_DE_DESENVOLVIMENTO)
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "VAVACOIN_DATABASE_URI", f"sqlite:///{RAIZ / 'vavacoin.sqlite3'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    #: Endereço público do site, usado para montar o link de convite fora de
    #: uma requisição (a CLI). Dentro de uma requisição o host verdadeiro é
    #: melhor fonte e este valor nem é consultado. Nunca escreva o domínio de
    #: produção no código: ele muda, e o código não deveria saber onde mora.
    BASE_URL = os.environ.get("VAVACOIN_BASE_URL", "http://localhost:5000")
    WTF_CSRF_ENABLED = True
    #: Custo do bcrypt. Não baixe em produção.
    BCRYPT_ROUNDS = 12

    # Cookie de sessão. `Lax` já corta o CSRF vindo de outro site em POST;
    # o token do Flask-WTF continua sendo a trava principal.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    #: Ligado em produção (ver ConfigProducao): sem HTTPS o cookie não sai.
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_HTTPONLY = True
    #: Ligado só em produção: aí a chave padrão vira erro, não aviso.
    EXIGE_SEGREDO_PROPRIO = False


class ConfigProducao(Config):
    """O que muda ao publicar. O PythonAnywhere serve por HTTPS."""

    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    EXIGE_SEGREDO_PROPRIO = True
    DEBUG = False


class ConfigTeste(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    BCRYPT_ROUNDS = 4
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
