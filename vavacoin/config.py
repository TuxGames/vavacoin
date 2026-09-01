"""Configuração da aplicação."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


class Config:
    SECRET_KEY = os.environ.get("VAVACOIN_SECRET_KEY", "dev-inseguro-trocar-ao-publicar")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "VAVACOIN_DATABASE_URI", f"sqlite:///{RAIZ / 'vavacoin.sqlite3'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WTF_CSRF_ENABLED = True
    #: Custo do bcrypt. Não baixe em produção.
    BCRYPT_ROUNDS = 12


class ConfigTeste(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    BCRYPT_ROUNDS = 4
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
