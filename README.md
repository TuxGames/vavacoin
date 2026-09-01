# VaVáCoin — núcleo monetário

Primeira fatia: só o motor de dinheiro e os testes que provam que ele não
vaza. Sem interface, sem ranking, sem cassino. As decisões do projeto estão
no `CLAUDE.md` e não se relitigam aqui.

## Ambiente

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.txt   # Windows
```

## Testes

```bash
.venv/Scripts/python.exe -m pytest
```

Toda operação é cercada pelo helper `conservacao()` (em `testes/conftest.py`):
a soma de **todos** os saldos, incluindo o do Banco Central, é sempre
exatamente 5.000,00.

## Banco e operação

```bash
export FLASK_APP=wsgi.py
flask db upgrade                      # cria as tabelas
flask genese                          # 5.000,00 no Banco Central (idempotente)
flask convite --destinatario "Fulano" # emite um convite; imprime o código
flask criar-conta fulano              # pede a senha sem eco
flask extrato fulano                  # o que entrou e saiu, com saldo em cada linha
flask conservacao                     # confere a massa
flask auditoria                       # massa + o ledger explica cada saldo (sai 1 se não)
flask resetar                         # recolhe de todos e redistribui os 50
```

Estes são os poderes do Banco Central, e é de propósito que só existam aqui:
exercê-los exige acesso ao servidor.

`conservacao` e `auditoria` respondem coisas diferentes, e a diferença importa.
A soma continuar 5.000,00 não prova nada sozinha — quem tira de um e põe no
outro por fora do `mover()` conserva a massa. `auditoria` reconstrói todo saldo
a partir do ledger e acusa a diferença; tem teste exatamente desse caso.

## Onde está o quê

| arquivo | o que guarda |
| --- | --- |
| `vavacoin/dinheiro.py` | tipo monetário: `Decimal` em Python, centavos inteiros no banco |
| `vavacoin/moeda.py` | `mover()` — o caminho único —, a gênese e a verificação de massa |
| `vavacoin/operacoes.py` | resgate do convite, transferência, reset |
| `vavacoin/auditoria.py` | extrato, estado da economia, reconstrução do ledger |
| `vavacoin/autoridade.py` | o Banco Central é o único administrador |
| `vavacoin/modelos.py` | `Usuario`, `Convite`, `Transacao` (ledger) |
| `vavacoin/constantes.py` | supply, saque inicial, capacidade |

## Regras que o código impõe

- Nenhuma função fora de `mover()` escreve em saldo. A exceção é a gênese,
  que só roda com o ledger vazio e sem Banco Central.
- Dinheiro nunca é `float` — `para_decimal()` recusa, não converte.
- Nada de `commit` dentro da biblioteca: quem chama é dono da transação. As
  operações compostas rodam em `SAVEPOINT` para não deixar meio trabalho.
- Senha com hash bcrypt.
- **O Banco Central não tem porta de entrada.** Ele é conta de dinheiro e poder
  administrativo ao mesmo tempo, então quem entrasse nele seria dono de tudo —
  o erro que o Benbals cometeu com contas de tesouraria. Fechado em cinco
  camadas, cada uma com teste: não tem senha (`definir_senha` recusa e o banco
  tem `CHECK`), não é `is_active`, o `user_loader` o descarta, o `get_id()`
  estoura (fecha o `login_user(..., force=True)`), e uma conta que já tem senha
  não pode ser promovida a BC.
- **Poder se pede explicitamente.** Criar conta, emitir convite e resetar exigem
  `autoridade=banco_central()`; não basta conseguir importar a função.
